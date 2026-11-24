import gzip
import json
from pathlib import Path
import tempfile
import unittest
import types
import subprocess
import sys
from unittest.mock import patch
from national_fixture import DAY,train,cancellation
from national import rail
from national.rail import RailMessages
from national.rail_cache import checkpoint_key,load_checkpoint,save_checkpoint


class RailCheckpointTests(unittest.TestCase):
    def test_round_trip_retains_paths_cancellations_and_horizon(self):
        with tempfile.TemporaryDirectory() as temp:
            store=RailMessages(DAY,'2026-10-28');store.add(train());store.add(cancellation())
            path=Path(temp)/'state.json.gz';save_checkpoint(path,'TEST',store)
            restored=load_checkpoint(path,'TEST',DAY,'2026-10-28')
            self.assertIsNotNone(restored)
            self.assertEqual(store.export(DAY,'2026-10-28'),restored.export(DAY,'2026-10-28'))

    def test_key_ignores_relocations_but_detects_source_bytes_and_horizon_changes(self):
        first=[{'url':'https://example.invalid/a','sha256':'a'*64,'path':'old'}]
        moved=[{**first[0],'path':'new','verifiedAt':'later'}]
        self.assertEqual(checkpoint_key(first,DAY,DAY),checkpoint_key(moved,DAY,DAY))
        self.assertNotEqual(checkpoint_key(first,DAY,DAY),checkpoint_key([{**first[0],'sha256':'b'*64}],DAY,DAY))
        self.assertNotEqual(checkpoint_key(first,DAY,DAY),checkpoint_key(first,DAY,'2026-10-27'))

    def test_export_fixes_do_not_discard_valid_parsed_source_checkpoint(self):
        entries=[{'sha256':'a'*64}]
        expected=checkpoint_key(entries,DAY,DAY)
        with patch.object(RailMessages,'export',return_value=None):
            self.assertEqual(checkpoint_key(entries,DAY,DAY),expected)

    def test_fingerprint_is_identical_in_cold_processes_with_different_imports(self):
        command="import sys;sys.path.insert(0,'scripts');from national.rail_cache import checkpoint_key;print(checkpoint_key([{'sha256':'a'*64}], '2026-10-26','2026-10-26'))"
        root=Path(__file__).resolve().parents[1]
        cold=subprocess.check_output([sys.executable,'-c',command],cwd=root,text=True).strip()
        other=subprocess.check_output([sys.executable,'-c',command.replace('from national.rail_cache','import prepare_priority_data;from national.rail_cache')],cwd=root,text=True).strip()
        self.assertEqual(cold,other)
        self.assertEqual(cold,checkpoint_key([{'sha256':'a'*64}],DAY,DAY))

    def test_changed_parser_default_invalidates_the_checkpoint(self):
        entries=[{'sha256':'a'*64}]
        expected=checkpoint_key(entries,DAY,DAY)
        with patch.object(rail.calendar,'__defaults__',(True,)):
            self.assertNotEqual(checkpoint_key(entries,DAY,DAY),expected)

    def test_relocated_source_and_export_line_shifts_keep_the_same_parser_key(self):
        entries=[{'sha256':'a'*64}]
        expected=checkpoint_key(entries,DAY,DAY)
        original=rail.parse
        changed=types.FunctionType(original.__code__.replace(co_filename='another-checkout/rail.py',
                                   co_firstlineno=9000),original.__globals__)
        with patch.object(rail,'parse',changed):
            self.assertEqual(checkpoint_key(entries,DAY,DAY),expected)

    def test_fingerprint_uses_loaded_functions_without_reopening_changed_source(self):
        entries=[{'sha256':'a'*64}]
        expected=checkpoint_key(entries,DAY,DAY)
        with patch('inspect.getsource',side_effect=AssertionError('source changed on disk')):
            self.assertEqual(checkpoint_key(entries,DAY,DAY),expected)

    def test_corrupt_or_wrong_version_checkpoint_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'state.json.gz';path.write_bytes(b'not gzip')
            self.assertIsNone(load_checkpoint(path,'TEST',DAY,DAY))
            store=RailMessages(DAY,DAY);store.add(train());save_checkpoint(path,'OLD',store)
            self.assertIsNone(load_checkpoint(path,'NEW',DAY,DAY))


if __name__=='__main__':
    unittest.main()
