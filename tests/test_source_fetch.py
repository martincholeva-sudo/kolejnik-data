import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
from national_fixture import DAY
import fetch_priority_sources as fetch
from national.common import ImportErrorDetail, sha_file

URL='https://portal.cisjr.cz/pub/draha/celostatni/szdc/2026/2026-10/first.xml.zip'

def plan():
    return {'rail':[URL,URL.replace('first','second')],
            'ostrava':['https://portal.cisjr.cz/pub/netex/NeTEx_DrahyMestske.zip','https://portal.cisjr.cz/pub/netex/NeTEx_VerejnaLinkovaDoprava.zip'],
            'pid':['https://data.pid.cz/PID_GTFS.zip']}

def fake_download(url,directory):
    target,receipt=fetch.cache_paths(url,directory);target.write_bytes(url.encode())
    return {'url':url,'path':str(target),'sha256':sha_file(target),'bytes':target.stat().st_size}

class SourceFetchTests(unittest.TestCase):
    def test_parallel_completion_preserves_the_exact_source_plan_order(self):
        event=threading.Event();lock=threading.Lock();running=0;peak=0
        def download(url,directory):
            nonlocal running,peak
            with lock:running+=1;peak=max(peak,running)
            try:
                if url==URL:
                    if not event.wait(5):raise RuntimeError('Second source did not run concurrently')
                result=fake_download(url,directory)
                if url.endswith('second.xml.zip'):event.set()
                return result
            finally:
                with lock:running-=1
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch,'plan',return_value=plan()),patch.object(fetch,'download',side_effect=download),contextlib.redirect_stdout(io.StringIO()):
            manifest=fetch.fetch_all(temp,2026,DAY,workers=2)
            data=json.loads(manifest.read_text(encoding='utf-8'))
            for kind,urls in plan().items():self.assertEqual([x['url'] for x in data['sources'][kind]],urls)
            summary=json.loads((Path(temp)/'summary.json').read_text(encoding='utf-8'))
            self.assertTrue(summary['sourcePlanComplete']);self.assertFalse(summary['railCoverageValidated'])
            self.assertFalse(summary['apkChanged']);self.assertEqual(peak,2)

    def test_one_failed_download_cannot_replace_a_previous_complete_manifest(self):
        def download(url,directory):
            if url.endswith('second.xml.zip'):raise OSError('TEST broken source')
            return fake_download(url,directory)
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch,'plan',return_value=plan()),patch.object(fetch,'download',side_effect=download),contextlib.redirect_stdout(io.StringIO()):
            path=Path(temp)/'inputs.json';path.write_text('{"old":true}',encoding='utf-8')
            with self.assertRaisesRegex(ImportErrorDetail,'TEST'):fetch.fetch_all(temp,2026,DAY,workers=2)
            self.assertEqual(path.read_text(encoding='utf-8'),'{"old":true}')
            summary=json.loads((Path(temp)/'summary.json').read_text(encoding='utf-8'))
            self.assertFalse(summary['sourcePlanComplete']);self.assertFalse(summary['inProgress'])
            self.assertIn('TEST',summary['error'])
            self.assertEqual(sum(summary['completedCounts'].values()),4)
            self.assertEqual(summary['failedSources'][0]['url'],URL.replace('first','second'))

    def test_reused_mutable_file_is_still_conditionally_revalidated_over_http(self):
        with tempfile.TemporaryDirectory() as temp:
            old=Path(temp)/'old';old.mkdir();new=Path(temp)/'new';new.mkdir()
            source,receipt=fetch.cache_paths(URL,old);source.write_bytes(b'original source')
            receipt.write_text(json.dumps({'url':URL,'path':str(source),'sha256':sha_file(source),'etag':'"TEST-etag"'}),encoding='utf-8')
            self.assertTrue(fetch.reuse_cached(URL,old,new))
            requests=[]
            def response(request,timeout):
                requests.append(request)
                raise urllib.error.HTTPError(URL,304,'Not Modified',{},None)
            with patch.object(fetch,'http_open',side_effect=response):result=fetch.download(URL,new)
            headers={key.lower():value for key,value in requests[0].header_items()}
            self.assertEqual(headers['if-none-match'],'"TEST-etag"')
            self.assertEqual(Path(result['path']).read_bytes(),b'original source')
            self.assertEqual(source.read_bytes(),b'original source')

    def test_tampered_reuse_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            old=Path(temp)/'old';old.mkdir();new=Path(temp)/'new';new.mkdir()
            source,receipt=fetch.cache_paths(URL,old);source.write_bytes(b'original source')
            receipt.write_text(json.dumps({'url':URL,'path':str(source),'sha256':sha_file(source)}),encoding='utf-8')
            source.write_bytes(b'tampered')
            self.assertFalse(fetch.reuse_cached(URL,old,new))
            self.assertFalse(fetch.cache_paths(URL,new)[0].exists())

    def test_incomplete_plan_or_invalid_concurrency_never_starts_downloads(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch,'plan',return_value={'rail':[],'ostrava':[],'pid':[]}),patch.object(fetch,'download') as downloader,contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ImportErrorDetail,'Incomplete'):fetch.fetch_all(temp,2026,DAY)
            with self.assertRaisesRegex(ImportErrorDetail,'1 to 8'):fetch.fetch_all(temp,2026,DAY,workers=0)
            downloader.assert_not_called()


