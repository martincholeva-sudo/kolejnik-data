import copy
import json
from pathlib import Path
import tempfile
import unittest
from national_fixture import DAY
from national.common import blank, digest, finalize, ImportErrorDetail
from national.locations import load_catalog, apply_locations
from national.station_locations import (DEFAULT_REGISTER, DEFAULT_NAME_ALIASES, PASSENGER_QUALIFIERS,
    apply_station_locations, gps, load_register, name_key, station_code)

ROOT = Path(__file__).resolve().parents[1]


class StationLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.rows = load_register()
        cls.source_stops = json.loads((ROOT/'tests/fixtures/priority-station-source-stops.json').read_text(encoding='utf-8'))['stops']

    def network(self):
        n = blank(DAY, '2026-11-03', 'synthetic service with retained station metadata')
        n['stops'] = copy.deepcopy(self.source_stops)
        for code, name in [('34364','Ostrava hl. n.'),('34124','Bohumín')]:
            n['stops'].append({'id':'czrail:CZ:'+code, 'name':name, 'lat':0, 'lon':0,
                'type':0, 'parent':'', 'zone':'', 'platform':'', 'group':'czrail:CZ:'+code,
                'source':'cis-rail', 'coordinatesKnown':False})
        n['stops'].append({**n['stops'][-1], 'id':'czrail:CZ:34124:replacement:test',
            'sourceStopId':'czrail:CZ:34124', 'boardingMode':'replacement'})
        n['routes'] = [['synthetic-route','test',2]]
        n['trips'] = [['synthetic-trip','daily',0,'test','',
            [[i,36000+i*600,36000+i*600,0,0,i+1,1] for i in range(6)]]]
        n['active'] = {day:['daily'] for day in n['active']}
        return finalize(n)

    def test_every_actual_source_identifier_has_a_valid_check_digit(self):
        self.assertEqual(len(self.rows),4367)
        self.assertTrue(all(len(station_code(row['sr70']))==5 for row in self.rows))
        self.assertEqual(station_code('570762'),'57076')
        self.assertEqual(station_code('341248'),'34124')
        self.assertEqual(station_code('3012'),'00301')

    def test_hemispheres_decimal_commas_and_scope_are_checked(self):
        self.assertEqual(gps('N49,851730°','N'),49.85173)
        self.assertEqual(gps('E18,266873°','E'),18.266873)
        for value,hemisphere in [('E49,851730°','N'),('N18,266873°','E'),('N0,000000°','N'),('Nnan°','N')]:
            with self.assertRaises(ImportErrorDetail):gps(value,hemisphere)
        for value in ['570761','570762.0','code570762']:
            with self.assertRaises(ImportErrorDetail):station_code(value)

    def test_formatting_normalization_preserves_accents_and_words(self):
        self.assertEqual(name_key('Praha hl. n.'),name_key('Praha hl.n.'))
        self.assertNotEqual(name_key('Praha hlavní nádraží'),name_key('Praha hl.n.'))
        self.assertNotEqual(name_key('Děčín'),name_key('Decin'))

    def test_reference_locations_preserve_all_service_and_search_fields(self):
        n=self.network();before=copy.deepcopy(n)
        result,report=apply_station_locations(n,self.manifest,self.rows)
        self.assertEqual(n,before)
        for key in ['trips','routes','active','paths','transfers']:self.assertEqual(result[key],n[key])
        self.assertEqual([(s['id'],s['group'],s['name']) for s in result['stops']],
                         [(s['id'],s['group'],s['name']) for s in n['stops']])
        self.assertEqual(report['newStationReferenceLocations'],6)
        self.assertEqual(result['coverage']['stopsWithKnownCoordinates'],6)
        ostrava=next(s for s in result['stops'] if s['id']=='czrail:CZ:34364')
        self.assertEqual((ostrava['lat'],ostrava['lon']),(49.85173,18.266873))
        self.assertTrue(all(not s.get('physicalPlatformVerified') for s in result['stops']))
        self.assertEqual(report['walkingEdgesAdded'],0)
        self.assertEqual(report['platformAliasesAdded'],0)

    def test_reviewed_osm_positions_and_svinov_endpoint_fingerprint_survive(self):
        catalog,geometry=load_catalog(ROOT/'config/priority-location-mappings.json')
        n=apply_locations(self.network(),catalog,geometry,'cis-rail')
        before={s['id']:copy.deepcopy(s) for s in n['stops'] if s['coordinatesKnown']}
        result,report=apply_station_locations(n,self.manifest,self.rows)
        after={s['id']:s for s in result['stops']}
        self.assertEqual(report['preexistingLocationsPreserved'],4)
        self.assertEqual(report['newStationReferenceLocations'],2)
        for key,stop in before.items():self.assertEqual(after[key],stop)
        self.assertEqual(digest(after['czrail:CZ:34434']),digest(before['czrail:CZ:34434']))

    def test_nad_and_registered_road_stops_cannot_inherit_station_coordinates(self):
        n=self.network()
        road=next(r for r in self.rows if r['state']=='1' and r['qualifier']=='65')
        code=station_code(road['sr70'])
        n['stops'].append({**n['stops'][0], 'id':'czrail:CZ:'+code, 'group':'czrail:CZ:'+code,
            'name':road['name'], 'coordinatesKnown':False})
        result,report=apply_station_locations(n,self.manifest,self.rows)
        self.assertFalse(result['stops'][-1]['coordinatesKnown'])
        self.assertFalse(next(s for s in result['stops'] if s.get('boardingMode')=='replacement')['coordinatesKnown'])
        self.assertEqual(report['nadContextsNotAssignedStationCoordinates'],1)
        self.assertIn('not-a-reviewed-passenger-station-or-halt',report['unmappedReasons'])

    def test_wrong_name_or_inactive_register_record_leaves_services_available(self):
        n=self.network();n['stops'][4]['name']='Bohumín'
        inactive=next(r for r in self.rows if r['state']=='4' and r['qualifier']=='1')
        code=station_code(inactive['sr70'])
        n['stops'].append({**n['stops'][0], 'id':'czrail:CZ:'+code, 'group':'czrail:CZ:'+code,
            'name':inactive['name'], 'coordinatesKnown':False})
        result,report=apply_station_locations(n,self.manifest,self.rows)
        self.assertFalse(result['stops'][4]['coordinatesKnown'])
        self.assertFalse(result['stops'][-1]['coordinatesKnown'])
        self.assertEqual(result['trips'],n['trips'])
        self.assertIn('source-name-needs-review',report['unmappedReasons'])
        self.assertIn('register-not-active',report['unmappedReasons'])

    def test_station_reference_cannot_paint_a_specific_verified_platform(self):
        n=self.network();n['stops'][4]['physicalPlatformVerified']=True
        n['stops'][4]['physicalPlatformId']='reviewed-specific-platform'
        result,report=apply_station_locations(n,self.manifest,self.rows)
        self.assertFalse(result['stops'][4]['coordinatesKnown'])
        self.assertIn('specific-platform-location-required',report['unmappedReasons'])

    def test_expired_naive_or_unreviewed_source_is_rejected_without_mutation(self):
        for field,value in [('validUntil','2026-10-25'),('observedAt','2026-10-27T13:00:00'),
                            ('sourceSha256','0'*64)]:
            n=self.network();before=copy.deepcopy(n);manifest={**self.manifest,field:value}
            with self.assertRaises(ImportErrorDetail):apply_station_locations(n,manifest,self.rows)
            self.assertEqual(n,before)

    def test_source_part_checksums_and_paths_are_checked(self):
        for kind in ['checksum','path']:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);manifest=copy.deepcopy(self.manifest)
                if kind=='checksum':
                    item=manifest['parts'][0]
                    (root/item['file']).write_bytes((DEFAULT_REGISTER/item['file']).read_bytes())
                    item['sha256']='0'*64
                else:manifest['parts'][0]['file']='../outside.json'
                (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
                with self.assertRaises(ImportErrorDetail):load_register(root)

    def test_abstract_same_name_transport_node_is_not_a_passenger_platform(self):
        node=next(r for r in self.rows if r['name']=='Bohumín' and r['qualifier']=='13')
        n=self.network();code=station_code(node['sr70'])
        n['stops'].append({**n['stops'][0], 'id':'czrail:CZ:'+code,'group':'czrail:CZ:'+code,
            'name':node['name'], 'coordinatesKnown':False})
        result,report=apply_station_locations(n,self.manifest,self.rows)
        self.assertFalse(result['stops'][-1]['coordinatesKnown'])
        self.assertIn('not-a-reviewed-passenger-station-or-halt',report['unmappedReasons'])

    def test_all_active_station_and_halt_reference_values_are_parseable(self):
        rows=[row for row in self.rows if row['state']=='1' and row['qualifier'] in PASSENGER_QUALIFIERS]
        self.assertEqual(len(rows),2859)
        for row in rows:
            self.assertEqual(len(station_code(row['sr70'])),5)
            self.assertTrue(48.4<=gps(row['latitude'],'N')<=51.2)
            self.assertTrue(12.0<=gps(row['longitude'],'E')<=19.0)

    def test_explicit_source_bound_aliases_cover_abbreviations_without_fuzzy_matching(self):
        stops=json.loads((ROOT/'tests/fixtures/rail-station-reference-alias-stops.json').read_text(encoding='utf-8'))['stops']
        n=self.network();n['stops'].append(next(s for s in stops if s['id']=='czrail:CZ:73275'))
        result,report=apply_station_locations(n,self.manifest,self.rows)
        self.assertTrue(result['stops'][-1]['coordinatesKnown'])
        self.assertEqual(report['reviewedNameAliasesApplied'],1)
        self.assertEqual(result['stops'][-1]['name'],'Plzeň hl. n.')
        self.assertEqual(result['stops'][-1]['coordinateEvidence']['validUntil'],'2026-11-03')

    def test_changed_or_expired_alias_cannot_silently_place_a_station(self):
        stops=json.loads((ROOT/'tests/fixtures/rail-station-reference-alias-stops.json').read_text(encoding='utf-8'))['stops']
        for kind in ['source','row','expiry']:
            n=self.network();n['stops'].append(copy.deepcopy(next(s for s in stops if s['id']=='czrail:CZ:73275')))
            catalog=json.loads(DEFAULT_NAME_ALIASES.read_text(encoding='utf-8'))
            if kind=='source':n['stops'][-1]['zone']='different source metadata'
            elif kind=='row':next(a for a in catalog['aliases'] if a['stopId']=='czrail:CZ:73275')['registerRowSha256']='0'*64
            else:catalog['reviewedUntil']='2026-10-28'
            result,report=apply_station_locations(n,self.manifest,self.rows,catalog)
            self.assertFalse(result['stops'][-1]['coordinatesKnown'])
            self.assertEqual(report['reviewedNameAliasesApplied'],0)

    def test_registered_track_group_can_reference_an_existing_public_cis_stop(self):
        row=next(r for r in self.rows if r['sr70'].startswith('57261'))
        self.assertEqual(row['qualifier'],'8')
        n=self.network();n['stops'].append({**n['stops'][0],'id':'czrail:CZ:57261','group':'czrail:CZ:57261','name':'Praha-Eden','coordinatesKnown':False})
        result,report=apply_station_locations(n,self.manifest,self.rows)
        self.assertTrue(result['stops'][-1]['coordinatesKnown'])
        self.assertFalse(result['stops'][-1].get('physicalPlatformVerified',False))
        self.assertEqual(report['walkingEdgesAdded'],0)

    def test_common_ostrava_name_does_not_rename_the_registered_coal_yard(self):
        stops=json.loads((ROOT/'tests/fixtures/rail-station-reference-alias-stops.json').read_text(encoding='utf-8'))['stops']
        n=self.network();n['stops'].append(next(s for s in stops if s['id']=='czrail:CZ:38034'))
        result,report=apply_station_locations(n,self.manifest,self.rows)
        self.assertFalse(result['stops'][-1]['coordinatesKnown'])
        self.assertIn('source-name-needs-review',report['unmappedReasons'])
