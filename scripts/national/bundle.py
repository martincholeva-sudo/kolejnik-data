"""Compose isolated source namespaces and only explicitly verified interchanges."""
import datetime as dt
import gzip
import io
import json
import os
from pathlib import Path
from .common import ImportErrorDetail, blank, digest, finalize, iso_day

SCOPE = 'cz-rail-ostrava'

def compose(networks, start, end, interchange_links=None):
    output = blank(start, end, 'CIS JŘ + PID; preparation')
    ids, trip_ids = set(), set()
    counts = {}
    location_attributions = set()
    for source, network in networks:
        if network.get('schema') != 2:
            raise ImportErrorDetail('Unsupported source schema')
        stop_offset, route_offset = len(output['stops']), len(output['routes'])
        for stop in network['stops']:
            if stop['id'] in ids:
                raise ImportErrorDetail('Source stop namespaces collide: ' + stop['id'])
            ids.add(stop['id']); output['stops'].append(dict(stop))
        output['routes'].extend(network['routes'])
        kept_services = set()
        kept_trips = 0
        for trip in network['trips']:
            # A single national rail source is authoritative for this candidate.
            # PID metro/tram/bus/trolleybus and public depot runs are retained.
            if source == 'pid' and network['routes'][trip[2]][2] == 2:
                continue
            if trip[0] in trip_ids:
                raise ImportErrorDetail('Source trip namespaces collide')
            trip_ids.add(trip[0]); kept_services.add(trip[1]); kept_trips += 1
            output['trips'].append([*trip[:2], trip[2] + route_offset, *trip[3:5],
                                   [[row[0] + stop_offset, *row[1:]] for row in trip[5]]])
        for day in output['active']:
            output['active'][day].extend(service for service in network['active'].get(day, []) if service in kept_services)
        output['paths'].extend([[a + stop_offset, b + stop_offset, *rest] for a, b, *rest in network['paths']])
        output['transfers'].extend([[a + stop_offset, b + stop_offset, *rest] for a, b, *rest in network['transfers']])
        counts[source] = {'trips': kept_trips, 'serviceCalendars': len(kept_services), 'stops': len(network['stops']), 'sha256': network['sha256']}
        counts[source]['knownStopCoordinates'] = sum(stop.get('coordinatesKnown', bool(stop.get('lat')) and bool(stop.get('lon'))) is True for stop in network['stops'])
        for evidence_key in ['coordinateMappingEvidence', 'stationRegisterEvidence']:
            attribution = network.get(evidence_key, {}).get('attribution')
            if attribution:
                location_attributions.add(attribution)
    indexes = {stop['id']: i for i, stop in enumerate(output['stops'])}
    accepted_links = []
    links = interchange_links or {'schema': 1, 'links': []}
    if not isinstance(links, dict):
        raise ImportErrorDetail('Invalid interchange evidence schema')
    if links.get('schema') != 1 or not isinstance(links.get('links'), list):
        raise ImportErrorDetail('Invalid interchange evidence schema')
    for link in links['links']:
        if not isinstance(link, dict):
            raise ImportErrorDetail('Invalid interchange evidence row')
        if link.get('verified') is not True or not str(link.get('evidence', '')).startswith('https://'):
            raise ImportErrorDetail('Every interchange requires explicit verification and source evidence')
        if link.get('from') not in indexes or link.get('to') not in indexes:
            raise ImportErrorDetail('Interchange endpoint does not exist in this source revision')
        for endpoint, field in [('from', 'fromStopSha256'), ('to', 'toStopSha256')]:
            if field in link and link[field] != digest(output['stops'][indexes[link[endpoint]]]):
                raise ImportErrorDetail('Interchange source stop changed; re-verification required')
        if 'reviewedFrom' in link and iso_day(link['reviewedFrom']) > iso_day(start):
            raise ImportErrorDetail('Interchange review does not cover the source horizon start')
        if 'reviewedUntil' in link and iso_day(link['reviewedUntil']) < iso_day(end):
            raise ImportErrorDetail('Interchange review expired before the source horizon end')
        if not isinstance(link.get('estimated', False), bool):
            raise ImportErrorDetail('Interchange estimate flag must be boolean')
        seconds = link.get('seconds')
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 30 <= seconds <= 1800:
            raise ImportErrorDetail('Invalid verified interchange duration')
        try:
            verified = dt.datetime.fromisoformat(link['verifiedAt'].replace('Z', '+00:00'))
            if verified.tzinfo is None:
                raise ValueError('Timezone is required')
        except (KeyError, ValueError, AttributeError) as error:
            raise ImportErrorDetail('Interchange verification date is required') from error
        a, b = indexes[link['from']], indexes[link['to']]
        output['paths'].append([a, b, seconds, link.get('estimated', False)])
        if link.get('bidirectional') is True:
            output['paths'].append([b, a, seconds, link.get('estimated', False)])
        accepted_links.append(dict(link))
    # Different timetable calls may be proved to use one physical platform.
    # Alias walking is zero, while the ordinary two-minute interchange minimum
    # and the user's additional reserve still apply to every vehicle change.
    platforms = {}
    for index, stop in enumerate(output['stops']):
        if stop.get('physicalPlatformVerified') is True and stop.get('physicalPlatformId'):
            if not isinstance(stop.get('platformEvidence'), dict) or not stop['platformEvidence'].get('url', '').startswith('https://'):
                raise ImportErrorDetail('Physical platform aliases require reviewed evidence')
            evidence = stop['platformEvidence']
            try:
                verified = dt.datetime.fromisoformat(evidence['verifiedAt'].replace('Z', '+00:00'))
                if verified.tzinfo is None or iso_day(evidence['validFrom']) > iso_day(start):
                    raise ValueError('Platform review does not cover the horizon')
                for field, bound, too_late in [('reviewedFrom', start, True), ('validUntil', end, False), ('reviewedUntil', end, False)]:
                    if field in evidence:
                        reviewed = iso_day(evidence[field]); horizon = iso_day(bound)
                        if (too_late and reviewed > horizon) or (not too_late and reviewed < horizon):
                            raise ValueError('Platform review does not cover the horizon')
            except (KeyError, ValueError, TypeError, AttributeError) as error:
                raise ImportErrorDetail('Invalid or expired physical platform review') from error
            platforms.setdefault(stop['physicalPlatformId'], []).append(index)
    platform_evidence = []
    for platform, calls in sorted(platforms.items()):
        for a in calls:
            for b in calls:
                if a == b:
                    continue
                output['paths'].append([a, b, 0, False])
                output['transfers'].append([a, b, 2, 120, '', ''])
        platform_evidence.append({'physicalPlatformId': platform, 'callCount': len(calls), 'minimumTransferSeconds': 120})
    output['platformTransferEvidence'] = platform_evidence
    output['scope'] = SCOPE
    output['sourceCoverage'] = counts
    output['interchangeEvidence'] = accepted_links
    output['liveCoverage'] = {'rail': 'not-connected', 'ostrava': 'not-connected'}
    output['candidateOnly'] = True
    output['attribution'] = 'CIS JŘ / Ministerstvo dopravy; PID / ROPID, CC BY; převedeno Kolejníkem'
    if accepted_links and isinstance(links.get('attribution'), str) and links['attribution'].strip():
        output['attribution'] += '; ' + links['attribution'].strip()
        output['interchangeAttribution'] = links['attribution'].strip()
    for attribution in sorted(location_attributions):
        if attribution not in output['attribution']:
            output['attribution'] += '; ' + attribution
    return finalize(output)

