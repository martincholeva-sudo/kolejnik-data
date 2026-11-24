import collections
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import ssl
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from national_fixture import DAY
from national.common import ImportErrorDetail
from national.http_transport import PersistentHTTPS
from fetch_priority_sources import allowed

URL='https://portal.cisjr.cz/pub/source.xml.zip'


def message(body=b'data',status=200,extra=''):
    return (f'HTTP/1.1 {status} TEST\r\nContent-Length: {len(body)}\r\n{extra}\r\n').encode()+body


class ScriptedSocket:
    def __init__(self,owner):
        self.owner=owner
        self.closed=False
        self.requests=[]

    def settimeout(self,timeout):
        self.timeout=timeout

    def sendall(self,data):
        self.requests.append(data)

    def makefile(self,*args):
        with self.owner.lock:
            return io.BytesIO(self.owner.responses.popleft())

    def close(self):
        self.closed=True


class ScriptedConnections:
    def __init__(self,*responses):
        self.responses=collections.deque(responses)
        self.created=[]
        self.lock=threading.Lock()

    def __call__(self,host,port,timeout,context):
        owner=self
        class Connection(http.client.HTTPSConnection):
            def connect(self):
                self.sock=ScriptedSocket(owner)
        connection=Connection(host,port,timeout=timeout,context=context)
        with self.lock:self.created.append(connection)
        return connection


class PersistentSourceTests(unittest.TestCase):
    def client(self,*responses):
        factory=ScriptedConnections(*responses)
        client=PersistentHTTPS(allowed,connection_factory=factory,proxies={})
        self.addCleanup(client.close_all)
        return client,factory

    def test_one_hundred_real_http_responses_use_one_connection(self):
        client,factory=self.client(*[message() for _ in range(100)])
        for _ in range(100):
            with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        self.assertEqual(len(factory.created),1)
        self.assertEqual(client.statistics()['connectionsOpened'],1)
        self.assertEqual(client.statistics()['reusedRequests'],99)
        self.assertTrue(client.context.check_hostname)
        self.assertEqual(client.context.verify_mode,ssl.CERT_REQUIRED)

    def test_304_finishes_the_response_and_retains_the_connection(self):
        client,factory=self.client(message(status=304),message())
        request=urllib.request.Request(URL,headers={'If-None-Match':'"TEST"'})
        with self.assertRaises(urllib.error.HTTPError) as caught:client.open(request)
        self.assertEqual(caught.exception.code,304)
        with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        self.assertEqual(len(factory.created),1)

    def test_unread_body_discards_connection_before_another_request(self):
        client,factory=self.client(message(b'large body'),message())
        response=client.open(URL);response.read(1);response.close()
        with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        self.assertEqual(len(factory.created),2)

    def test_server_connection_close_is_respected(self):
        client,factory=self.client(message(extra='Connection: close\r\n'),message())
        with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        self.assertEqual(len(factory.created),2)

    def test_stale_persistent_connection_reopens_once_without_backoff(self):
        client,factory=self.client(message(),b'',message())
        with client.open(URL) as response:response.read()
        with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        self.assertEqual(len(factory.created),2)
        self.assertEqual(client.statistics()['staleConnectionsReopened'],1)

    def test_each_download_thread_has_its_own_connection(self):
        client,factory=self.client(message(),message())
        barrier=threading.Barrier(2)
        def run():
            with client.open(URL) as response:
                barrier.wait(timeout=5)
                return response.read()
        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs=[executor.submit(run) for _ in range(2)]
            self.assertEqual([job.result(timeout=10) for job in jobs],[b'data',b'data'])
        self.assertEqual(len(factory.created),2)

    def test_redirect_to_unapproved_host_is_rejected_before_request(self):
        client,factory=self.client(message(status=302,extra='Location: https://unapproved.example/source\r\n'))
        with self.assertRaises(ImportErrorDetail):client.open(URL)
        self.assertEqual(len(factory.created),1)

    def test_relative_redirect_retains_a_validated_final_url(self):
        client,factory=self.client(message(status=302,extra='Location: /new.xml.zip\r\n'),message())
        with client.open(URL) as response:
            self.assertEqual(response.geturl(),'https://portal.cisjr.cz/new.xml.zip')
            self.assertEqual(response.read(),b'data')
        self.assertEqual(len(factory.created),2)

    def test_truncated_body_discards_connection_and_never_poison_next_file(self):
        client,factory=self.client(b'HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\npart',message())
        with client.open(URL) as response:
            with self.assertRaises(http.client.IncompleteRead):response.read()
        with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        self.assertEqual(len(factory.created),2)

    def test_configured_proxy_uses_existing_proxy_handling(self):
        factory=ScriptedConnections()
        client=PersistentHTTPS(allowed,connection_factory=factory,proxies={'https':'http://TEST-proxy.invalid'})
        self.addCleanup(client.close_all)
        class Response(io.BytesIO):
            def geturl(self):return URL
        with patch('urllib.request.proxy_bypass',return_value=False),patch('urllib.request.urlopen',return_value=Response(b'proxy')) as opener:
            with client.open(URL) as response:self.assertEqual(response.read(),b'proxy')
        opener.assert_called_once()
        self.assertEqual(factory.created,[])

    def test_hosts_do_not_share_connections(self):
        client,factory=self.client(message(),message())
        for url in [URL,'https://data.pid.cz/PID_GTFS.zip']:
            with client.open(url) as response:response.read()
        self.assertEqual(len(factory.created),2)
        self.assertNotEqual(factory.created[0].host,factory.created[1].host)

    def test_invalid_url_or_post_cannot_start_a_connection(self):
        client,factory=self.client()
        for url in ['http://portal.cisjr.cz/data','https://unapproved.example/data','https://user@portal.cisjr.cz/data']:
            with self.assertRaises(ImportErrorDetail):client.open(url)
        with self.assertRaises(ValueError):client.open(urllib.request.Request(URL,data=b'POST'))
        self.assertEqual(factory.created,[])

    def test_close_all_releases_connections_and_allows_a_new_collection(self):
        client,factory=self.client(message(),message())
        with client.open(URL) as response:response.read()
        client.close_all()
        self.assertEqual(client.connections,set())
        with client.open(URL) as response:response.read()
        self.assertEqual(len(factory.created),2)



    def test_chunked_response_is_fully_consumed_before_reuse(self):
        raw=b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\ndata\r\n0\r\n\r\n'
        client,factory=self.client(raw,message())
        with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        with client.open(URL) as response:self.assertEqual(response.read(),b'data')
        self.assertEqual(len(factory.created),1)

    def test_tls_certificate_failure_remains_a_verification_error(self):
        created=[]
        def factory(host,port,timeout,context):
            connection=http.client.HTTPSConnection(host,port,timeout=timeout,context=context)
            def fail():raise ssl.SSLCertVerificationError('TEST invalid certificate')
            connection.connect=fail;created.append(connection);return connection
        client=PersistentHTTPS(allowed,connection_factory=factory,proxies={})
        self.addCleanup(client.close_all)
        with self.assertRaises(urllib.error.URLError) as caught:client.open(URL)
        self.assertIsInstance(caught.exception.reason,ssl.SSLCertVerificationError)
        self.assertEqual(len(created),1)


if __name__=='__main__':
    unittest.main()
