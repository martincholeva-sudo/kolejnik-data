import copy
import hashlib
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from national_fixture import DAY
from national.common import documents, first, children, descendants, ImportErrorDetail
from national.rail import parse,RailMessages

FIXTURE=Path(__file__).parent/'fixtures/cis/PA_0054_--KADR076655_01_2026.xml.zip'


class RealRailTrackTests(unittest.TestCase):
    def source(self):
        return next(documents(FIXTURE))[0]

    def test_original_departure_track_annotations_keep_timed_path(self):
        provenance=json.loads((FIXTURE.parent/'PROVENANCE.json').read_text(encoding='utf-8'))
        expected=next(row['sha256'] for row in provenance['additionalFixtures'] if row['file']==FIXTURE.name)
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),expected)
        root=self.source()
        record=parse(root)
        self.assertEqual(len(children(first(root,'CZPTTInformation'),'CZPTTLocation')),125)
        self.assertEqual(len(record['points']),115)
        self.assertEqual(len(record['trackChangeAnnotations']),10)
        for annotation in record['trackChangeAnnotations']:
            self.assertEqual(record['points'][annotation['pointIndex']]['id'],annotation['station'])
            self.assertTrue(annotation['departureTrack'])

    def test_annotations_do_not_change_calendars_times_or_boarding(self):
        root=self.source()
        parsed=parse(root)
        normalized=copy.deepcopy(root)
        info=first(normalized,'CZPTTInformation')
        for node in list(children(info,'CZPTTLocation')):
            if not descendants(node,'Timing'):
                info.remove(node)
        expected=parse(normalized)
        self.assertEqual(parsed['points'],expected['points'])
        self.assertEqual(parsed['days'],expected['days'])
        self.assertEqual(parsed['key'],expected['key'])
        self.assertEqual(parsed['train'],expected['train'])

    def test_untimed_other_station_is_not_silently_skipped(self):
        root=self.source()
        info=first(root,'CZPTTInformation')
        annotation=next(node for node in children(info,'CZPTTLocation') if not descendants(node,'Timing'))
        first(first(annotation,'Location'),'LocationPrimaryCode').text='99999'
        with self.assertRaisesRegex(ImportErrorDetail,'Missing station timing'):parse(root)

    def test_untimed_public_stop_is_not_mistaken_for_a_track_annotation(self):
        root=self.source()
        annotation=next(node for node in children(first(root,'CZPTTInformation'),'CZPTTLocation') if not descendants(node,'Timing'))
        ET.SubElement(annotation,'TrainType').text='1'
        with self.assertRaisesRegex(ImportErrorDetail,'Missing station timing'):parse(root)

    def test_real_departure_track_may_omit_the_allocation_company(self):
        path=FIXTURE.parent/'PA_0054_--KADR112429_01_2026.xml.zip'
        record=parse(next(documents(path))[0])
        self.assertTrue(any(item['allocationCompany']=='' for item in record['trackChangeAnnotations']))
        self.assertTrue(any(point['replacementTransport'] for point in record['points']))

    def test_real_declared_countertime_is_exported_without_clock_changes(self):
        path=FIXTURE.parent/'PA_0054_--KADR272587_01_2026.xml.zip'
        root=next(documents(path))[0];record=parse(root)
        point=next(p for p in record['points'] if p['name']=='Praha-Radotín')
        self.assertEqual((point['arrival'],point['departure']),(18960,18840))
        self.assertTrue(point['inconsistentTime'])
        self.assertEqual(point['sourceDwellTime'],'')
        store=RailMessages();store.add(root)
        network,report=store.export('2026-10-27','2026-10-27')
        self.assertTrue(report['completeTimingsExported'])
        self.assertEqual(report['declaredCounterTimeJourneyVariants'],1)
        route=network['routes'][network['trips'][0][2]]
        declaration=route[3]['declaredCounterTimes'][0]
        self.assertEqual((declaration['arrival'],declaration['departure']),(18960,18840))
        row=next(row for row in network['trips'][0][5] if row[5]==declaration['sequence'])
        self.assertEqual(row[1:3],[18960,18840])

    def test_original_additional_archives_still_match_their_receipts(self):
        provenance=json.loads((FIXTURE.parent/'PROVENANCE.json').read_text(encoding='utf-8'))
        for item in provenance['additionalFixtures']:
            self.assertEqual(hashlib.sha256((FIXTURE.parent/item['file']).read_bytes()).hexdigest(),item['sha256'])

    def test_real_night_run_does_not_publish_the_unboardable_midnight_point(self):
        path=FIXTURE.parent/'PA_0054_--KADR255387_02_2026.xml.zip'
        root=next(documents(path))[0];record=parse(root)
        self.assertEqual(next(p for p in record['points'] if p['id']=='CZ:55479')['arrival'],0)
        store=RailMessages();store.add(root);network,report=store.export('2026-10-27','2026-10-27')
        self.assertEqual(len(network['trips']),1)
        self.assertNotIn('Koštice nad Ohří',{s['name'] for s in network['stops']})
        trip=network['trips'][0]
        self.assertEqual(network['stops'][trip[5][-1][0]]['name'],'Bříza obec')
        self.assertEqual(trip[5][-1][1],83430)
        self.assertTrue(report['completeTimingsExported'])

    def test_real_903_replacement_keeps_public_olomouc_arrival(self):
        path=FIXTURE.parent/'PA_0054_--KADR378436_01_2026.xml.zip'
        store=RailMessages();store.archive(path)
        network,report=store.export('2026-11-01','2026-11-01')
        self.assertEqual(len(network['trips']),2)
        replacement=next(t for t in network['trips'] if network['routes'][t[2]][3]['replacementTransport'])
        self.assertEqual(network['stops'][replacement[5][-1][0]]['name'],'Olomouc hl. n.')
        self.assertEqual(replacement[5][-1][1],31860)
        self.assertNotIn('Olomouc kol. č. 5a',{s['name'] for s in network['stops']})
        self.assertTrue(report['completeTimingsExported'])

    def test_first_untimed_location_needs_a_real_timed_point(self):
        root=self.source()
        info=first(root,'CZPTTInformation')
        annotation=next(node for node in children(info,'CZPTTLocation') if not descendants(node,'Timing'))
        info.remove(annotation);info.insert(0,annotation)
        with self.assertRaisesRegex(ImportErrorDetail,'Missing station timing'):parse(root)


if __name__=='__main__':
    unittest.main()
