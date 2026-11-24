"""Download official inputs with bounded retries and resumable checked files."""
import argparse
import contextlib
import datetime as dt
import email.utils
import errno
import html.parser
import http.client
import json
import os
import random
import shutil
import socket
import ssl
import time
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from national.common import ImportErrorDetail, sha_file
from national.http_transport import PersistentHTTPS

ALLOWED_HOSTS = {'portal.cisjr.cz', 'data.pid.cz'}
DOWNLOAD_VERSION = '2026-10-26.6'
MAX_ATTEMPTS = 5
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class SourceTransferError(ImportErrorDetail):
    def __init__(self, url, operation, attempts, cause):
        self.url = url
        self.operation = operation
        self.attempts = attempts
        super().__init__(f'{operation} failed for {url} after {attempts} attempt(s): {type(cause).__name__}: {cause}')


class TruncatedDownload(ConnectionError):
    pass


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            self.links.extend(value for name, value in attrs if name.lower() == 'href' and value)


def allowed(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ImportErrorDetail('Only the fixed official HTTPS data hosts are accepted')
    if '..' in urllib.parse.unquote(parsed.path).split('/'):
        raise ImportErrorDetail('Unsafe source URL')
    return url


HTTP_CLIENT = PersistentHTTPS(allowed)
http_open = HTTP_CLIENT.open
HTTP_MODE = 'persistent'


def retryable(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRY_STATUSES
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        return not isinstance(reason, ssl.SSLError) and (
            isinstance(reason, (TimeoutError, ConnectionError, socket.gaierror)) or
            getattr(reason, 'winerror', None) in {10051, 10054, 10060, 10061, 10065} or
            getattr(reason, 'errno', None) in {errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH})
    return isinstance(error, (TimeoutError, ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected))


def retry_delay(error, attempt):
    delay = min(30, 2 ** attempt) + random.uniform(0, 0.5)
    if isinstance(error, urllib.error.HTTPError):
        value = error.headers.get('Retry-After', '') if error.headers else ''
        if value:
            try:
                requested = float(value)
            except ValueError:
                try:
                    when = email.utils.parsedate_to_datetime(value)
                    requested = (when - dt.datetime.now(dt.timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    requested = 0
            # A longer server cooldown stops this source; do not retry earlier.
            if requested > 60:
                return None
            delay = max(delay, requested)
    return max(0, delay)


def transfer(url, operation, action):
    allowed(url)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return action()
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected) as error:
            delay = retry_delay(error, attempt) if retryable(error) and attempt < MAX_ATTEMPTS else None
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            if delay is None:
                raise SourceTransferError(url, operation, attempt, error) from error
            print(f'Pripojeni preruseno; opakuji {attempt + 1}/{MAX_ATTEMPTS} za {delay:.1f} s: {url}', flush=True)
            time.sleep(delay)


def listing(url):
    def once():
        with http_open(url, timeout=60) as response:
            allowed(response.geturl())
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ImportErrorDetail('Oversized source listing: ' + url)
        parser = Links()
        parser.feed(raw.decode('utf-8-sig'))
        return sorted({urllib.parse.urljoin(url, value) for value in parser.links})
    return transfer(url, 'source listing', once)


def sample_plan(year, through):
    """Only list the selected update month for a bounded, non-complete audit."""
    day = dt.date.fromisoformat(through)
    if not 2000 <= year <= 2100:
        raise ImportErrorDetail('Invalid timetable year')
    base = f'https://portal.cisjr.cz/pub/draha/celostatni/szdc/{year}/'
    links = listing(base)
    folder = base + day.isoformat()[:7] + '/'
    if folder not in {link.rstrip('/') + '/' for link in links}:
        raise ImportErrorDetail('The selected rail update month is absent from the official listing')
    recent = [allowed(link) for link in listing(folder)
              if link.startswith(folder) and link.lower().endswith(('.zip', '.xml.gz', '.xml'))]
    return {'rail': sorted(set(recent)), 'ostrava': [
        'https://portal.cisjr.cz/pub/netex/NeTEx_DrahyMestske.zip',
        'https://portal.cisjr.cz/pub/netex/NeTEx_VerejnaLinkovaDoprava.zip']}


def plan(year, through):
    dt.date.fromisoformat(through)
    if not 2000 <= year <= 2100:
        raise ImportErrorDetail('Invalid timetable year')
    base = f'https://portal.cisjr.cz/pub/draha/celostatni/szdc/{year}/'
    links = listing(base)
    main = base + f'JR{year}.zip'
    if main not in links:
        raise ImportErrorDetail('The base rail archive is absent from the official listing')
    rail = [main]
    months = set()
    for link in links:
        if not link.startswith(base):
            continue
        path = urllib.parse.urlsplit(link).path.rstrip('/')
        month = path.rsplit('/', 1)[-1]
        if len(month) == 7 and month[:4].isdigit() and month[4] == '-' and month[5:].isdigit() and month <= through[:7]:
            allowed(link)
            months.add(link.rstrip('/') + '/')
    if not months:
        raise ImportErrorDetail('No rail update directories discovered; source completeness is unverified')
    # Newest updates first, without omitting any older listed updates.
    for folder in sorted(months, reverse=True):
        print('Ctu katalog aktualizaci: ' + folder, flush=True)
        for link in listing(folder):
            if not link.startswith(folder):
                continue
            if link.lower().endswith(('.zip', '.xml.gz', '.xml')):
                rail.append(allowed(link))
            elif link.endswith('/'):
                raise ImportErrorDetail('Nested update directory needs an explicit supported source plan')
    return {'rail': list(dict.fromkeys(rail)), 'ostrava': [
        'https://portal.cisjr.cz/pub/netex/NeTEx_DrahyMestske.zip',
        'https://portal.cisjr.cz/pub/netex/NeTEx_VerejnaLinkovaDoprava.zip'],
        'pid': ['https://data.pid.cz/PID_GTFS.zip']}


def cache_paths(url, directory):
    allowed(url)
    from national.common import digest
    name = digest(url)[:16] + '-' + urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1])
    target = Path(directory) / name
    return target, target.with_suffix(target.suffix + '.receipt.json')


def replace_file(source, destination):
    """Retry Windows sharing/access failures without deleting the old target."""
    for attempt in range(10):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if getattr(error, 'winerror', None) not in {5, 32, 33, 1224} or attempt == 9:
                raise
            time.sleep(min(1, 0.05 * (2 ** attempt)))


def write_json(path, value):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         prefix=path.name + '.', suffix='.tmp',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
        replace_file(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def persist_status(path, status, final=False):
    """A busy progress report must not discard successful source transfers."""
    status['updatedAt'] = dt.datetime.now(dt.timezone.utc).isoformat()
    status['httpTransport'] = HTTP_CLIENT.statistics()
    status['httpMode'] = HTTP_MODE
    try:
        write_json(path, status)
    except OSError as error:
        status['statusWriteWarning'] = type(error).__name__ + ': ' + str(error)
        status['statusWriteWarningCount'] = status.get('statusWriteWarningCount', 0) + 1
        print('Prubezna zprava je docasne nedostupna; podklady stahuji dal.', flush=True)
    if final:
        snapshot = Path(path).with_name('summary-' + status['runId'] + '.json')
        write_json(snapshot, status)
        print('Zaverecna zprava: ' + str(snapshot), flush=True)


@contextlib.contextmanager
def collection_lock(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.kolejnik-source.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            lock = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        try:
            lock()
        except OSError as error:
            raise ImportErrorDetail('V teto slozce uz bezi jine stahovani. Pockej na jeho dokonceni nebo zavri jeho okno.') from error
        try:
            yield
        finally:
            stream.seek(0)
            unlock()


def checked_receipt(target, receipt, url):
    if not target.is_file() or not receipt.is_file():
        return {}
    try:
        prior = json.loads(receipt.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError):
        return {}
    if not isinstance(prior, dict) or prior.get('url') != url or prior.get('sha256') != sha_file(target):
        return {}
    return prior


def reuse_cached(url, original_directory, directory):
    if original_directory is None:
        return False
    if isinstance(original_directory, (list, tuple)):
        return any(reuse_cached(url, item, directory) for item in original_directory)
    source, old_receipt = cache_paths(url, original_directory)
    target, receipt = cache_paths(url, directory)
    if target.exists():
        return False
    prior = checked_receipt(source, old_receipt, url)
    if not prior:
        return False
    temporary = target.with_suffix(target.suffix + '.copy-part')
    try:
        shutil.copyfile(source, temporary)
        replace_file(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    prior['path'] = str(target.resolve())
    write_json(receipt, prior)
    return True


def download(url, directory):
    target, receipt = cache_paths(url, directory)
    prior = checked_receipt(target, receipt, url)
    headers = {'User-Agent': 'Kolejnik-data-preparation/1.1', 'Accept-Encoding': 'identity'}
    if prior.get('etag'):
        headers['If-None-Match'] = prior['etag']
    if prior.get('lastModified'):
        headers['If-Modified-Since'] = prior['lastModified']

    def once():
        try:
            response = http_open(urllib.request.Request(url, headers=headers), timeout=60)
        except urllib.error.HTTPError as error:
            if error.code != 304 or not prior or not (prior.get('etag') or prior.get('lastModified')):
                raise
            error.close()
            result = {**prior, 'path': str(target.resolve()), 'verifiedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'httpStatus': 304}
            write_json(receipt, result)
            return result
        temporary = target.with_suffix(target.suffix + '.part')
        try:
            with response, temporary.open('wb') as stream:
                allowed(response.geturl())
                declared = response.headers.get('Content-Length')
                expected = int(declared) if declared and declared.isdigit() else None
                if expected is not None and expected > 3_000_000_000:
                    raise ImportErrorDetail('Archive exceeds the download size limit: ' + url)
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 3_000_000_000:
                        raise ImportErrorDetail('Archive exceeds the download size limit: ' + url)
                    stream.write(chunk)
                if expected is not None and total != expected:
                    raise TruncatedDownload(f'Received {total} of {expected} bytes')
                result = {'url': url, 'path': str(target.resolve()), 'bytes': total, 'httpStatus': 200,
                          'etag': response.headers.get('ETag'), 'lastModified': response.headers.get('Last-Modified'),
                          'verifiedAt': dt.datetime.now(dt.timezone.utc).isoformat()}
            result['sha256'] = sha_file(temporary)
            replace_file(temporary, target)
            write_json(receipt, result)
            return result
        finally:
            temporary.unlink(missing_ok=True)
    return transfer(url, 'source download', once)


def source_jobs(sources):
    # Acquire the base and the three large companion archives before history.
    yield 'rail', 0, sources['rail'][0]
    for kind in ('ostrava', 'pid'):
        for index, url in enumerate(sources[kind]):
            yield kind, index, url
    for index, url in enumerate(sources['rail'][1:], 1):
        yield 'rail', index, url


def _fetch_all(destination, year, through, workers=4, reuse_cache=None):
    if not isinstance(workers, int) or isinstance(workers, bool) or not 1 <= workers <= 8:
        raise ImportErrorDetail('Use 1 to 8 download workers')
    dt.date.fromisoformat(through)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    status = {'schema': 2, 'downloadVersion': DOWNLOAD_VERSION, 'runId': uuid.uuid4().hex,
              'sourcePlanComplete': False, 'inProgress': True, 'phase': 'planning',
              'timetableYear': year, 'through': through, 'workers': workers,
              'railCoverageValidated': False, 'ostravaCoverageValidated': False,
              'apkChanged': False, 'deployed': False, 'maxAttemptsPerRequest': MAX_ATTEMPTS,
              'completedCounts': {'rail': 0, 'ostrava': 0, 'pid': 0}, 'failedSources': [],
              'httpValidatedCachedFiles': 0, 'httpDownloadedFiles': 0}
    summary = destination / 'summary.json'
    persist_status(summary, status)
    try:
        print('Zjistuji kmenovy vlakovy archiv a vsechny dostupne aktualizace...', flush=True)
        sources = plan(year, through)
        if set(sources) != {'rail', 'ostrava', 'pid'} or any(not urls for urls in sources.values()):
            raise ImportErrorDetail('Incomplete required source plan')
        print('Zdrojovy plan: ' + ', '.join(kind + '=' + str(len(urls)) for kind, urls in sources.items()), flush=True)
        result = {'schema': 1, 'timetableYear': year, 'through': through,
                  'sourcePlanComplete': True, 'synthetic': False,
                  'sources': {kind: [None] * len(urls) for kind, urls in sources.items()}}
        completed = status['completedCounts']
        status.update({'phase': 'downloading', 'plannedCounts': {kind: len(urls) for kind, urls in sources.items()}})
        persist_status(summary, status)
        reused = 0
        consecutive_failures = 0
        failures = []
        jobs = iter(source_jobs(sources))

        def acquire(url):
            copied = reuse_cached(url, reuse_cache, destination)
            # Cached files remain subject to HTTP revalidation, never assumed immutable.
            return download(url, destination), copied

        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending = {}

            def enqueue():
                job = next(jobs, None)
                if job is None:
                    return False
                kind, index, url = job
                pending[executor.submit(acquire, url)] = (kind, index, url)
                return True

            for _ in range(workers * 2):
                if not enqueue():
                    break
            while pending:
                done, _ = wait(pending, timeout=15, return_when=FIRST_COMPLETED)
                if not done:
                    print('Stahovani pokracuje; cekam na odpoved zdroje...', flush=True)
                    continue
                for future in done:
                    kind, index, url = pending.pop(future)
                    try:
                        receipt, copied = future.result()
                    except Exception as error:
                        failure = {'kind': kind, 'url': url, 'error': type(error).__name__ + ': ' + str(error)}
                        failures.append(failure)
                        status['failedSources'] = failures[:100]
                        consecutive_failures += 1
                        print('Soubor zatim chybi, pokracuji dalsimi: ' + url + ' (' + str(error) + ')', flush=True)
                        persist_status(summary, status)
                    else:
                        result['sources'][kind][index] = receipt
                        reused += int(copied)
                        completed[kind] += 1
                        if receipt.get('httpStatus') == 304:
                            status['httpValidatedCachedFiles'] += 1
                        elif receipt.get('httpStatus') == 200:
                            status['httpDownloadedFiles'] += 1
                        consecutive_failures = 0
                        if completed[kind] == 1 or completed[kind] % 25 == 0 or completed[kind] == len(sources[kind]):
                            print(kind + ': ' + str(completed[kind]) + '/' + str(len(sources[kind])), flush=True)
                            if completed[kind] % 250 == 0:
                                stats = HTTP_CLIENT.statistics()
                                print('HTTPS: spojeni ' + str(stats['connectionsOpened']) + ', znovu pouzita spojeni ' + str(stats['reusedRequests']) + '; ulozene soubory overene bez stahovani ' + str(status['httpValidatedCachedFiles']), flush=True)
                            status['reusedAndHttpRevalidated'] = reused
                            persist_status(summary, status)
                    if consecutive_failures >= 12:
                        for queued in pending:
                            queued.cancel()
                        raise ImportErrorDetail('Repeated source outage; collection paused. Cached files are retained. Last source: ' + url)
                    enqueue()
        if failures:
            raise ImportErrorDetail(str(len(failures)) + ' source file(s) could not be acquired. First: ' + failures[0]['url'] + ': ' + failures[0]['error'])
        if any(entry is None for entries in result['sources'].values() for entry in entries):
            raise ImportErrorDetail('The source collection did not finish')
        write_json(destination / 'inputs.json', result)
        status.update({'sourcePlanComplete': True, 'inProgress': False, 'phase': 'complete',
                       'sourceCounts': completed, 'reusedAndHttpRevalidated': reused,
                       'sourceManifest': str(destination / 'inputs.json')})
        persist_status(summary, status, final=True)
        return destination / 'inputs.json'
    except (Exception, KeyboardInterrupt) as error:
        status.update({'sourcePlanComplete': False, 'inProgress': False, 'phase': 'incomplete',
                       'error': type(error).__name__ + ': ' + str(error)})
        if isinstance(error, SourceTransferError):
            status['failedSources'] = [{'url': error.url, 'operation': error.operation, 'attempts': error.attempts, 'error': str(error)}]
        persist_status(summary, status, final=True)
        raise


def fetch_all(destination, year, through, workers=4, reuse_cache=None):
    with collection_lock(destination):
        try:
            return _fetch_all(destination, year, through, workers, reuse_cache)
        finally:
            HTTP_CLIENT.close_all()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='runtime/priority-sources')
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--through', required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--reuse-cache', action='append')
    parser.add_argument('--http-mode', choices=('persistent','legacy'), default='persistent')
    args = parser.parse_args()
    dt.date.fromisoformat(args.through)
    HTTP_MODE = args.http_mode
    if args.http_mode == 'legacy':
        http_open = urllib.request.urlopen
    else:
        print('Pouzivam opakovane HTTPS spojeni pro male vlakove soubory.', flush=True)
    try:
        print(fetch_all(args.out, args.year, args.through, args.workers, args.reuse_cache))
    except (Exception, KeyboardInterrupt) as error:
        print('Stahovani neni uplne: ' + str(error), flush=True)
        raise SystemExit(130 if isinstance(error, KeyboardInterrupt) else 1)
