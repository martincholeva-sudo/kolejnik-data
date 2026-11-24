import copy
import gzip
import json
from pathlib import Path
import unittest
from national.common import blank, finalize, digest, ImportErrorDetail
from national.platforms import apply_platform_mappings
from national.pedestrian import analyse_svinov
from audit_svinov_tram_pedestrian import audit
from build_svinov_tram_interchanges import build

ROOT = Path(__file__).resolve().parents[1]

class SvinovFixedStairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT/'tests/fixtures/streets/svinov-full-osm-20261027.json.gz'
        cls.source = json.loads(gzip.decompress(cls.path.read_bytes()))
        cls.access = json.loads((ROOT/'config/svinov-fixed-stair-connections.json').read_text(encoding='utf-8'))
        cls.report = audit(cls.path, cls.access)
        cls.catalog = json.loads((ROOT/'config/svinov-tram-platform-mappings.json').read_text(encoding='utf-8'))
        fixture = json.loads((ROOT/'tests/fixtures/svinov-tram-source-calls.json').read_text(encoding='utf-8'))
        dpo = blank('2026-10-26','2026-11-03','CIS test subset')
        dpo['stops'] = [row['stop'] for row in fixture['calls']]
        cls.dpo = apply_platform_mappings(finalize(dpo), cls.catalog)
        for i in range(6):
            cls.dpo['stops'].append({'id':'bus:'+str(i),'platform':'N1' if i<3 else 'N2',
                'physicalPlatformVerified':True,'sourceCall':{'scheduledStopPointRef':'CZ:cisjr_jdf:ScheduledStopPoint:915037__13'}})
        cls.rail = {**blank('2026-10-26','2026-11-03','CIS test subset'),
                    'stops':[{'id':'czrail:CZ:34434','name':'Ostrava-Svinov'}]}
        cls.previous = {'schema':1,'links':[{'from':'prior:'+str(i),'to':'prior:other','seconds':720} for i in range(28)]}

    def test_twenty_routes_use_explicit_plan_stairs_without_mechanical_access(self):
        report = self.report
        self.assertEqual(20,len(report['results']))
        self.assertFalse(report['liftsUsed']); self.assertFalse(report['escalatorsUsed'])
        excluded=set(report['excludedEscalatorsOrRestrictedWays'])
        for row in report['results']:
            self.assertGreater(row['conservativeWalkingSeconds'],0)
            stairs=[e for e in row['wayWitness'] if e['kind']=='plan-fixed-stair-connection']
            if 'tram' in row['direction']:
                self.assertTrue(stairs)
                self.assertTrue(row['pathMetresExcludeSchematicStairConnections'])
            for edge in stairs:
                self.assertEqual(0,edge['metres']); self.assertEqual(120,edge['seconds'])
                self.assertEqual('plan-topology-not-surveyed-polyline',edge['geometry'])
            self.assertTrue(all(e['way'] not in excluded for e in row['wayWitness']))
        times={r['platform']:r['conservativeWalkingSeconds'] for r in report['results'] if r['direction']=='rail-to-tram'}
        self.assertEqual({'H1':1050,'H2':1020},times)

    def test_original_bus_routes_remain_identical(self):
        old=analyse_svinov(self.source)['results']
        current=[{k:v for k,v in row.items() if k!='pathMetresExcludeSchematicStairConnections'} for row in self.report['results'] if 'tram' not in row['direction']]
        self.assertEqual(old,current)

    def test_changed_source_and_wrong_platform_anchor_require_review(self):
        source=copy.deepcopy(self.source); source['elements'][0]['lat']+=.000001
        with self.assertRaisesRegex(ImportErrorDetail,'source changed'):
            analyse_svinov(source,self.access)
        access=copy.deepcopy(self.access); access['connections'][0]['upperNode']=2420344719
        with self.assertRaisesRegex(ImportErrorDetail,'anchors'):
            analyse_svinov(self.source,access)

    def test_floor_and_duration_validation_rejects_zero_and_boolean_costs(self):
        for key,value in [('upperLevel','0'),('seconds',0),('seconds',True),('bidirectional',False)]:
            access=copy.deepcopy(self.access); access['connections'][0][key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ImportErrorDetail):
                analyse_svinov(self.source,access)

    def test_no_cross_track_shortcut_when_both_underpasses_close(self):
        source=copy.deepcopy(self.source)
        for row in source['elements']:
            if row['type']=='way' and row['id'] in [65617884,65629129]: row['tags']['foot']='no'
        access=copy.deepcopy(self.access); access['sourceContentSha256']=digest(source)
        report=analyse_svinov(source,access)
        rows=[r for r in report['results'] if 'rail' in r['direction']]
        self.assertTrue(all({'2','3','4'}<=set(r['unreachableRailPlatforms']) for r in rows))

    def test_closed_ground_stairs_do_not_fall_back_to_escalators_or_lifts(self):
        source=copy.deepcopy(self.source)
        for row in source['elements']:
            if row['type']=='way' and row.get('tags',{}).get('highway')=='steps' and row['tags'].get('level')=='0;1': row['tags']['foot']='no'
        access=copy.deepcopy(self.access); access['sourceContentSha256']=digest(source)
        with self.assertRaisesRegex(ImportErrorDetail,'disconnected'):
            analyse_svinov(source,access)

    def test_compiler_preserves_prior_links_and_excludes_same_platform_and_nad(self):
        rail=copy.deepcopy(self.rail)
        rail['stops'].append({'id':'nad:svinov','name':'Ostrava-Svinov','boardingMode':'replacement'})
        result=build(rail,self.dpo,self.report,self.catalog,self.previous)
        self.assertEqual(self.previous['links'],result['links'][:28]); self.assertEqual(1712,len(result['links']))
        self.assertTrue(all('nad:' not in r['from'] and 'nad:' not in r['to'] for r in result['links']))
        stops={s['id']:s for s in self.dpo['stops']}
        for row in result['links'][28:]:
            self.assertTrue(row['estimated']); self.assertFalse(row['durationMeasured'])
            source=rail['stops'][0] if row['from']=='czrail:CZ:34434' else stops[row['from']]
            self.assertEqual(digest(source),row['fromStopSha256'])
            if row['from'] in stops and row['to'] in stops:
                self.assertNotEqual(stops[row['from']]['platform'],stops[row['to']]['platform'])

    def test_expired_access_or_changed_exact_source_pattern_cannot_build_links(self):
        rail=copy.deepcopy(self.rail); rail['end']='2026-11-04'
        dpo=copy.deepcopy(self.dpo); dpo['end']='2026-11-04'
        with self.assertRaisesRegex(ImportErrorDetail,'expired'):
            build(rail,dpo,self.report,self.catalog,self.previous)
        dpo=copy.deepcopy(self.dpo); dpo['stops'][0]['sourceCall']['patternSha256']='0'*64
        with self.assertRaisesRegex(ImportErrorDetail,'re-verification'):
            build(self.rail,dpo,self.report,self.catalog,self.previous)
