"""Persistent verified HTTPS for the fixed public timetable hosts."""
import http.client
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request


class SourceResponse:
    def __init__(self, owner, connection, response, url):
        self.owner=owner
        self.connection=connection
        self.response=response
        self.url=url
        self.headers=response.headers
        self.status=response.status
        self.reason=response.reason
        self.closed=False

    def geturl(self):
        return self.url

    def read(self, size=None):
        try:
            return self.response.read(size)
        except (OSError, http.client.HTTPException):
            self.owner.discard(self.connection)
            raise

    def close(self):
        if self.closed:
            return
        self.closed=True
        complete=self.response.isclosed() and self.response.length in (0, None)
        if not complete or self.response.will_close:
            self.owner.discard(self.connection)
        self.response.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class PersistentHTTPS:
    def __init__(self, validate, connection_factory=None, proxies=None):
        self.validate=validate
        self.factory=connection_factory or http.client.HTTPSConnection
        self.proxies=urllib.request.getproxies() if proxies is None else proxies
        self.local=threading.local()
        self.lock=threading.Lock()
        self.connections=set()
        self.context=None
        self.counters={'directRequests':0,'proxyRequests':0,'connectionsOpened':0,
                       'reusedRequests':0,'staleConnectionsReopened':0}

    def statistics(self):
        with self.lock:
            return dict(self.counters)

    def count(self, name):
        with self.lock:
            self.counters[name]+=1

    def mapping(self):
        if not hasattr(self.local,'connections'):
            self.local.connections={}
        return self.local.connections

    def connection(self, parsed, timeout):
        key=(parsed.hostname,parsed.port or 443)
        mapping=self.mapping()
        if key not in mapping:
            with self.lock:
                if self.context is None:
                    self.context=ssl.create_default_context()
                    self.context.set_alpn_protocols(['http/1.1'])
            connection=self.factory(key[0],key[1],timeout=timeout,context=self.context)
            mapping[key]=connection
            with self.lock:
                self.connections.add(connection)
        return mapping[key]

    def discard(self, connection):
        for key,value in list(self.mapping().items()):
            if value is connection:
                self.mapping().pop(key)
        with self.lock:
            self.connections.discard(connection)
        connection.close()

    def close_all(self):
        with self.lock:
            connections=list(self.connections)
            self.connections.clear()
        for connection in connections:
            connection.close()
        self.mapping().clear()

    def request_once(self, request, timeout):
        parsed=urllib.parse.urlsplit(request.full_url)
        path=parsed.path or '/'
        headers={key:value for key,value in request.header_items()
                 if key.lower() not in {'connection','host'}}
        for attempt in range(2):
            connection=self.connection(parsed,timeout)
            reused=connection.sock is not None
            try:
                connection.timeout=timeout
                if connection.sock is None:
                    connection.connect()
                    self.count('connectionsOpened')
                else:
                    connection.sock.settimeout(timeout)
                self.count('directRequests')
                connection.request('GET',path,headers=headers)
                response=connection.getresponse()
                if reused:
                    self.count('reusedRequests')
                return SourceResponse(self,connection,response,request.full_url)
            except (OSError,http.client.HTTPException) as error:
                self.discard(connection)
                stale=isinstance(error,(http.client.RemoteDisconnected,BrokenPipeError,
                                        ConnectionResetError,http.client.CannotSendRequest,
                                        http.client.ResponseNotReady))
                if reused and stale and attempt==0:
                    self.count('staleConnectionsReopened')
                    continue
                raise urllib.error.URLError(error) from error

    def open(self, value, timeout=60):
        request=value if isinstance(value,urllib.request.Request) else urllib.request.Request(value)
        self.validate(request.full_url)
        if request.get_method()!='GET' or request.data is not None:
            raise ValueError('Timetable source requests must be GET without a body')
        parsed=urllib.parse.urlsplit(request.full_url)
        if self.proxies.get('https') and not urllib.request.proxy_bypass(parsed.hostname):
            self.count('proxyRequests')
            response=urllib.request.urlopen(request,timeout=timeout)
            try:
                self.validate(response.geturl())
            except Exception:
                response.close()
                raise
            return response
        for _ in range(6):
            response=self.request_once(request,timeout)
            if response.status in {301,302,303,307,308}:
                location=response.headers.get('Location')
                response.close()
                if not location:
                    raise urllib.error.HTTPError(request.full_url,response.status,'Redirect without Location',response.headers,None)
                destination=urllib.parse.urljoin(request.full_url,location)
                self.validate(destination)
                request=urllib.request.Request(destination,headers=dict(request.header_items()))
                continue
            if response.status==304:
                # A 304 body is empty; finish it so the same connection can be reused.
                response.read()
                response.close()
                raise urllib.error.HTTPError(request.full_url,304,response.reason,response.headers,None)
            if not 200<=response.status<300:
                response.close()
                raise urllib.error.HTTPError(request.full_url,response.status,response.reason,response.headers,None)
            return response
        raise urllib.error.HTTPError(request.full_url,310,'Too many source redirects',{},None)
