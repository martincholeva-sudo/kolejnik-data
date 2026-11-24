import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from national_fixture import ostrava, DAY
from national.common import ImportErrorDetail, descendants, first
from national.ostrava import OstravaNetex, archive_documents, unroll_cis_midnight

FIXTURES = Path(__file__).parent / 'fixtures/netex'

def fixture(document):
    return next(FIXTURES.glob('*LINE-' + str(document) + '_*.xml.gz'))

class RealOstravaTests(unittest.TestCase):
    def test_original_source_xml_is_preserved_verbatim(self):
        data=json.loads((FIXTURES/'PROVENANCE.json').read_text(encoding='utf-8'))
        for item in data['files']:
            raw=(FIXTURES/item['file']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item['fixtureSha256'])
            self.assertEqual(hashlib.sha256(gzip.decompress(raw)).hexdigest(),item['xmlSha256'])

    def test_real_midnight_trip_keeps_its_departure_service_day(self):
        city=OstravaNetex(DAY,'2026-11-01');city.archive(fixture(108))
        network,report=city.export()
        trip=next(t for t in network['trips'] if t[4]=='228')
        self.assertEqual((trip[5][0][2],trip[5][-1][1]),(85800,87720))
        self.assertEqual(network['routes'][trip[2]][1:3],['2',0])
        self.assertGreater(report['activeMidnightJourneys'],0)
        self.assertTrue(network['candidateOnly']);self.assertFalse(report['productionReady'])

    def test_same_stopplace_numbers_in_different_lines_do_not_merge(self):
        city=OstravaNetex(DAY,'2026-11-01')
        for doc in [108,10191]:city.archive(fixture(doc))
        network,_=city.export();groups={}
        for stop in network['stops']:groups.setdefault(stop['group'],set()).add(stop['name'])
        self.assertTrue(all(len(names)==1 for names in groups.values()))
        reused=[key for key in groups if key.endswith('CZ:cisjr_jdf:StopPlace:1')]
        self.assertEqual(len(reused),2)
        self.assertNotEqual(groups[reused[0]],groups[reused[1]])
        self.assertEqual(network['paths'],[])

    def test_real_source_pattern_calls_do_not_merge_opposite_or_short_trip_platforms(self):
        city=OstravaNetex(DAY,'2026-11-01');city.archive(fixture(108));network,report=city.export()
        calls={};groups={}
        for stop in network['stops']:
            self.assertIn('sourceCall',stop)
            self.assertFalse(stop['physicalPlatformVerified'])
            call=stop['sourceCall'];identity=(call['journeyPatternRef'],call['pointInJourneyPatternRef'])
            self.assertNotIn(identity,calls);calls[identity]=stop['id']
            groups.setdefault(stop['group'],[]).append(stop)
        split=[stops for stops in groups.values() if len(stops)>1]
        self.assertTrue(split)
        self.assertTrue(any(len({s['sourceCall']['journeyPatternRef'] for s in stops})>1 for stops in split))
        for stops in split:
            self.assertEqual(len({s['name'] for s in stops}),1)
            self.assertEqual(len({s['sourceStopId'] for s in stops}),1)
        self.assertEqual(network['paths'],[])
        self.assertEqual(report['isolatedCisCallContexts'],len(network['stops']))
        self.assertEqual(report['physicalPlatformMappingsVerified'],0)

    def test_pattern_direction_change_keeps_call_ids_but_invalidates_old_platform_fingerprints(self):
        root=ET.fromstring(gzip.decompress(fixture(108).read_bytes()))
        before=OstravaNetex(DAY,'2026-11-01');before.add(copy.deepcopy(root));left,_=before.export()
        next(n for n in descendants(root,'ScheduledStopPoint') if first(n,'Name') is not None).find('{*}Name').text='Changed remote endpoint'
        after=OstravaNetex(DAY,'2026-11-01');after.add(root);right,_=after.export()
        old={s['id']:s for s in left['stops']};new={s['id']:s for s in right['stops']}
        self.assertEqual(old.keys(),new.keys())
        self.assertTrue(any(old[key]['name']==new[key]['name'] and old[key]['sourceCall']['patternSha256']!=new[key]['sourceCall']['patternSha256'] for key in old))

    def test_reviewed_conflicting_offers_are_order_independent(self):
        results=[]
        for order in [(3022,3275),(3275,3022)]:
            city=OstravaNetex(DAY,'2026-11-01')
            for doc in order:city.archive(fixture(doc))
            network,report=city.export()
            trip=next(t for t in network['trips'] if t[4]=='68')
            self.assertEqual(trip[5][0][2],25740)
            self.assertEqual(report['reviewedOffersExcluded'],1)
            self.assertEqual(report['referenceCheckCompleteness'],'selected_departures_only')
            results.append(network['sha256'])
        self.assertEqual(*results)

    def test_daily_archive_renumbering_keeps_the_same_reviewed_offer(self):
        results=[]
        for documents in [(3022,3275),(3275,3022)]:
            city=OstravaNetex(DAY,'2026-11-01')
            for document in documents:
                root=ET.fromstring(gzip.decompress(fixture(document).read_bytes()))
                first(root,'PublicationTimestamp').text='2026-10-26T11:00:00Z'
                renamed='NX-PI-01_CZ_CISJR-JDF_LINE-'+str(document+10000)+'_20261026.xml'
                city.add(root,renamed)
            network,report=city.export()
            results.append(network['sha256'])
            self.assertEqual(report['reviewedOffersExcluded'],1)
            self.assertEqual(next(t for t in network['trips'] if t[4]=='68')[5][0][2],25740)
        self.assertEqual(*results)

    def test_reviewed_source_change_or_missing_preferred_offer_blocks_export(self):
        city=OstravaNetex(DAY,'2026-11-01');city.archive(fixture(3022))
        with self.assertRaisesRegex(ImportErrorDetail,'absent'):city.export()
        root=ET.fromstring(gzip.decompress(fixture(357).read_bytes()))
        descendants(root,'DepartureTime')[0].text='03:00:00'
        city=OstravaNetex(DAY,'2026-11-01')
        with self.assertRaisesRegex(ImportErrorDetail,'semantics changed'):city.add(root,str(fixture(357)))
        self.assertEqual(city.network['trips'],[])
        self.assertEqual(city.network['stops'],[])

    def test_newer_complete_validity_supersedes_the_old_timetable(self):
        results=[]
        for order in [(191,679),(679,191)]:
            city=OstravaNetex(DAY,'2026-11-01')
            for doc in order:city.archive(fixture(doc))
            network,report=city.export()
            self.assertEqual(len(network['trips']),78)
            self.assertEqual(report['modes'],[11])
            self.assertGreater(report['supersededServiceDays'],0)
            results.append(network['sha256'])
        self.assertEqual(*results)

    def test_failed_document_rolls_back_every_partial_trip_and_stop(self):
        root=ostrava('bus');journey=descendants(root,'ServiceJourney')[0]
        broken=copy.deepcopy(journey);broken.set('id','BROKEN')
        descendants(broken,'StopPointInJourneyPatternRef')[0].set('ref','UNKNOWN')
        descendants(root,'vehicleJourneys')[0].append(broken)
        city=OstravaNetex(DAY,DAY)
        with self.assertRaises(ImportErrorDetail):city.add(root)
        self.assertEqual(city.network['trips'],[]);self.assertEqual(city.network['stops'],[])
        self.assertEqual(city.network['routes'],[]);self.assertEqual(city.journeys,{})
        self.assertTrue(all(not values for values in city.network['active'].values()))

    def test_unrelated_large_city_does_not_abort_the_ostrava_scope(self):
        header=b'<PublicationDelivery><ParticipantRef>CZ:cisjr</ParticipantRef><ResourceFrame id="CZ:cisjr_jdf:ResourceFrame-EU_PI_COMMON"><Operator><PublicCode>11111111</PublicCode><LegalName>TEST Other operator</LegalName></Operator></ResourceFrame>'
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'netex.zip'
            with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('other.xml',header+b' '*32_000_100+b'</PublicationDelivery>')
            self.assertEqual(list(archive_documents(path)),[])

    def test_night_inference_does_not_hide_a_backwards_daytime_time(self):
        rows=[[0,36000,36000,0,0,1,1],[1,35000,35000,0,0,2,1]]
        with self.assertRaisesRegex(ImportErrorDetail,'backwards'):unroll_cis_midnight(rows)
        rows=[[0,85800,85800,0,0,1,1],[1,86340,60,0,0,2,1],[2,180,180,0,0,3,1]]
        result=unroll_cis_midnight(rows)
        self.assertEqual(result[1][1:3],[86340,86460]);self.assertEqual(result[-1][1],86580)

if __name__=='__main__':unittest.main()
