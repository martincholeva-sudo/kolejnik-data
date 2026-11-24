import copy
import hashlib
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from national_fixture import train, cancellation, loc, ostrava, network_fixture, DAY
from national.common import ImportErrorDetail, bitmap, documents, instant, instant_order
from national.rail import RailMessages
from national.ostrava import OstravaNetex
from national.bundle import compose, write_candidate

class RailTests(unittest.TestCase):
    def test_real_cis_gzip_named_as_zip_preserves_the_public_route_and_calendar(self):
        path=Path(__file__).parent/'fixtures/cis/PA_0054_--KADR218357_01_2026.xml.zip'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),'4bad8213699821cedb620a6f69984fe7a179e916347f0670c93b9f3f14bcd7d8')
        self.assertTrue(path.read_bytes().startswith(b'\x1f\x8b'))
        store=RailMessages();store.archive(path)
        network,report=store.export('2026-12-04','2026-12-12')
        self.assertEqual(report['activeOperatorsExported'],['3246'])
        self.assertEqual(len(network['trips']),1)
        trip=network['trips'][0];rows=trip[5]
        self.assertEqual(trip[4],'1101');self.assertEqual(len(rows),11)
        self.assertEqual(len(next(iter(store.paths.values()))['points']),61)
        self.assertEqual(network['stops'][rows[0][0]]['id'],'czrail:CZ:33295')
        self.assertEqual(network['stops'][rows[-1][0]]['id'],'czrail:CZ:34124')
        self.assertEqual((rows[0][2],rows[-1][1]),(18120,27300))
        self.assertEqual(sum(r[3]==0 for r in rows),11)
        self.assertFalse(network['active']['2026-12-06'])
        self.assertEqual(sum(bool(days) for days in network['active'].values()),8)
        self.assertFalse(report['realSourceValidated'])
        self.assertFalse(next(iter(store.paths.values()))['created'].endswith('+00:00'))

    def test_source_local_creation_and_cancellation_are_ordered_without_assuming_utc(self):
        store=RailMessages();store.add(train(stamp='2026-10-01T12:00:00'));store.add(cancellation(stamp='2026-10-02T12:00:00'))
        network,_=store.export(DAY,'2026-10-28')
        self.assertFalse(network['active']['2026-10-27']);self.assertTrue(network['active'][DAY])
        for newer in [train(stamp='2026-10-03T12:00:00Z'),cancellation(stamp='2026-10-03T12:00:00Z')]:
            with self.assertRaisesRegex(ImportErrorDetail,'Mixed'):store.add(newer)

    def test_revision_precision_and_clock_change_ambiguity_are_preserved(self):
        first=instant('2026-10-15T12:16:17.0000001',allow_local=True)
        second=instant('2026-10-15T12:16:17.0000002',allow_local=True)
        self.assertLess(instant_order(first,second),0)
        self.assertEqual(instant('2026-10-15T14:16:17+02:00'),instant('2026-10-15T12:16:17Z'))
        for value in ['2026-10-25T02:30:00','2026-03-29T02:30:00']:
            with self.assertRaisesRegex(ImportErrorDetail,'Clock-change'):instant(value,allow_local=True)
        self.assertTrue(instant('2026-10-25T02:30:00+02:00').endswith('+00:00'))

    def test_all_public_carriers_are_imported_without_a_whitelist(self):
        store=RailMessages()
        for i,code in enumerate(['TEST-CD','TEST-PRIVATE-1','NEW-UNKNOWN-CARRIER']):store.add(train(path=str(i),operator=code))
        network,report=store.export(DAY,'2026-10-28')
        self.assertEqual(report['activeOperatorsExported'],['NEW-UNKNOWN-CARRIER','TEST-CD','TEST-PRIVATE-1'])
        self.assertEqual(len(network['trips']),3)
    def test_independent_trains_with_identical_times_and_numbers_are_not_merged(self):
        store=RailMessages();store.add(train(path='ONE'));store.add(train(path='TWO'))
        network,_=store.export(DAY,DAY);self.assertEqual(len(network['trips']),2)
    def test_domestic_trip_through_a_foreign_point_remains_continuous(self):
        foreign=loc('X','TEST Foreign passage','07:00:00').replace('<CountryCodeISO>CZ','<CountryCodeISO>DE')
        store=RailMessages();store.add(train(points=[loc('A','TEST A','06:00:00'),foreign,loc('C','TEST C','09:00:00')]))
        network,_=store.export(DAY,DAY);self.assertEqual(len(network['trips']),1);self.assertEqual(network['trips'][0][5][1][3:5],[1,1]);self.assertEqual(network['stops'][1]['type'],3)
    def test_operator_change_does_not_create_a_phantom_transfer(self):
        store=RailMessages();store.add(train(points=[loc('A','A','06:00:00',operator='ONE'),loc('B','B','07:00:00',operator='TWO'),loc('C','C','09:00:00',operator='TWO')]))
        network,report=store.export(DAY,DAY);self.assertEqual(len(network['trips']),1);self.assertEqual(report['activeOperatorsExported'],['ONE','TWO'])
    def test_missing_base_path_for_a_partial_cancellation_blocks_the_import(self):
        store=RailMessages();store.add(cancellation(section=('A','B')))
        with self.assertRaisesRegex(ImportErrorDetail,'missing base path'):store.export(DAY,'2026-10-28')
    def test_full_cancellation_without_base_is_a_tombstone_for_only_its_exact_path(self):
        store=RailMessages();store.add(cancellation())
        other=train(path='OTHER',train_id='TRAIN-PATH-TEST',points=[loc('A','TEST Praha stanice','06:10:00'),loc('B','TEST mezistanice','07:10:00'),loc('C','TEST Ostrava stanice','09:10:00')]);store.add(other)
        graph,report=store.export(DAY,'2026-10-28')
        self.assertEqual(len(graph['trips']),1)
        self.assertEqual(len(graph['active']['2026-10-27']),1)
        self.assertEqual(len(report['fullCancellationPathsWithoutBase']),1)
        store.add(train())
        graph,report=store.export(DAY,'2026-10-28')
        self.assertEqual(report['fullCancellationPathsWithoutBase'],[])
        self.assertEqual(len(graph['active']['2026-10-27']),1)
        self.assertEqual(len(graph['active']['2026-10-26']),2)

    def test_full_cancellation_removes_only_the_marked_service_day(self):
        store=RailMessages();store.add(train());store.add(cancellation())
        network,report=store.export(DAY,'2026-10-28')
        self.assertTrue(network['active'][DAY]);self.assertFalse(network['active']['2026-10-27']);self.assertTrue(network['active']['2026-10-28']);self.assertEqual(report['fullCancelledServiceDays'],1)
    def test_load_order_and_duplicate_messages_do_not_change_the_snapshot(self):
        results=[]
        for order in [[train(),cancellation(),train()],[cancellation(),train()]]:
            store=RailMessages()
            for message in order:store.add(message)
            network,_=store.export(DAY,'2026-10-28');network.pop('coverage');network.pop('sha256');results.append(network)
        self.assertEqual(results[0],results[1])
    def test_newer_creation_supersedes_older_calendar_and_cancellation(self):
        store=RailMessages();store.add(cancellation());store.add(train(stamp='2026-10-03T00:00:00Z',bits='010'));store.add(train())
        network,_=store.export(DAY,'2026-10-28');self.assertFalse(network['active'][DAY]);self.assertTrue(network['active']['2026-10-27'])
    def test_conflicting_equal_timestamps_abort(self):
        store=RailMessages();store.add(train())
        with self.assertRaisesRegex(ImportErrorDetail,'Conflicting'):store.add(train(bits='010'))
    def test_partial_cancellation_preserves_the_uncancelled_endpoint(self):
        store=RailMessages();store.add(train());store.add(cancellation(section=('A','B')))
        network,report=store.export(DAY,'2026-10-28');services=set(network['active']['2026-10-27']);trips=[t for t in network['trips'] if t[1] in services]
        self.assertEqual(len(trips),1);self.assertEqual([network['stops'][r[0]]['id'] for r in trips[0][5]],['czrail:CZ:B','czrail:CZ:C']);self.assertEqual(report['partialCancelledServiceDays'],1)
    def test_private_middle_segment_cannot_be_traversed_as_a_public_trip(self):
        points=[loc('A','A','06:00:00'),loc('B','B','07:00:00',public=False),loc('C','C','08:00:00'),loc('D','D','09:00:00')]
        store=RailMessages();store.add(train(points=points));network,_=store.export(DAY,DAY)
        self.assertEqual(len(network['trips']),2);self.assertTrue(all(len(t[5])==2 for t in network['trips']))
    def test_midnight_offsets_and_boarding_restrictions_are_preserved(self):
        points=[loc('A','A','23:50:00',activity='0028'),loc('B','B','00:20:00',activity='0029',offset=1)]
        store=RailMessages();store.add(train(points=points));network,_=store.export(DAY,DAY);rows=network['trips'][0][5]
        self.assertEqual(rows[1][1],87600);self.assertEqual(rows[0][4],1);self.assertEqual(rows[1][3],1)
    def test_passage_points_do_not_become_passenger_boarding_points(self):
        store=RailMessages();store.add(train(points=[loc('A','A','06:00:00'),loc('B','B','07:00:00',activity='0099'),loc('C','C','08:00:00')]))
        network,_=store.export(DAY,DAY);self.assertEqual([stop['name'] for stop in network['stops']],['A','C'])
        self.assertEqual(len(network['trips'][0][5]),2)
        self.assertEqual(next(iter(store.paths.values()))['points'][1]['pickup'],1)
    def test_non_monotonic_times_and_ambiguous_timestamps_fail_closed(self):
        store=RailMessages();store.add(train(points=[loc('A','A','09:00:00'),loc('B','B','08:00:00')]))
        with self.assertRaisesRegex(ImportErrorDetail,'Non-monotonic'):store.export(DAY,DAY)
        with self.assertRaisesRegex(ImportErrorDetail,'timezone'):instant('2026-10-25T02:30:00')
    def test_duplicate_public_variants_are_merged_without_merging_distinct_carriers(self):
        store=RailMessages();store.add(train(path='1',train_id='SAME-TRAIN'));store.add(train(path='2',train_id='SAME-TRAIN'));store.add(train(path='3',operator='OTHER'))
        network,report=store.export(DAY,DAY);self.assertEqual(len(network['trips']),2);self.assertEqual(report['duplicatePublicJourneysMerged'],1)

