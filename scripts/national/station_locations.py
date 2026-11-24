"""Exact SR70 reference locations for public rail station identities.

Coordinates describe the registered station reference point. They establish
neither a boarding platform nor a pedestrian/NAD interchange.
"""
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
from .common import ImportErrorDetail, digest, finalize, iso_day

SOURCE_SHA256 = '81bfbb9cc36c6a8930725d2ffd1b7764183e3bf1285217710a6b6fb94d0bf69c'
DEFAULT_REGISTER = Path(__file__).resolve().parents[2] / 'reports/station-register-20261027'
# Station/halt classes that may describe the public CIS calls already exported.
# The infrastructure register alone does not establish a passenger service.
# NAD stops (65), operational junctions and abstract transport nodes are excluded.
PASSENGER_QUALIFIERS = {'1', '3', '4', '8', '9', '23', '24', '37', '38', '43', '52', '61', '62'}
ATTRIBUTION = 'Správa železnic, SR70; referenční souřadnice stanic'
DEFAULT_NAME_ALIASES = Path(__file__).resolve().parents[2] / 'config/rail-station-reference-aliases.json'


def station_code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,6}', value):
        raise ImportErrorDetail('Invalid SR70 record identifier')
    number = value.zfill(6)
    total = 0
    for index, digit in enumerate(number[:5]):
        product = int(digit) * (2 if index % 2 == 0 else 1)
        total += product // 10 + product % 10
    if (10 - total % 10) % 10 != int(number[5]):
        raise ImportErrorDetail('SR70 record check digit does not match')
    return number[:5]


def gps(value, hemisphere):
    if hemisphere not in {'N', 'E'}:
        raise ImportErrorDetail('Unreviewed GPS hemisphere')
    if not isinstance(value, str):
        raise ImportErrorDetail('GPS DEG value must preserve the source string')
    match = re.fullmatch(hemisphere + r'([0-9]{1,3}[,.][0-9]{1,9})°', value.strip())
    if not match:
        raise ImportErrorDetail('Unexpected GPS hemisphere or decimal format')
    coordinate = float(match[1].replace(',', '.'))
    if not math.isfinite(coordinate):
        raise ImportErrorDetail('Invalid GPS value')
    lower, upper = (48.4, 51.2) if hemisphere == 'N' else (12.0, 19.0)
    if not lower <= coordinate <= upper:
        raise ImportErrorDetail('Station reference is outside the reviewed Czech geographic scope')
    return coordinate


def name_key(value):
    # Formatting only: no fuzzy matching, accent removal or invented abbreviations.
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in unicodedata.normalize('NFC', value).casefold() if c.isalnum())


def name_alias_catalog(catalog=None):
    if catalog is None:
        if not DEFAULT_NAME_ALIASES.exists():
            return {}, None, None
        catalog = json.loads(DEFAULT_NAME_ALIASES.read_text(encoding='utf-8-sig'))
    try:
        if catalog.get('schema') != 1 or catalog.get('sourceSha256') != SOURCE_SHA256 or catalog.get('verified') is not True:
            raise ValueError('Unreviewed alias catalog')
        stamp = dt.datetime.fromisoformat(catalog['verifiedAt'].replace('Z', '+00:00'))
        first, last = iso_day(catalog['reviewedFrom']), iso_day(catalog['reviewedUntil'])
        if stamp.tzinfo is None or first > last or not isinstance(catalog.get('aliases'), list):
            raise ValueError('Invalid alias review')
        indexed = {}
        for alias in catalog['aliases']:
            key = alias['stopId']
            if key in indexed or not re.fullmatch(r'czrail:CZ:[0-9]{1,5}', key):
                raise ValueError('Duplicate or invalid alias identity')
            indexed[key] = alias
        return indexed, first, last
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ImportErrorDetail('Invalid reviewed station-name alias catalog') from error


