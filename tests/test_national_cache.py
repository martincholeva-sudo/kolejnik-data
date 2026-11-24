import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from national_cache import pack, restore


class NationalCacheTests(unittest.TestCase):
    def test_roundtrip_keeps_sources_and_checkpoint_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); original = base/'original'; target = base/'restored'
            (original/'sources'/'nested').mkdir(parents=True)
            for i in range(1000):
                (original/'sources'/'nested'/str(i)).write_bytes(b'public timetable '+str(i).encode())
            (original/'prepare').mkdir()
            (original/'prepare'/'rail-state-test.json.gz').write_bytes(b'checkpoint')
            (original/'prepare'/'excluded.txt').write_text('not an input')
            (original/'excluded.txt').write_text('not an input')
            archive = base/'inputs.tar'
            pack(original, archive); restore(target, archive)
            self.assertEqual(len(list((target/'sources'/'nested').iterdir())), 1000)
            self.assertEqual((target/'sources'/'nested'/'999').read_bytes(), b'public timetable 999')
            self.assertEqual((target/'prepare'/'rail-state-test.json.gz').read_bytes(), b'checkpoint')
            self.assertFalse((target/'excluded.txt').exists())
            self.assertFalse((target/'prepare'/'excluded.txt').exists())

    def test_restoration_rejects_escaping_paths_and_links(self):
        for name, kind in [('sources/../../outside', tarfile.REGTYPE),
                           ('target/secret', tarfile.REGTYPE),
                           ('sources/link', tarfile.SYMTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp); archive = base/'inputs.tar'
                with tarfile.open(archive, 'w') as out:
                    item = tarfile.TarInfo(name); item.type = kind
                    if kind == tarfile.SYMTYPE: item.linkname = '../../outside'
                    else: item.size = 1
                    out.addfile(item, io.BytesIO(b'x') if kind == tarfile.REGTYPE else None)
                with self.assertRaises(ValueError): restore(base/'restored', archive)
                self.assertFalse((base/'outside').exists())

    def test_missing_archive_is_a_cold_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            restore(Path(tmp)/'restored', Path(tmp)/'absent.tar')


if __name__ == '__main__':
    unittest.main()