class OstravaTests(unittest.TestCase):
    def test_tram_bus_trolleybus_and_unrelated_operator_selection(self):
        importer=OstravaNetex(DAY,'2026-10-28')
        for mode,code in [('tram','T'),('bus','B'),('trolleyBus','TB')]:importer.add(ostrava(mode,code=code))
        importer.add(ostrava('bus',ico='11111111',code='OTHER'))
        network,report=importer.export();self.assertEqual(report['modes'],[0,3,11]);self.assertEqual(len(network['trips']),3);self.assertEqual(report['otherOperatorsExcluded'],1)
    def test_explicit_boarding_prohibition_is_not_turned_into_a_public_departure(self):
        importer=OstravaNetex(DAY,DAY);importer.add(ostrava(pickup='false'));network,_=importer.export();self.assertEqual(network['trips'],[])
    def test_calendar_bits_and_negative_assignment(self):
        root=ostrava(bits='110')
        assignments=next(n for n in root.iter() if n.tag.endswith('dayTypeAssignments'))
        ET.SubElement(assignments,'DayTypeAssignment').extend([ET.fromstring('<DayTypeRef ref="DAYS"/>'),ET.fromstring('<Date>2026-10-27</Date>'),ET.fromstring('<IsAvailable>false</IsAvailable>')])
        importer=OstravaNetex(DAY,'2026-10-28');importer.add(root);network,_=importer.export();self.assertTrue(network['active'][DAY]);self.assertFalse(network['active']['2026-10-27']);self.assertFalse(network['active']['2026-10-28'])
    def test_unresolved_calendar_and_booking_requirement_abort_selected_import(self):
        for change in ['calendar','booking']:
            root=ostrava()
            if change=='calendar':next(n for n in root.iter() if n.tag.endswith('ValidDayBits')).text='1'
            else:ET.SubElement(next(n for n in root.iter() if n.tag.endswith('ServiceJourney')),'FlexibleServiceProperties')
            with self.assertRaises(ImportErrorDetail):OstravaNetex(DAY,DAY).add(root)
    def test_no_coordinate_or_crossing_is_invented_from_a_stop_name(self):
        importer=OstravaNetex(DAY,DAY);importer.add(ostrava());network,_=importer.export();self.assertEqual(network['paths'],[]);self.assertTrue(all(s['coordinatesKnown'] is False for s in network['stops']))
    def test_unknown_timing_reference_and_duplicate_conflicts_abort(self):
        first=ostrava();second=ostrava(departure='09:20:00');importer=OstravaNetex(DAY,DAY);importer.add(first)
        with self.assertRaisesRegex(ImportErrorDetail,'Conflicting'):importer.add(second)
        invalid=ostrava();next(n for n in invalid.iter() if n.tag.endswith('StopPointInJourneyPatternRef')).set('ref','MISSING')
        with self.assertRaises(ImportErrorDetail):OstravaNetex(DAY,DAY).add(invalid)