def audit_register_rows(manifest, rows):
    if manifest.get('sourceSha256') != SOURCE_SHA256 or len(rows) != manifest.get('rowCount'):
        raise ImportErrorDetail('Unreviewed or incomplete station-register source')
    codes = [station_code(row['sr70']) for row in rows]
    if len(set(codes)) != len(codes):
        raise ImportErrorDetail('Register has ambiguous primary identities')
    candidates = [row for row in rows if row['state'] == '1' and row['qualifier'] in PASSENGER_QUALIFIERS]
    for row in candidates:
        gps(row['latitude'], 'N')
        gps(row['longitude'], 'E')
    return {'sourceSha256': SOURCE_SHA256, 'sourceRows': len(rows),
        'allRecordCheckDigitsValid': True, 'primaryIdentitiesUnique': True,
        'activeStationOrHaltReferenceRows': len(candidates), 'allCandidateGpsValuesParsed': True,
        'passengerServicesEstablishedByRegisterAlone': False, 'bindingToFullCisCollectionVerified': False,
        'walkingConnectionsAdded': 0, 'coordinatesActivatedInProduction': False, 'apkChanged': False}


def load_register(directory=DEFAULT_REGISTER):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8-sig'))
    if (manifest.get('schema') != 1 or manifest.get('sourceSha256') != SOURCE_SHA256
            or manifest.get('sourceFormat') != 'xlsx' or not isinstance(manifest.get('parts'), list)):
        raise ImportErrorDetail('Unreviewed station-register source revision')
    rows, seen = [], set()
    for part in manifest['parts']:
        name = part.get('file')
        if not isinstance(name, str) or not re.fullmatch(r'part-[0-9]{3}\.json', name) or name in seen:
            raise ImportErrorDetail('Invalid or duplicate station-register part')
        seen.add(name)
        raw = (directory / name).read_bytes()
        if len(raw) != part.get('bytes') or hashlib.sha256(raw).hexdigest() != part.get('sha256'):
            raise ImportErrorDetail('Station-register source part checksum differs')
        data = json.loads(raw)
        if (data.get('schema') != 1 or data.get('sourceSha256') != SOURCE_SHA256
                or not isinstance(data.get('rows'), list) or len(data['rows']) != part.get('rowCount')):
            raise ImportErrorDetail('Invalid station-register source part')
        rows.extend(data['rows'])
    if not rows or len(rows) != manifest.get('rowCount'):
        raise ImportErrorDetail('Incomplete station-register source rows')
    return manifest, rows


