import copy
import gzip
import json
import unittest
from pathlib import Path
from national.pedestrian import analyse_svinov

class SvinovPedestrianTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/streets/svinov-osm-20261027.json.gz').read_bytes()))
    def test_all_four_real_platforms_connect_without_unknown_escalator_directions(self):
        result=analyse_svinov(self.source)
        self.assertEqual(set(result['railPlatformGroundAccess']),{'1','2','3','4'})
        self.assertEqual(len(result['results']),6)
        self.assertTrue(all(r.get('conservativeWalkingSeconds',0)>0 for r in result['results']))
        excluded=set(result['excludedEscalatorsOrRestrictedWays'])
        self.assertTrue(excluded)
        for row in result['results']:
            self.assertTrue(all(e['way'] not in excluded for e in row['wayWitness']))
        self.assertTrue(all(p['distanceMetres']<=5 for p in result['lastMetresAnchors']))
    def test_closing_both_underpasses_cannot_be_repaired_by_a_cross_track_shortcut(self):
        source=copy.deepcopy(self.source)
        for e in source['elements']:
            if e['type']=='way' and e['id'] in [65617884,65629129]:e['tags']['foot']='no'
        result=analyse_svinov(source)
        missing=[r for r in result['results'] if 'unreachableRailPlatforms' in r]
        self.assertTrue(missing)
        self.assertTrue(all({'2','3','4'}<=set(r['unreachableRailPlatforms']) for r in missing))
