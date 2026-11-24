"""Apply reviewed source-bound reference locations without inventing footpaths."""
import datetime as dt
import gzip
import json
import math
import re
from pathlib import Path
from .common import ImportErrorDetail, digest, finalize, sha_file


def objects(geometry):
    result = {}
    for item in geometry.get('elements', []):
        key = f"{item.get('type')}:{item.get('id')}"
        if key in result:
            raise ImportErrorDetail('Duplicate location geometry object')
        result[key] = item
    return result


def coordinate(item, indexed):
    """Nodes are exact references; ways use a point halfway along their geometry."""
    def point(node):
        lat, lon = node.get('lat'), node.get('lon')
        if (isinstance(lat, bool) or isinstance(lon, bool)
                or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))
                or not math.isfinite(lat) or not math.isfinite(lon)
                or not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0)):
            raise ImportErrorDetail('Invalid reference coordinate')
        return lat, lon
    if item.get('type') == 'node':
        return point(item)
    if item.get('type') != 'way' or len(item.get('nodes', [])) < 2:
        raise ImportErrorDetail('Unsupported or incomplete platform geometry')
    try:
        points = [point(indexed[f'node:{key}']) for key in item['nodes']]
    except KeyError as error:
        raise ImportErrorDetail('Missing platform geometry node') from error
    lengths = [math.hypot((b[0]-a[0])*111200, (b[1]-a[1])*111200*math.cos(math.radians((a[0]+b[0])/2))) for a,b in zip(points,points[1:])]
    half = sum(lengths)/2
    if half <= 0:
        raise ImportErrorDetail('Degenerate platform geometry')
    for a,b,length in zip(points,points[1:],lengths):
        if half <= length:
            fraction = half/length
            return tuple(round(x+(y-x)*fraction, 7) for x,y in zip(a,b))
        half -= length
    return points[-1]


def route_calls(relation):
    """Accept the reviewed alternating stop/platform member form, with missing platforms explicit."""
    result = []
    for member in relation.get('members', []):
        role = member.get('role', '')
        if role in ('stop', 'stop_entry_only', 'stop_exit_only'):
            result.append({'stop': member, 'platform': None})
        elif role in ('platform', 'platform_entry_only', 'platform_exit_only'):
            if not result or result[-1]['platform'] is not None:
                raise ImportErrorDetail('Ambiguous route platform membership')
            result[-1]['platform'] = member
    return result


def load_catalog(path):
    path = Path(path)
    catalog = json.loads(path.read_text(encoding='utf-8-sig'))
    source = path.parent / catalog['geometryFile']
    if sha_file(source) != catalog.get('geometryFileSha256'):
        raise ImportErrorDetail('Location source bytes changed; re-review required')
    raw = source.read_bytes()
    geometry = json.loads((gzip.decompress(raw) if source.suffix == '.gz' else raw).decode('utf-8'))
    return catalog, geometry


