"""Pipeline tests use exclusively hand-written synthetic archives, never live timetables."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
from national_fixture import train, cancellation, ostrava, DAY
from national.common import sha_file, ImportErrorDetail
from prepare_priority_data import prepare
from fetch_priority_sources import plan, allowed


def fixtures(folder):
    rail=folder/'rail.zip'
    with zipfile.ZipFile(rail,'w') as z:z.writestr('train.xml',ET.tostring(train()))
    city=folder/'city.zip'
    with zipfile.ZipFile(city,'w') as z:
        for mode,code in [('tram','T'),('bus','B'),('trolleyBus','TB')]:z.writestr(code+'.xml',ET.tostring(ostrava(mode,code=code)))
    pid=folder/'pid.zip'
    with zipfile.ZipFile(pid,'w') as z:
        z.writestr('feed_info.txt','feed_start_date,feed_end_date\n20261025,20261029\n')
        z.writestr('stops.txt','stop_id,stop_name,stop_lat,stop_lon\nPID-A,TEST PID A,0,0\nPID-B,TEST PID B,0,0\n')
        z.writestr('routes.txt','route_id,route_short_name,route_long_name,route_type\nPID-L,TEST12,TEST route,0\n')
        z.writestr('trips.txt','route_id,service_id,trip_id,trip_headsign\nPID-L,PID-CAL,PID-TRIP,TEST PID B\n')
        z.writestr('stop_times.txt','trip_id,arrival_time,departure_time,stop_id,stop_sequence\nPID-TRIP,05:00:00,05:00:00,PID-A,1\nPID-TRIP,05:40:00,05:40:00,PID-B,2\n')
        z.writestr('calendar.txt','service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nPID-CAL,1,1,1,1,1,1,1,20261025,20261029\n')
    receipts={'schema':1,'sourcePlanComplete':True,'synthetic':True,'sources':{kind:[{'path':str(file.resolve()),'sha256':sha_file(file)}] for kind,file in [('rail',rail),('ostrava',city),('pid',pid)]}}
    inputs=folder/'inputs.json';inputs.write_text(json.dumps(receipts),encoding='utf-8')
    return inputs,receipts

class PriorityPipelineTests(unittest.TestCase):
    def test_complete_synthetic_import_writes_an_explicitly_unpublished_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);inputs,_=fixtures(folder)
            with contextlib.redirect_stdout(io.StringIO()):report=prepare(inputs,DAY,2,folder/'prepared')
            metadata=json.loads((folder/'prepared/candidate/manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(set(metadata['days']),{'2026-10-26','2026-10-27'})
            self.assertTrue(metadata['candidateOnly']);self.assertFalse(report['apkActivated']);self.assertFalse(report['realDataImported']);self.assertEqual(report['ostrava']['modes'],[0,3,11]);self.assertFalse(report['productionPublished'])
    def test_prefetched_individual_updates_match_the_streamed_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);inputs,receipts=fixtures(folder)
            canceled=folder/'canceled.zip'
            with zipfile.ZipFile(canceled,'w') as z:z.writestr('cancel.xml',ET.tostring(cancellation()))
            receipts['sources']['rail'].append({'path':str(canceled),'sha256':sha_file(canceled)})
            inputs.write_text(json.dumps(receipts),encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):prepare(inputs,DAY,2,folder/'streamed')
            for index,entry in enumerate(receipts['sources']['rail']):
                entry['url']='https://example.invalid/'+str(index)+'.xml.zip'
            inputs.write_text(json.dumps(receipts),encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):prepare(inputs,DAY,2,folder/'prefetched')
            left=json.loads((folder/'streamed/candidate/manifest.json').read_text(encoding='utf-8'))
            right=json.loads((folder/'prefetched/candidate/manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(left,right)

    def test_an_individual_update_with_multiple_messages_is_rejected_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);inputs,receipts=fixtures(folder)
            entry=receipts['sources']['rail'][0];path=Path(entry['path'])
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('one.xml',ET.tostring(train()))
                z.writestr('two.xml',ET.tostring(train(path='OTHER')))
            entry['sha256']=sha_file(path);entry['url']='https://example.invalid/update.xml.zip'
            inputs.write_text(json.dumps(receipts),encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(ImportErrorDetail,'exactly one XML'):
                prepare(inputs,DAY,2,folder/'prepared')
            self.assertFalse((folder/'prepared/candidate/manifest.json').exists())

    def test_checksum_failure_leaves_an_existing_candidate_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);inputs,receipts=fixtures(folder)
            receipts['sources']['rail'][0]['sha256']='0'*64;inputs.write_text(json.dumps(receipts),encoding='utf-8')
            target=folder/'prepared/candidate';target.mkdir(parents=True);sentinel=target/'manifest.json';sentinel.write_text('existing')
            with self.assertRaisesRegex(ImportErrorDetail,'checksum'):prepare(inputs,DAY,2,folder/'prepared')
            self.assertEqual(sentinel.read_text(),'existing')
    def test_incomplete_source_plan_cannot_be_claimed_as_full_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);inputs,receipts=fixtures(folder);receipts['sourcePlanComplete']=False;inputs.write_text(json.dumps(receipts))
            with self.assertRaisesRegex(ImportErrorDetail,'Incomplete'):prepare(inputs,DAY,2,folder/'prepared')
            self.assertFalse((folder/'prepared/candidate/manifest.json').exists())
    def test_source_plan_includes_base_and_all_listed_updates_up_to_the_selected_period(self):
        base='https://portal.cisjr.cz/pub/draha/celostatni/szdc/2026/'
        def links(url):
            if url==base:return [base+'JR2026.zip',base+'2025-11/',base+'2026-10/',base+'2026-11/']
            return [url+'train.xml.zip',url+'cancel.xml.zip',base]
        with patch('fetch_priority_sources.listing',side_effect=links):sources=plan(2026,DAY)
        self.assertIn(base+'JR2026.zip',sources['rail']);self.assertIn(base+'2025-11/cancel.xml.zip',sources['rail']);self.assertNotIn(base+'2026-11/train.xml.zip',sources['rail']);self.assertEqual(len(sources['ostrava']),2)
    def test_download_targets_are_restricted_to_official_https_data_hosts(self):
        for url in ['http://portal.cisjr.cz/pub/x.zip','https://example.invalid/x.zip','https://portal.cisjr.cz:444/pub/x.zip','https://user@portal.cisjr.cz/pub/x.zip','https://portal.cisjr.cz/pub/../x.zip']:
            with self.subTest(url=url),self.assertRaises(ImportErrorDetail):allowed(url)

if __name__=='__main__':unittest.main()
