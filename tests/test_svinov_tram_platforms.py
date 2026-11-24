import copy
import json
from pathlib import Path
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from national.common import blank, finalize, ImportErrorDetail
from national.platforms import apply_platform_mappings
from national.bundle import compose

ROOT = Path(__file__).resolve().parents[1]


class SvinovTramPlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / 'tests/fixtures/svinov-tram-source-calls.json').read_text(encoding='utf-8'))
        cls.catalog = json.loads((ROOT / 'config/svinov-tram-platform-mappings.json').read_text(encoding='utf-8'))

    def source(self):
        n = blank('2026-10-26', '2026-11-03', 'Retained exact public CIS source calls')
        n['stops'] = [copy.deepcopy(row['stop']) for row in self.fixture['calls']]
        return finalize(n)

    def test_both_directions_of_all_seven_source_lines_receive_the_reviewed_labels(self):
        before = self.source()
        result = apply_platform_mappings(before, self.catalog)
        indexed = {stop['id']: stop for stop in result['stops']}
        self.assertEqual(46, len(indexed))
        self.assertEqual({'3','4','7','8','11','17','19'}, {row['line'] for row in self.fixture['calls']})
        for row in self.fixture['calls']:
            expected = 'H1' if row['nextName'] == 'Ostrava,Třebovice,OC' else 'H2'
            self.assertEqual(expected, indexed[row['stop']['id']]['platform'])
            self.assertEqual(row['platform'], expected)
        for field in ['trips', 'routes', 'active', 'paths', 'transfers']:
            self.assertEqual(before[field], result[field])
        for left, right in zip(before['stops'], result['stops']):
            for field in ['id','name','group','lat','lon','coordinatesKnown','sourceCall']:
                self.assertEqual(left.get(field), right.get(field))

    def test_same_local_source_code_does_not_merge_opposite_directions(self):
        result = apply_platform_mappings(self.source(), self.catalog)
        labels = {}
        for stop in result['stops']:
            labels.setdefault(stop['sourceCall']['scheduledStopPointRef'], set()).add(stop['platform'])
        self.assertEqual(7, len(labels))
        self.assertTrue(all(value == {'H1', 'H2'} for value in labels.values()))

    def test_only_same_platform_aliases_are_built_and_two_minute_minimum_is_kept(self):
        source = apply_platform_mappings(self.source(), self.catalog)
        network = compose([('ostrava', source)], source['start'], source['end'])
        self.assertEqual(20*19 + 26*25, len(network['paths']))
        self.assertEqual(len(network['paths']), len(network['transfers']))
        for a, b, seconds, estimated in network['paths']:
            self.assertEqual(source['stops'][a]['platform'], source['stops'][b]['platform'])
            self.assertEqual(0, seconds)
            self.assertFalse(estimated)
        self.assertTrue(all(row[2:4] == [2,120] for row in network['transfers']))
        self.assertEqual([], network['interchangeEvidence'])

    def test_changed_full_pattern_cannot_reuse_the_retained_mapping(self):
        source = self.source()
        source['stops'][-1]['sourceCall']['patternSha256'] = '0' * 64
        with self.assertRaisesRegex(ImportErrorDetail, 're-verification'):
            apply_platform_mappings(source, self.catalog)

    def test_separate_catalogs_preserve_prior_labels_counts_and_review_evidence(self):
        first = copy.deepcopy(self.catalog)
        second = copy.deepcopy(self.catalog)
        first['calls'] = first['calls'][:1]
        second['calls'] = second['calls'][1:]
        second['reviews'][0]['id'] += '-second'
        for call in second['calls']:
            call['reviewId'] = second['reviews'][0]['id']
        once = apply_platform_mappings(self.source(), first)
        twice = apply_platform_mappings(once, second)
        self.assertEqual(46, twice['coverage']['physicalPlatformMappingsVerified'])
        self.assertEqual(46, twice['platformMappingEvidence']['callCount'])
        self.assertEqual(2, len(twice['platformMappingEvidence']['reviews']))
        self.assertEqual(once['stops'][0], twice['stops'][0])
        self.assertEqual([], twice['paths'])

    def test_supplement_cannot_relabel_an_existing_physical_platform(self):
        h1 = next(c for c in self.catalog['calls'] if c['platform'] == 'H1')
        h2 = next(c for c in self.catalog['calls'] if c['platform'] == 'H2')
        first = copy.deepcopy(self.catalog); first['calls'] = [h1]
        second = copy.deepcopy(self.catalog); second['calls'] = [copy.deepcopy(h2)]
        second['reviews'][0]['id'] += '-second'
        second['calls'][0]['reviewId'] = second['reviews'][0]['id']
        second['calls'][0]['physicalPlatformId'] = h1['physicalPlatformId']
        once = apply_platform_mappings(self.source(), first)
        with self.assertRaisesRegex(ImportErrorDetail, 'conflicting labels'):
            apply_platform_mappings(once, second)

    def test_expired_review_cannot_create_same_platform_transfers(self):
        source = self.source(); source['end'] = '2026-11-04'
        with self.assertRaisesRegex(ImportErrorDetail, 'horizon'):
            apply_platform_mappings(source, self.catalog)