def write_candidate(network, directory, report):
    """Write a separate candidate, never overwrite the production PID manifest."""
    directory = Path(directory).resolve()
    if directory.name in ('data-v2', 'data'):
        raise ImportErrorDetail('National preparation must use a separate candidate directory')
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {key: network[key] for key in ('schema', 'source', 'attribution', 'sha256', 'start', 'end', 'scope', 'candidateOnly', 'sourceCoverage', 'liveCoverage')}
    metadata['days'] = {}
    for day, services in network['active'].items():
        date = iso_day(day)
        previous, following = [(date + dt.timedelta(days=n)).isoformat() for n in (-1, 1)]
        current = set(services)
        before = set(network['active'].get(previous, [])); after = set(network['active'].get(following, []))
        trips = [trip for trip in network['trips'] if trip[1] in current or
                 (trip[1] in before and trip[5][-1][2] >= 86400) or
                 (trip[1] in after and trip[5][0][1] <= 43200)]
        bundle = {**network, 'trips': trips, 'active': {d: network['active'][d] for d in (previous, day, following) if d in network['active']}}
        filename = network['sha256'][:12] + '-' + day + '.json.gz'
        temporary = directory / (filename + '.tmp')
        with temporary.open('wb') as raw, gzip.GzipFile(filename='',fileobj=raw,mode='wb',compresslevel=5,mtime=0) as packed, io.TextIOWrapper(packed,encoding='utf-8') as stream:
            json.dump(bundle, stream, ensure_ascii=False, separators=(',', ':'))
        os.replace(temporary, directory / filename)
        metadata['days'][day] = filename
    pending = directory / 'manifest.tmp'
    pending.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(pending, directory / 'manifest.json')
    (directory / 'coverage-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return metadata