class CompositionTests(unittest.TestCase):
    def test_nested_gzip_and_zip_are_recognized_by_content_instead_of_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml=ET.tostring(train());path=Path(tmp)/'message.xml.zip'
            path.write_bytes(gzip.compress(xml))
            self.assertEqual(len(list(documents(path))),1)
            with zipfile.ZipFile(path,'w') as z:z.writestr('message.xml.zip',gzip.compress(xml))
            self.assertEqual(len(list(documents(path))),1)
            path=Path(tmp)/'true-zip.xml.gz'
            with zipfile.ZipFile(path,'w') as z:z.writestr('message.xml',xml)
            self.assertEqual(len(list(documents(path))),1)
            path=Path(tmp)/'wrapped.zip';inner=io.BytesIO()
            with zipfile.ZipFile(inner,'w') as z:z.writestr('../bad.xml',xml)
            path.write_bytes(gzip.compress(inner.getvalue()))
            with self.assertRaisesRegex(ImportErrorDetail,'Unsafe'):list(documents(path))

    def test_gzip_does_not_bypass_xml_size_or_entity_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'message.xml.zip'
            path.write_bytes(gzip.compress(b'x'*16_000_001))
            with self.assertRaisesRegex(ImportErrorDetail,'limit'):list(documents(path))
            path.write_bytes(gzip.compress(b'<!DOCTYPE x [<!ENTITY x "text">]><x>&x;</x>'))
            with self.assertRaisesRegex(ImportErrorDetail,'DTD'):list(documents(path))

    def test_explicit_interchanges_and_source_namespaces_are_preserved(self):
        network=network_fixture();self.assertEqual(len(network['interchangeEvidence']),2);self.assertEqual(len(network['paths']),4);self.assertEqual(len({s['id'] for s in network['stops']}),len(network['stops']));self.assertTrue(network['candidateOnly'])
    def test_collisions_and_unverified_links_are_rejected(self):
        network=network_fixture()
        with self.assertRaisesRegex(ImportErrorDetail,'namespaces collide'):compose([('one',network),('two',network)],DAY,DAY)
        link={'schema':1,'links':[{'from':'TEST-PRAHA','to':'czrail:CZ:A','seconds':300}]}
        with self.assertRaisesRegex(ImportErrorDetail,'verification'):compose([('one',network)],DAY,DAY,link)
    def test_daily_candidates_read_back_and_never_write_into_production_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            network=network_fixture();folder=Path(tmp)/'candidate';metadata=write_candidate(network,folder,{'synthetic':True})
            self.assertEqual(metadata['scope'],'cz-rail-ostrava')
            with gzip.open(folder/metadata['days'][DAY],'rt',encoding='utf-8') as stream:bundle=json.load(stream)
            self.assertEqual(bundle['sha256'],metadata['sha256']);self.assertTrue(bundle['candidateOnly'])
            with self.assertRaises(ImportErrorDetail):write_candidate(network,Path(tmp)/'data-v2',{})
    def test_nested_archives_do_not_extract_and_reject_entities_or_unsafe_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested=io.BytesIO()
            with zipfile.ZipFile(nested,'w') as z:z.writestr('train.xml',ET.tostring(train()))
            path=Path(tmp)/'source.zip'
            with zipfile.ZipFile(path,'w') as z:z.writestr('train.xml.zip',nested.getvalue())
            self.assertEqual(len(list(documents(path))),1)
            with zipfile.ZipFile(path,'w') as z:z.writestr('../train.xml',ET.tostring(train()))
            with self.assertRaisesRegex(ImportErrorDetail,'Unsafe'):list(documents(path))
            path=Path(tmp)/'entity.xml';path.write_text('<!DOCTYPE x [<!ENTITY x "text">]><x>&x;</x>')
            with self.assertRaisesRegex(ImportErrorDetail,'DTD'):list(documents(path))
    def test_bad_calendar_length_fails_instead_of_guessing_service_days(self):
        with self.assertRaises(ImportErrorDetail):bitmap(DAY,'2026-10-28','11')

if __name__=='__main__':unittest.main()
