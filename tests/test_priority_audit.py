import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from national_fixture import train, cancellation, ostrava, DAY
import audit_priority_sources as audit_module
from fetch_priority_sources import sample_plan

class AuditTests(unittest.TestCase):
    def test_sample_planning_does_not_list_historic_update_months(self):
        base = 'https://portal.cisjr.cz/pub/draha/celostatni/szdc/2026/'
        month = base+'2026-10/'
        responses = {base:[base+'JR2026.zip',base+'2026-09/',month],
                     month:[month+'normal.xml.zip',month+'cancel_one.xml.zip','https://example.invalid/outside.zip']}
        requested=[]
        def listing(url):requested.append(url);return responses[url]
        with patch('fetch_priority_sources.listing',side_effect=listing):
            result=sample_plan(2026,DAY)
        self.assertEqual(requested,[base,month])
        self.assertEqual(result['rail'],[month+'cancel_one.xml.zip',month+'normal.xml.zip'])
        self.assertNotIn('pid',result)

    def fake_sources(self):
        root='https://portal.cisjr.cz/pub/draha/celostatni/szdc/2026/2026-10/'
        return {'rail':[root+'normal.xml.zip',root+'cancel_one.xml.zip'],
                'ostrava':['https://portal.cisjr.cz/pub/netex/urban.zip','https://portal.cisjr.cz/pub/netex/road.zip']}

    def run_synthetic(self,destination,bad_train=False,bad_container=False,missing_cancel=False,empty_rail=False):
        def download(url,directory):return {'path':url,'url':url,'sha256':'synthetic-only'}
        def documents(url):
            if url.endswith('normal.xml.zip'):
                if bad_container:raise zipfile.BadZipFile('TEST broken archive')
                if empty_rail:return
                root=train()
                if bad_train:root.tag='UnsupportedMessage'
                yield root,'normal'
            elif url.endswith('cancel_one.xml.zip'):
                if not empty_rail:yield cancellation(),'cancel'
            elif url.endswith('urban.zip'):
                yield ostrava('tram',code='TEST-T'),'urban-tram'
                yield ostrava('trolleyBus',code='TEST-TB'),'urban-trolley'
            else:yield ostrava('bus',code='TEST-B'),'road-bus'
        sources=self.fake_sources()
        if missing_cancel:sources['rail']=sources['rail'][:1]
        with patch.object(audit_module,'sample_plan',return_value=sources), patch.object(audit_module,'download',side_effect=download), patch.object(audit_module,'documents',side_effect=documents),patch.object(audit_module,'archive_documents',side_effect=documents),contextlib.redirect_stdout(io.StringIO()):
            return audit_module.audit(destination,2026,DAY)

    def test_successful_format_sample_never_claims_coverage_live_delays_or_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            result=self.run_synthetic(temp)
            self.assertTrue(result['formatSamplePassed'])
            self.assertTrue(result['sampleOnly'])
            for key in ['fullCoverageVerified','nationalRealtimeConnected','ostravaRealtimeConnected','apkActivated']:
                self.assertFalse(result[key])
            self.assertEqual(result['rail']['samples'],2)
            self.assertEqual(result['ostrava']['modes'],[0,3,11])
            self.assertEqual(json.loads((Path(temp)/'report.json').read_text(encoding='utf-8')),result)
            self.assertTrue(list((Path(temp)/'samples').glob('*.xml')))

    def test_parser_failure_preserves_report_and_raw_sample_for_diagnosis(self):
        with tempfile.TemporaryDirectory() as temp:
            result=self.run_synthetic(temp,bad_train=True)
            self.assertFalse(result['formatSamplePassed'])
            self.assertEqual(len(result['rail']['problems']),1)
            self.assertIn('Unsupported',result['rail']['problems'][0]['error'])
            self.assertTrue((Path(temp)/'samples/rail-1.xml').is_file())
            self.assertTrue((Path(temp)/'report.json').is_file())

    def test_network_failure_still_writes_a_diagnostic_report(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(audit_module,'sample_plan',side_effect=OSError('TEST offline')),contextlib.redirect_stdout(io.StringIO()):
            result=audit_module.audit(temp,2026,DAY)
            self.assertFalse(result['formatSamplePassed'])
            self.assertEqual(result['blockedBy'],'OSError: TEST offline')
            self.assertEqual(result['rail']['samples'],0)
            self.assertTrue((Path(temp)/'report.json').is_file())

    def test_missing_cancellation_sample_cannot_pass_but_dpo_still_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            result=self.run_synthetic(temp,missing_cancel=True)
            self.assertFalse(result['formatSamplePassed'])
            self.assertIn('no train/cancellation sample',result['blockedBy'])
            self.assertEqual(result['ostrava']['archivesRead'],2)

    def test_bad_rail_container_does_not_prevent_collecting_both_dpo_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            result=self.run_synthetic(temp,bad_container=True)
            self.assertFalse(result['formatSamplePassed'])
            self.assertIn('BadZipFile',result['blockedBy'])
            self.assertEqual(result['ostrava']['archivesRead'],2)
            self.assertEqual(result['ostrava']['parsed'],3)
            self.assertTrue(list((Path(temp)/'samples').glob('ostrava-*.xml')))

    def test_empty_rail_containers_do_not_count_as_successful_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            result=self.run_synthetic(temp,empty_rail=True)
            self.assertFalse(result['formatSamplePassed'])
            self.assertEqual(result['rail']['archivesRead'],0)
            self.assertEqual(result['ostrava']['archivesRead'],2)

if __name__=='__main__':unittest.main()