class HttpRecoveryTests(unittest.TestCase):
    class Response(io.BytesIO):
        def __init__(self, body=b'checked bytes', headers=None):
            super().__init__(body)
            self.headers = headers or {'Content-Length': str(len(body))}
        def geturl(self):
            return URL

    def test_timeout_then_success_retries_the_same_url(self):
        requests = []
        def response(request, timeout):
            requests.append(request.full_url)
            if len(requests) == 1:
                raise urllib.error.URLError(TimeoutError('TEST timeout'))
            return self.Response()
        with tempfile.TemporaryDirectory() as temp, patch.object(fetch,'http_open',side_effect=response), patch.object(fetch.time,'sleep') as pause, contextlib.redirect_stdout(io.StringIO()):
            result=fetch.download(URL,Path(temp))
            self.assertEqual(requests,[URL,URL])
            self.assertEqual(Path(result['path']).read_bytes(),b'checked bytes')
            self.assertEqual(pause.call_count,1)

    def test_truncated_transfer_is_not_installed_and_can_retry(self):
        responses=[self.Response(b'part',{'Content-Length':'13'}),self.Response()]
        with tempfile.TemporaryDirectory() as temp, patch.object(fetch,'http_open',side_effect=responses), patch.object(fetch.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            result=fetch.download(URL,Path(temp))
            self.assertEqual(result['bytes'],13)
            self.assertEqual(Path(result['path']).read_bytes(),b'checked bytes')
            self.assertFalse(list(Path(temp).glob('*.part')))

    def test_repeated_timeouts_preserve_previous_checked_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target,receipt=fetch.cache_paths(URL,temp)
            target.write_bytes(b'old checked source')
            old={'url':URL,'path':str(target),'sha256':sha_file(target),'etag':'"old"'}
            receipt.write_text(json.dumps(old),encoding='utf-8')
            with patch.object(fetch,'http_open',side_effect=urllib.error.URLError(TimeoutError('TEST timeout'))) as request, patch.object(fetch.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(fetch.SourceTransferError) as caught:fetch.download(URL,Path(temp))
            self.assertEqual(request.call_count,5)
            self.assertEqual(caught.exception.url,URL)
            self.assertEqual(target.read_bytes(),b'old checked source')
            self.assertEqual(json.loads(receipt.read_text(encoding='utf-8')),old)

    def test_404_is_reported_with_url_without_retry(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch,'http_open',side_effect=urllib.error.HTTPError(URL,404,'Missing',{},None)) as request,patch.object(fetch.time,'sleep') as pause:
            with self.assertRaises(fetch.SourceTransferError) as caught:fetch.download(URL,Path(temp))
            self.assertIn(URL,str(caught.exception));self.assertEqual(request.call_count,1);pause.assert_not_called()

    def test_503_respects_retry_after(self):
        responses=[urllib.error.HTTPError(URL,503,'Busy',{'Retry-After':'7'},None),self.Response()]
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch,'http_open',side_effect=responses),patch.object(fetch.time,'sleep') as pause,contextlib.redirect_stdout(io.StringIO()):
            fetch.download(URL,Path(temp))
            self.assertGreaterEqual(pause.call_args.args[0],7)

    def test_long_server_cooldown_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch,'http_open',side_effect=urllib.error.HTTPError(URL,429,'Busy',{'Retry-After':'120'},None)) as request,patch.object(fetch.time,'sleep') as pause:
            with self.assertRaises(fetch.SourceTransferError):fetch.download(URL,Path(temp))
            self.assertEqual(request.call_count,1);pause.assert_not_called()

    def test_listing_timeout_is_retried_and_parsed(self):
        responses=[urllib.error.URLError(TimeoutError('TEST')),self.Response(b'<a href="next.xml.zip">Next</a>')]
        with patch.object(fetch,'http_open',side_effect=responses),patch.object(fetch.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fetch.listing(URL),[URL.replace('first','next')])

    def test_all_months_remain_in_plan_with_base_and_newest_first(self):
        base='https://portal.cisjr.cz/pub/draha/celostatni/szdc/2026/'
        listings={base:[base+'JR2026.zip',base+'2025-11/',base+'2026-10/'],
                  base+'2025-11/':[base+'2025-11/old.xml.zip'],
                  base+'2026-10/':[base+'2026-10/current.xml.zip']}
        with patch.object(fetch,'listing',side_effect=lambda url:listings[url]),contextlib.redirect_stdout(io.StringIO()):
            result=fetch.plan(2026,'2026-10-26')
        self.assertEqual(result['rail'],[base+'JR2026.zip',base+'2026-10/current.xml.zip',base+'2025-11/old.xml.zip'])
        jobs=list(fetch.source_jobs(result))
        self.assertEqual([kind for kind,index,url in jobs[:4]],['rail','ostrava','ostrava','pid'])
        self.assertEqual(set(url for kind,index,url in jobs),set(url for values in result.values() for url in values))

    def test_receipt_from_wrong_url_is_not_reused_for_304(self):
        with tempfile.TemporaryDirectory() as temp:
            target,receipt=fetch.cache_paths(URL,temp);target.write_bytes(b'old')
            receipt.write_text(json.dumps({'url':URL.replace('first','other'),'sha256':sha_file(target),'etag':'"wrong"'}),encoding='utf-8')
            requests=[]
            def response(request,timeout):
                requests.append(request);return self.Response()
            with patch.object(fetch,'http_open',side_effect=response):fetch.download(URL,temp)
            self.assertNotIn('If-none-match',dict(requests[0].header_items()))
            self.assertEqual(target.read_bytes(),b'checked bytes')

    def test_reuse_can_find_files_in_a_second_prior_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);empty=root/'empty';empty.mkdir();old=root/'old';old.mkdir();new=root/'new';new.mkdir()
            target,receipt=fetch.cache_paths(URL,old);target.write_bytes(b'prior')
            receipt.write_text(json.dumps({'url':URL,'sha256':sha_file(target)}),encoding='utf-8')
            self.assertTrue(fetch.reuse_cached(URL,[empty,old],new))
            self.assertEqual(fetch.cache_paths(URL,new)[0].read_bytes(),b'prior')