def apply_locations(network, catalog, geometry, source):
    if (not isinstance(catalog, dict) or catalog.get('schema') != 1
            or not isinstance(catalog.get('locations'), list) or not isinstance(geometry, dict)
            or geometry.get('schema') != 1 or digest(geometry) != catalog.get('geometrySha256')):
        raise ImportErrorDetail('Invalid or changed location catalog/source')
    review = catalog.get('review', {})
    try:
        stamp = dt.datetime.fromisoformat(review['verifiedAt'].replace('Z', '+00:00'))
        first = dt.date.fromisoformat(review['reviewedFrom'])
        last = dt.date.fromisoformat(review['reviewedUntil'])
        if (review.get('verified') is not True or stamp.tzinfo is None
                or first > dt.date.fromisoformat(network['start'])
                or last < dt.date.fromisoformat(network['end'])):
            raise ValueError('Location review does not cover the horizon')
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ImportErrorDetail('Invalid or expired location review') from error
    indexed = objects(geometry)
    stops = {stop['id']: stop for stop in network['stops']}
    changed = {}
    for mapping in catalog['locations']:
        if not isinstance(mapping, dict):
            raise ImportErrorDetail('Invalid reference location mapping')
        if mapping.get('source') != source:
            continue
        key = mapping.get('stopId')
        if not isinstance(key, str) or key not in stops or key in changed:
            raise ImportErrorDetail('Missing or duplicate reference location call')
        stop = stops[key]
        if stop.get('source') != source or digest(stop) != mapping.get('stopSha256'):
            raise ImportErrorDetail('Reference source stop changed; re-review required')
        if not isinstance(mapping.get('osmObject'), str):
            raise ImportErrorDetail('Invalid source geometry identity')
        item = indexed.get(mapping['osmObject'])
        if item is None or digest(item) != mapping.get('osmObjectSha256'):
            raise ImportErrorDetail('Reference geometry changed; re-review required')
        role = mapping.get('role')
        physical_id = None
        if role == 'station-reference':
            if (source != 'cis-rail' or stop.get('boardingMode') == 'replacement'
                    or item.get('type') != 'node' or item.get('tags', {}).get('railway') not in ('station', 'halt')):
                raise ImportErrorDetail('Station reference is not a boarding platform or NAD stop')
            uic = item.get('tags', {}).get('uic_ref', '')
            if not isinstance(uic, str) or not re.fullmatch('54[0-9]{5}', uic) or key != 'czrail:CZ:' + uic[2:]:
                raise ImportErrorDetail('Railway reference requires an exact UIC/CIS identity')
        elif role == 'platform-reference':
            if (source != 'cis-ostrava' or not isinstance(stop.get('sourceCall'), dict)
                    or not re.fullmatch('[a-f0-9]{64}', str(stop['sourceCall'].get('patternSha256', '')))
                    or item.get('tags', {}).get('public_transport') != 'platform'):
                raise ImportErrorDetail('A vehicle stop position is not a verified platform')
            if mapping.get('sourcePatternSha256') != stop['sourceCall']['patternSha256']:
                raise ImportErrorDetail('Platform source pattern changed')
            relation = indexed.get('relation:' + str(mapping.get('osmRelation')))
            if relation is None or digest(relation) != mapping.get('osmRelationSha256'):
                raise ImportErrorDetail('Platform route direction changed')
            tags = relation.get('tags', {})
            if tags.get('route') != 'bus' or tags.get('operator') != 'cz:DPO' or tags.get('ref') != mapping.get('line'):
                raise ImportErrorDetail('Platform route/operator does not match')
            if catalog.get('patternDirections', {}).get(stop['sourceCall']['patternSha256']) != relation['id']:
                raise ImportErrorDetail('Platform direction differs from the reviewed source pattern')
            rows = route_calls(relation)
            index = mapping.get('osmRouteIndex')
            if type(index) is not int or not 0 <= index < len(rows):
                raise ImportErrorDetail('Invalid platform route sequence')
            expected_name = catalog.get('nameAliases', {}).get(stop['name'])
            name_matches = [i for i, row in enumerate(rows)
                if indexed.get(f"{row['stop']['type']}:{row['stop']['ref']}", {}).get('tags', {}).get('name') == expected_name]
            if not isinstance(expected_name, str) or name_matches != [index]:
                raise ImportErrorDetail('Platform route call does not uniquely match its reviewed source name')
            member = rows[index]['platform']
            if member is None or f"{member['type']}:{member['ref']}" != mapping['osmObject']:
                raise ImportErrorDetail('Platform is not paired with this route stop')
            physical_id = mapping.get('physicalPlatformId')
            expected = stop.get('physicalPlatformId') if stop.get('physicalPlatformVerified') is True else 'osm:' + mapping['osmObject']
            if physical_id != expected:
                raise ImportErrorDetail('Platform identity conflicts with its reviewed geometry')
        else:
            raise ImportErrorDetail('Unknown reference coordinate role')
        lat, lon = coordinate(item, indexed)
        evidence = {'source': 'OpenStreetMap', 'url': f"https://www.openstreetmap.org/{item['type']}/{item['id']}",
                    'object': mapping['osmObject'], 'version': item.get('version'), 'geometrySha256': digest(item),
                    'verifiedAt': review['verifiedAt'], 'reviewedFrom': review['reviewedFrom'], 'reviewedUntil': review['reviewedUntil'],
                    'positionIsBoardingDoor': False, 'method': 'node reference' if item['type'] == 'node' else 'halfway along mapped platform geometry'}
        changed[key] = {**stop, 'lat': lat, 'lon': lon, 'coordinatesKnown': True, 'coordinateRole': role, 'coordinateEvidence': evidence}
        if physical_id is not None:
            changed[key]['physicalPlatformId'] = physical_id
            changed[key]['physicalPlatformVerified'] = True
            if stop.get('physicalPlatformVerified') is not True:
                changed[key]['platformEvidence'] = {'url': f"https://www.openstreetmap.org/relation/{relation['id']}",
                    'verifiedAt': review['verifiedAt'], 'validFrom': review['reviewedFrom'],
                    'reviewedFrom': review['reviewedFrom'], 'reviewedUntil': review['reviewedUntil'],
                    'method': 'reviewed exact CIS pattern and OSM direction/platform membership', 'operatorValidityClaimed': False}
    output = dict(network)
    output['stops'] = [changed.get(stop['id'], stop) for stop in network['stops']]
    output['active'] = dict(network['active'])
    output['coverage'] = dict(network.get('coverage', {}))
    output['coverage']['stopsWithKnownCoordinates'] = sum(stop.get('coordinatesKnown') is True for stop in output['stops'])
    if source == 'cis-ostrava':
        output['coverage']['physicalPlatformMappingsVerified'] = sum(stop.get('physicalPlatformVerified') is True for stop in output['stops'])
    output['coordinateMappingEvidence'] = {'mappedCalls': len(changed), 'geometrySha256': digest(geometry), 'review': dict(review),
        'attribution': geometry['attribution'] + ', ODbL 1.0', 'license': geometry['license'], 'walkingPathsAdded': 0}
    return finalize(output)