def apply_station_locations(network, manifest, rows, name_aliases=None):
    if manifest.get('sourceSha256') != SOURCE_SHA256 or len(rows) != manifest.get('rowCount'):
        raise ImportErrorDetail('Unreviewed or incomplete station-register source')
    try:
        first, last = iso_day(manifest['validFrom']), iso_day(manifest['validUntil'])
        if first > iso_day(network['start']) or last < iso_day(network['end']):
            raise ValueError('Register does not cover the network horizon')
        stamp = dt.datetime.fromisoformat(manifest['observedAt'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Register observation needs a timezone')
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ImportErrorDetail('Invalid or expired station-register validity') from error

    aliases, alias_first, alias_last = name_alias_catalog(name_aliases)
    aliases_applied = set()
    indexed = defaultdict(list)
    invalid_rows = []
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise ImportErrorDetail('Invalid station-register row')
            indexed[station_code(row.get('sr70'))].append(row)
        except ImportErrorDetail as error:
            invalid_rows.append({'sr70': row.get('sr70') if isinstance(row, dict) else None, 'reason': str(error)})
    changed, missing, preserved = {}, [], []
    skipped_nad = 0
    for stop in network['stops']:
        if stop.get('source') != 'cis-rail' or stop.get('type') != 0:
            continue
        if stop.get('boardingMode') == 'replacement':
            skipped_nad += 1
            continue
        match = re.fullmatch(r'czrail:CZ:([0-9]{1,5})', stop['id'])
        if not match:
            missing.append({'id': stop['id'], 'name': stop['name'], 'reason': 'unreviewed-source-identity'})
            continue
        candidates = indexed.get(match[1].zfill(5), [])
        reason = None
        if len(candidates) != 1:
            reason = 'missing-register-identity' if not candidates else 'ambiguous-register-identity'
        else:
            row = candidates[0]
            if row.get('state') != '1':
                reason = 'register-not-active'
            elif row.get('qualifier') not in PASSENGER_QUALIFIERS:
                reason = 'not-a-reviewed-passenger-station-or-halt'
            else:
                names = {name_key(row.get(field)) for field in ['name', 'name35', 'name20', 'name12']}
                named = bool(name_key(stop['name'])) and name_key(stop['name']) in names
                used_alias = False
                if not named and stop['id'] in aliases:
                    alias = aliases[stop['id']]
                    if alias_first > iso_day(network['start']) or alias_last < iso_day(network['end']):
                        reason = 'name-alias-review-expired'
                    elif (alias.get('sourceName') != stop['name'] or alias.get('registerRecord') != row['sr70']
                            or alias.get('registerName') != row['name'] or alias.get('sourceStopSha256') != digest(stop)
                            or alias.get('registerRowSha256') != digest(row)):
                        reason = 'name-alias-source-changed'
                    else:
                        named = used_alias = True
                if not named and reason is None:
                    reason = 'source-name-needs-review'
                if named:
                    try:
                        lat, lon = gps(row.get('latitude'), 'N'), gps(row.get('longitude'), 'E')
                    except ImportErrorDetail:
                        reason = 'invalid-register-coordinate'

        if stop.get('coordinatesKnown') is True:
            # Keep independently reviewed positions and their endpoint hashes.
            preserved.append({'id': stop['id'], 'registerIdentityAndNameMatch': reason is None,
                              'reviewReason': reason})
            continue
        if reason:
            missing.append({'id': stop['id'], 'name': stop['name'], 'reason': reason})
            continue
        if stop.get('physicalPlatformVerified') is True:
            missing.append({'id': stop['id'], 'name': stop['name'], 'reason': 'specific-platform-location-required'})
            continue
        if used_alias:
            aliases_applied.add(stop['id'])
        evidence = {'source': 'Správa železnic SR70', 'url': manifest['sourceUrl'],
            'sourceSha256': SOURCE_SHA256, 'rowSha256': digest(row), 'record': row['sr70'],
            'qualifier': row['qualifier'], 'state': row['state'],
            'validFrom': manifest['validFrom'], 'validUntil': manifest['validUntil'],
            'positionIsBoardingDoor': False, 'positionIsPlatform': False}
        if used_alias:
            evidence['validFrom'] = max(first, alias_first).isoformat()
            evidence['validUntil'] = min(last, alias_last).isoformat()
            evidence['nameAliasSourceStopSha256'] = aliases[stop['id']]['sourceStopSha256']
        changed[stop['id']] = {**stop, 'lat': lat, 'lon': lon, 'coordinatesKnown': True,
                              'coordinateRole': 'station-reference', 'coordinateEvidence': evidence}

    report = {'sourceUrl': manifest['sourceUrl'], 'sourceSha256': SOURCE_SHA256,
        'sourceRows': len(rows), 'validFrom': manifest['validFrom'], 'validUntil': manifest['validUntil'],
        'newStationReferenceLocations': len(changed), 'reviewedNameAliasesApplied': len(aliases_applied), 'preexistingLocationsPreserved': len(preserved),
        'nadContextsNotAssignedStationCoordinates': skipped_nad,
        'unmappedNormalRailContexts': len(missing),
        'unmappedReasons': dict(Counter(item['reason'] for item in missing)),
        'unmapped': missing, 'preservedReferences': preserved,
        'invalidRegisterRows': invalid_rows, 'invalidRegisterRowCount': len(invalid_rows),
        'walkingEdgesAdded': 0, 'platformAliasesAdded': 0,
        'serviceTimesCalendarsAndPublicGroupsChanged': False, 'attribution': ATTRIBUTION}
    output = dict(network)
    output['stops'] = [changed.get(stop['id'], stop) for stop in network['stops']]
    output['active'] = dict(network['active'])
    output['coverage'] = dict(network.get('coverage', {}))
    output['coverage']['stopsWithKnownCoordinates'] = sum(s.get('coordinatesKnown') is True for s in output['stops'])
    output['stationRegisterEvidence'] = {k: v for k, v in report.items()
        if k not in {'unmapped', 'preservedReferences', 'invalidRegisterRows'}}
    return finalize(output), report