class MetadataRecoveryTests(unittest.TestCase):
    @staticmethod
    def denied():
        error=PermissionError(13,'TEST Windows file sharing')
        error.winerror=5
        return error

    def test_transient_windows_rename_failure_keeps_old_file_then_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'summary.json';target.write_text('{"old":true}',encoding='utf-8')
            original=fetch.os.replace;calls=[]
            def replace(source,destination):
                calls.append(Path(source))
                if len(calls)<3:
                    self.assertEqual(target.read_text(encoding='utf-8'),'{"old":true}')
                    raise self.denied()
                return original(source,destination)
            with patch.object(fetch.os,'replace',side_effect=replace),patch.object(fetch.time,'sleep') as pause:
                fetch.write_json(target,{'new':True})
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'new':True})
            self.assertEqual(len(calls),3);self.assertEqual(pause.call_count,2)

    def test_permanent_windows_rename_failure_never_deletes_previous_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'summary.json';target.write_text('{"old":true}',encoding='utf-8')
            with patch.object(fetch.os,'replace',side_effect=self.denied()) as replace,patch.object(fetch.time,'sleep'):
                with self.assertRaises(PermissionError):fetch.write_json(target,{'new':True})
            self.assertEqual(replace.call_count,10)
            self.assertEqual(target.read_text(encoding='utf-8'),'{"old":true}')
            self.assertFalse(list(Path(temp).glob('*.tmp')))

    def test_concurrent_metadata_writes_use_distinct_temporary_files(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'summary.json';original=fetch.os.replace
            barrier=threading.Barrier(2);sources=[];lock=threading.Lock();synchronized=set()
            def replace(source,destination):
                with lock:
                    initial=Path(source) not in synchronized
                    if initial:sources.append(Path(source));synchronized.add(Path(source))
                if initial:barrier.wait(timeout=5)
                return original(source,destination)
            with patch.object(fetch.os,'replace',side_effect=replace),ThreadPoolExecutor(max_workers=2) as executor:
                jobs=[executor.submit(fetch.write_json,target,{'writer':i}) for i in range(2)]
                for job in jobs:job.result(timeout=10)
            self.assertEqual(len(set(sources)),2)
            self.assertIn(json.loads(target.read_text(encoding='utf-8'))['writer'],[0,1])

    def test_a_second_collection_cannot_write_the_same_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            with fetch.collection_lock(temp):
                with self.assertRaisesRegex(ImportErrorDetail,'jine stahovani'):
                    with fetch.collection_lock(temp):self.fail('second collection acquired the cache')
            with fetch.collection_lock(temp):pass

    def test_busy_progress_report_does_not_stop_downloads_or_lose_final_snapshot(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch,'plan',return_value=plan()),patch.object(fetch,'download',side_effect=fake_download),contextlib.redirect_stdout(io.StringIO()):
            original=fetch.write_json
            def write(path,value):
                if Path(path).name=='summary.json':raise self.denied()
                return original(path,value)
            with patch.object(fetch,'write_json',side_effect=write):
                manifest=fetch.fetch_all(temp,2026,DAY,workers=2)
            self.assertTrue(manifest.is_file())
            snapshots=list(Path(temp).glob('summary-*.json'));self.assertEqual(len(snapshots),1)
            status=json.loads(snapshots[0].read_text(encoding='utf-8'))
            self.assertTrue(status['sourcePlanComplete']);self.assertFalse(status['inProgress'])
            self.assertGreater(status['statusWriteWarningCount'],0)
            self.assertEqual(sum(status['completedCounts'].values()),5)

    def test_failed_manifest_install_keeps_old_complete_manifest_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(fetch,'plan',return_value=plan()),patch.object(fetch,'download',side_effect=fake_download),contextlib.redirect_stdout(io.StringIO()):
            target=Path(temp)/'inputs.json';target.write_text('{"old":true}',encoding='utf-8')
            original=fetch.replace_file
            def replace(source,destination):
                if Path(destination)==target:raise self.denied()
                return original(source,destination)
            with patch.object(fetch,'replace_file',side_effect=replace):
                with self.assertRaises(PermissionError):fetch.fetch_all(temp,2026,DAY,workers=2)
            self.assertEqual(target.read_text(encoding='utf-8'),'{"old":true}')
            snapshot=next(Path(temp).glob('summary-*.json'))
            status=json.loads(snapshot.read_text(encoding='utf-8'))
            self.assertFalse(status['sourcePlanComplete']);self.assertIn('PermissionError',status['error'])

    def test_existing_temporary_name_from_an_older_run_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'summary.json';old=Path(temp)/'summary.json.tmp'
            old.write_text('old run still owns this',encoding='utf-8')
            fetch.write_json(target,{'new':True})
            self.assertEqual(old.read_text(encoding='utf-8'),'old run still owns this')
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'new':True})


class NativeWindowsMetadataTests(unittest.TestCase):
    @unittest.skipUnless(fetch.os.name=='nt','Windows file-sharing regression')
    def test_real_windows_reader_temporarily_blocks_report_replacement(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        create=kernel.CreateFileW
        create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        create.restype=wintypes.HANDLE
        close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'summary.json';target.write_text('{"old":true}',encoding='utf-8')
            handle=create(str(target),0x80000000,1,None,3,0x80,None)
            if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            timers=[];errors=[];original=fetch.os.replace
            def replace(source,destination):
                try:return original(source,destination)
                except OSError as error:
                    errors.append(error.winerror)
                    if len(errors)==1:
                        timer=threading.Timer(0.1,lambda:close(handle));timers.append(timer);timer.start()
                    raise
            try:
                with patch.object(fetch.os,'replace',side_effect=replace):
                    fetch.write_json(target,{'new':True})
            finally:
                if timers:timers[0].join(2)
                else:close(handle)
            self.assertTrue(errors)
            self.assertTrue(set(errors)<={5,32,33,1224})
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'new':True})

if __name__=='__main__':unittest.main()
