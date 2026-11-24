import unittest,tempfile,json,gzip,sys,datetime as dt,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from data_validation import validate_public,verify_deployed,validate_manifest
from update_public_data import update

NOW=dt.datetime(2026,10,26,9,tzinfo=dt.timezone.utc)
TODAY='2026-10-26'
def revision(folder,sha='a'*64,broken=False):
    days={(dt.date(2026,10,26)+dt.timedelta(days=i)).isoformat():None for i in range(14)}
    m={'schema':2,'sha256':sha,'start':TODAY,'end':'2026-11-08','importedAt':NOW.isoformat(),'checkedAt':NOW.isoformat(),'days':{day:sha[:12]+'-'+day+'.json.gz' for day in days}}
    for day,name in m['days'].items():
        raw={'schema':2,'sha256':sha if not broken else 'invalid','active':{day:['s']},'stops':[{},{}],'trips':[[],[]]}
        with gzip.open(folder/name,'wt',encoding='utf-8') as f:json.dump(raw,f)
    (folder/'manifest.json').write_text(json.dumps(m),encoding='utf-8');(folder/'walking-links.json').write_text('{"schema":1}',encoding='utf-8');return m

def validate(folder,manifest=None):return validate_public(folder,manifest,today=TODAY,now=NOW if manifest is None else dt.datetime.fromisoformat(manifest['checkedAt']),min_stops=1,min_trips=1)

class DataValidationTest(unittest.TestCase):
    def test_complete_revision(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);m=revision(p);self.assertEqual(validate(p),m)
    def test_missing_or_foreign_bundle_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);m=revision(p);(p/m['days'][TODAY]).unlink()
            with self.assertRaises(FileNotFoundError):validate(p)
            revision(p,broken=True)
            with self.assertRaisesRegex(ValueError,'revision'):validate(p)
    def test_holes_path_traversal_old_and_future_checks_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            m=revision(Path(d));bad=json.loads(json.dumps(m));bad['days'].pop('2026-10-27')
            with self.assertRaises(ValueError):validate_manifest(bad,TODAY,NOW)
            for checked in ['2026-10-20T09:00:00Z','2026-10-26T10:00:00Z','invalid']:
                bad={**m,'checkedAt':checked}
                with self.assertRaises(ValueError):validate_manifest(bad,TODAY,NOW)
            m['days'][TODAY]='../secret.json.gz'
            with self.assertRaises(ValueError):validate_manifest(m,TODAY,NOW)
    def test_public_manifest_without_real_files_is_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);m=revision(p)
            def fetch(url):
                if 'manifest.json' in url:return json.dumps(m).encode()
                raise FileNotFoundError(url)
            with self.assertRaises(FileNotFoundError):verify_deployed(m,fetch_bytes=fetch,today=TODAY,now=NOW,min_stops=1,min_trips=1)
            def complete(url):return (p/('manifest.json' if 'manifest.json' in url else url.rsplit('/',1)[-1])).read_bytes()
            self.assertEqual(verify_deployed(m,fetch_bytes=complete,today=TODAY,now=NOW,min_stops=1,min_trips=1),m)
    def test_failed_import_leaves_public_revision_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);public=p/'public';public.mkdir();old=revision(public);original=(public/'manifest.json').read_bytes()
            def broken(source,out,stage,carry_from):revision(stage,hashlib.sha256(source.read_bytes()).hexdigest(),broken=True)
            with self.assertRaises(ValueError):update(public,p/'runtime',fetch=lambda:b'new provider',build_snapshot=broken,validate=validate)
            self.assertEqual((public/'manifest.json').read_bytes(),original);self.assertTrue(all((public/n).exists() for n in old['days'].values()))
    def test_successful_revision_keeps_previous_readers_and_checks_unchanged_files(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);public=p/'public';public.mkdir();old=revision(public);raw=b'new provider'
            def build(source,out,stage,carry_from):revision(stage,hashlib.sha256(source.read_bytes()).hexdigest())
            new=update(public,p/'runtime',fetch=lambda:raw,build_snapshot=build,validate=validate)
            self.assertNotEqual(old['sha256'],new['sha256']);self.assertTrue(all((public/n).exists() for n in old['days'].values()))
            original=(public/'manifest.json').read_bytes();(public/new['days'][TODAY]).unlink()
            with self.assertRaises(FileNotFoundError):update(public,p/'runtime',fetch=lambda:raw,validate=validate)
            self.assertEqual((public/'manifest.json').read_bytes(),original)

if __name__=='__main__':unittest.main()
