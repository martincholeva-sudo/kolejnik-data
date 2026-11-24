import copy
import json
import unittest
from pathlib import Path
from national_fixture import DAY
from national.common import ImportErrorDetail
from national.ostrava import OstravaNetex
from national.platforms import apply_platform_mappings, stop_fingerprint

class PlatformMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Original CIS document, retained and checksum-tested by RealOstravaTests.
        source=next((Path(__file__).parent/'fixtures/netex').glob('*LINE-108_*.xml.gz'))
        city=OstravaNetex(DAY,'2026-11-01');city.archive(source);cls.source,_=city.export()
    def fixture(self):
        n=copy.deepcopy(self.source);stop=n['stops'][0]
        catalog={'schema':1,'reviews':[{'id':'review','evidence':'https://example.test/original-plan.pdf','verified':True,'verifiedAt':'2026-10-27T11:00:00Z','validFrom':'2026-10-20'}],
                 'calls':[{'stopId':stop['id'],'stopSha256':stop_fingerprint(stop),'physicalPlatformId':'physical:fixture:N1','platform':'N1','reviewId':'review'}]}
        return n,catalog
    def test_labels_keep_source_calls_clocks_calendars_search_groups_and_unknown_coordinates(self):
        n,catalog=self.fixture();before=copy.deepcopy(n);result=apply_platform_mappings(n,catalog)
        self.assertEqual(n,before)
        for field in ['trips','routes','active','paths','transfers']:self.assertEqual(result[field],before[field])
        self.assertEqual(result['stops'][0]['platform'],'N1');self.assertTrue(result['stops'][0]['physicalPlatformVerified'])
        self.assertEqual(result['coverage']['physicalPlatformMappingsVerified'],1)
        self.assertEqual([(s['id'],s['group'],s['name'],s['lat'],s['lon'],s['coordinatesKnown']) for s in result['stops']],[(s['id'],s['group'],s['name'],s['lat'],s['lon'],s['coordinatesKnown']) for s in before['stops']])
        self.assertEqual(result['platformMappingEvidence']['walkingPathsAdded'],0)
    def test_changed_source_call_or_pattern_blocks_old_platform_without_mutating_input(self):
        for field in ['name','direction-pattern']:
            n,catalog=self.fixture()
            if field=='name':n['stops'][0]['name']='Different source stop'
            else:n['stops'][0]['sourceCall']['patternSha256']='0'*64
            before=copy.deepcopy(n)
            with self.assertRaisesRegex(ImportErrorDetail,'re-verification'):apply_platform_mappings(n,catalog)
            self.assertEqual(n,before)
    def test_unreviewed_naive_expired_or_future_horizon_evidence_is_rejected(self):
        for field,value in [('verified',False),('verifiedAt','2026-10-27T11:00:00'),('validUntil','2026-10-25'),('validFrom','2026-11-02'),('reviewedUntil','2026-10-25'),('reviewedFrom','2026-11-02')]:
            n,catalog=self.fixture();catalog['reviews'][0][field]=value
            with self.assertRaises(ImportErrorDetail):apply_platform_mappings(n,catalog)
    def test_missing_duplicate_foreign_and_conflicting_calls_cannot_be_partially_applied(self):
        for kind in ['missing','duplicate','foreign','labels']:
            n,catalog=self.fixture()
            if kind=='missing':catalog['calls'][0]['stopId']='unknown'
            elif kind=='duplicate':catalog['calls'].append(copy.deepcopy(catalog['calls'][0]))
            elif kind=='foreign':n['stops'][0]['source']='pid'
            else:
                stop=n['stops'][1];catalog['calls'].append({**catalog['calls'][0],'stopId':stop['id'],'stopSha256':stop_fingerprint(stop),'platform':'N2'})
            before=copy.deepcopy(n)
            with self.assertRaises(ImportErrorDetail):apply_platform_mappings(n,catalog)
            self.assertEqual(n,before)
