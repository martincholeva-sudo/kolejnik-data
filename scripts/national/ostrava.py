"""Conservative CIS NeTEx EPIP import of DPO tram, trolleybus and bus services.
Selected unsupported calendars/restrictions fail the import; no services are silently skipped.
"""
from .common import (ImportErrorDetail, local, children, first, text, descendants, ref,
                     iso_day, bitmap, clock, digest, documents, blank, add_service, finalize)
import re
import json
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import unicodedata

DPO_ICO = '61974757'
MODES = {'tram': 0, 'bus': 3, 'coach': 3, 'trolleyBus': 11, 'trolleybus': 11}
SOURCE = 'https://portal.cisjr.cz/pub/netex/'

def is_dpo(node):
    code = re.sub(r'\D', '', text(node, 'PublicCode'))
    name = unicodedata.normalize('NFKD', text(node, 'LegalName') or text(node, 'Name'))
    name = ''.join(c for c in name if not unicodedata.combining(c)).lower()
    return code == DPO_ICO or 'dopravni podnik ostrava' in name

def relevant_header(prefix):
    """CIS line offers declare their operator in the initial EU_PI_COMMON frame."""
    if b'\x00' in prefix or b'<!DOCTYPE' in prefix.upper() or b'<!ENTITY' in prefix.upper():
        raise ImportErrorDetail('DTD/entity declarations are not accepted')
    parser = ET.XMLPullParser(events=('end',))
    participant = ''
    try:
        parser.feed(prefix)
        for _, node in parser.read_events():
            if local(node.tag) == 'ParticipantRef':
                participant = (node.text or '').strip()
            if local(node.tag) == 'ResourceFrame':
                operators = descendants(node, 'Operator')
                if any(is_dpo(operator) for operator in operators):
                    return True
                if participant == 'CZ:cisjr' and node.get('id', '').endswith('ResourceFrame-EU_PI_COMMON') and operators:
                    return False
    except ET.ParseError:
        return None
    return None


def archive_documents(filename):
    # Selection happens before full decompression. The complete national road
    # archive exceeds 5 GB uncompressed; DPO represents only a small portion.
    yield from documents(filename, max_xml_bytes=32_000_000, xml_header_filter=relevant_header)


def bool_value(node, name, default=True):
    value = text(node, name) or text(node, name[:1].lower() + name[1:])
    if not value:
        return default
    if value not in ('true', 'false', '1', '0'):
        raise ImportErrorDetail('Invalid boolean: ' + name)
    return value in ('true', '1')

def operating_days(root):
    periods = {}
    for node in descendants(root, 'UicOperatingPeriod'):
        periods[node.get('id')] = bitmap(text(node, 'FromDate'), text(node, 'ToDate'), text(node, 'ValidDayBits'))
    result, excluded = {}, {}
    for assignment in descendants(root, 'DayTypeAssignment'):
        day_type = ref(assignment, 'DayTypeRef')
        if not day_type:
            raise ImportErrorDetail('Calendar assignment has no DayTypeRef')
        period = ref(assignment, 'OperatingPeriodRef') or ref(assignment, 'UicOperatingPeriodRef')
        literal = text(assignment, 'Date')
        if period:
            if period not in periods:
                raise ImportErrorDetail('Unknown operating period')
            dates = periods[period]
        elif literal:
            dates = {iso_day(literal).isoformat()}
        else:
            raise ImportErrorDetail('Unsupported calendar assignment; no bitmap or date')
        target = result if bool_value(assignment, 'IsAvailable') else excluded
        target.setdefault(day_type, set()).update(dates)
    return {key: values - excluded.get(key, set()) for key, values in result.items()}

def source_fingerprint(root):
    stamp = first(root, 'PublicationTimestamp')
    position = list(root).index(stamp) if stamp is not None else None
    if stamp is not None:
        root.remove(stamp)
    try:
        canonical = ET.canonicalize(ET.tostring(root, encoding='unicode'), strip_text=True, rewrite_prefixes=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
    finally:
        if stamp is not None:
            root.insert(position, stamp)


def unroll_cis_midnight(rows):
    """CIS JDF conversion omits day offsets; accept only a bounded night crossing."""
    result = []
    day = 0
    previous = None
    for row in rows:
        arrival, departure = row[1:3]
        a = arrival + day * 86400
        if previous is not None and a < previous:
            if previous % 86400 < 18 * 3600 or arrival > 6 * 3600 or day:
                raise ImportErrorDetail('Invalid backwards time in a CIS journey')
            day += 1
            a = arrival + day * 86400
        d = departure + day * 86400
        if d < a:
            if not result or arrival < 18 * 3600 or departure > 6 * 3600 or day:
                raise ImportErrorDetail('Invalid backwards dwell time in a CIS journey')
            day += 1
            d = departure + day * 86400
        result.append([row[0], a, d, *row[3:]])
        previous = d
    if result and result[-1][1] - result[0][2] > 6 * 3600:
        raise ImportErrorDetail('Inferred DPO journey exceeds the supported six-hour bound')
    return result


class OstravaNetex:
    def __init__(self, start, end):
        self.network = blank(start, end, SOURCE)
        self.stops = {}
        self.journeys = {}
        self.trip_offers = {}
        self.offers = {}
        self.mode_corrections = set()
        self.resolutions_seen = {}
        config = Path(__file__).resolve().parents[2] / 'config'
        catalog = config / 'dpo-route-catalog.json'
        resolutions = config / 'dpo-offer-resolutions.json'
        self.catalog = json.loads(catalog.read_text(encoding='utf-8-sig')) if catalog.exists() else {'routes': {}}
        self.resolutions = json.loads(resolutions.read_text(encoding='utf-8-sig')).get('offers', []) if resolutions.exists() else []
        self.report = {'xmlDocuments': 0, 'selectedJourneys': 0, 'otherOperatorsExcluded': 0,
                       'duplicateJourneys': 0, 'inferredMidnightJourneys': 0, 'reviewedOffersExcluded': 0, 'productionReady': False, 'realSourceValidated': False,
                       'liveDelayCoverage': 'not-connected', 'operator': {'id': DPO_ICO, 'name': 'Dopravní podnik Ostrava a.s.'}}
    def reviewed_offer(self, root, origin, line):
        validity = first(line, 'ValidBetween')
        for rule in self.resolutions:
            if rule['lineCode'] != text(line, 'PublicCode'):
                continue
            if iso_day(text(validity, 'FromDate')).isoformat() != rule['from'] or iso_day(text(validity, 'ToDate')).isoformat() != rule['to']:
                raise ImportErrorDetail('Reviewed source offer validity changed; re-verification required')
            fingerprint = source_fingerprint(root)
            if fingerprint not in rule['semanticSha256'].values():
                raise ImportErrorDetail('Reviewed source offer semantics changed; re-verification required')
            # CIS renumbers archive members daily. Reviewed content is stable;
            # a filename is provenance, never the identity of a timetable offer.
            self.resolutions_seen.setdefault(rule['lineCode'], set()).add(fingerprint)
            preferred = rule['semanticSha256'][rule['preferredDocument']]
            if fingerprint != preferred:
                self.report['reviewedOffersExcluded'] += 1
                return False
        return True

    def add(self, root, origin=''):
        # A bad document must not leak half its stops/trips into later inputs.
        counts = {key: len(self.network[key]) for key in ('stops', 'routes', 'trips')}
        day_counts = {day: len(values) for day, values in self.network['active'].items()}
        had_route_cache = '_routeIndexes' in self.network
        route_cache = dict(self.network.get('_routeIndexes', {}))
        saved = (dict(self.stops), dict(self.journeys), dict(self.trip_offers),
                 {key: dict(values) for key, values in self.offers.items()},
                 set(self.mode_corrections), {key: set(values) for key, values in self.resolutions_seen.items()},
                 dict(self.report))
        try:
            self._add(root, origin)
        except Exception:
            for key, length in counts.items():
                del self.network[key][length:]
            for day, length in day_counts.items():
                del self.network['active'][day][length:]
            if had_route_cache:
                self.network['_routeIndexes'] = route_cache
            else:
                self.network.pop('_routeIndexes', None)
            (self.stops, self.journeys, self.trip_offers, self.offers,
             self.mode_corrections, self.resolutions_seen, self.report) = saved
            raise

    def _add(self, root, origin=''):
        self.report['xmlDocuments'] += 1
        operators = {node.get('id'): node for node in descendants(root, 'Operator')}
        lines = {node.get('id'): node for node in descendants(root, 'Line')}
        selected = {key for key, node in operators.items() if is_dpo(node)}
        if not selected:
            self.report['otherOperatorsExcluded'] += len(descendants(root, 'ServiceJourney'))
            return
        if len(lines) == 1 and not self.reviewed_offer(root, origin, next(iter(lines.values()))):
            return
        patterns = {node.get('id'): node for node in descendants(root, 'ServiceJourneyPattern')}
        pattern_signatures = {}
        places = {node.get('id'): node for node in descendants(root, 'StopPlace')}
        points = {node.get('id'): node for node in descendants(root, 'ScheduledStopPoint')}
        assignments = {}
        for assignment in descendants(root, 'PassengerStopAssignment'):
            point, place = ref(assignment, 'ScheduledStopPointRef'), ref(assignment, 'StopPlaceRef')
            if point and place:
                if point in assignments and assignments[point] != place:
                    raise ImportErrorDetail('One scheduled point has multiple StopPlaces')
                assignments[point] = place
        days = None
        cis_profile = any(key.startswith('CZ:cisjr_jdf:Line:') for key in lines)
        scope = digest([[key, text(first(node, 'ValidBetween'), 'FromDate'), text(first(node, 'ValidBetween'), 'ToDate')] for key, node in sorted(lines.items())])[:16] if cis_profile else ''
        for journey in descendants(root, 'ServiceJourney'):
            pattern = patterns.get(ref(journey, 'JourneyPatternRef') or ref(journey, 'ServiceJourneyPatternRef'))
            if pattern is None:
                raise ImportErrorDetail('Unknown journey pattern')
            line = lines.get(ref(journey, 'LineRef') or ref(pattern, 'LineRef') or ref(first(pattern, 'RouteView'), 'LineRef'))
            if line is None:
                raise ImportErrorDetail('Unknown line')
            operator_id = ref(journey, 'OperatorRef') or ref(line, 'OperatorRef')
            if operator_id not in selected:
                self.report['otherOperatorsExcluded'] += 1
                continue
            self.report['selectedJourneys'] += 1
            mode = text(line, 'TransportMode')
            if mode not in MODES:
                raise ImportErrorDetail('Unsupported DPO transport mode: ' + mode)
            public_code = text(line, 'PublicCode')
            presentation = self.catalog.get('routes', {}).get(public_code) if cis_profile else None
            if cis_profile and presentation is None:
                raise ImportErrorDetail('DPO vehicle class needs a reviewed source mapping: ' + public_code)
            if cis_profile and not (self.catalog['validFrom'] <= min(self.network['active']) <= max(self.network['active']) <= self.catalog['validUntil']):
                raise ImportErrorDetail('DPO vehicle-class reference is outside its reviewed period')
            route_type = presentation['routeType'] if presentation else MODES[mode]
            if route_type != MODES[mode]:
                self.mode_corrections.add(public_code)
            if descendants(journey, 'FlexibleServiceProperties') or descendants(pattern, 'FlexiblePointProperties'):
                raise ImportErrorDetail('Booking/conditional service needs a supported passenger policy')
            for node in [pattern, journey]:
                for item in descendants(node, 'KeyValue'):
                    if text(item, 'Key') in ('JdfFixedCode§', 'JdfFixedCodeA', 'JdfFixedCodeB', 'JdfFixedCodeC'):
                        raise ImportErrorDetail('Unsupported within-route passenger restriction')
            if days is None:
                days = operating_days(root)
            day_refs = [node.get('ref') for node in descendants(first(journey, 'dayTypes'), 'DayTypeRef')] if first(journey, 'dayTypes') is not None else []
            if not day_refs or any(key not in days for key in day_refs):
                raise ImportErrorDetail('Selected journey has an unresolved calendar')
            active = set().union(*(days[key] for key in day_refs))
            validity = first(line, 'ValidBetween')
            if cis_profile:
                lower_day = iso_day(text(validity, 'FromDate')).isoformat()
                upper_day = iso_day(text(validity, 'ToDate')).isoformat()
                self.offers.setdefault(line.get('id'), {})[scope] = (lower_day, upper_day)
            for validity in [validity, first(journey, 'ValidBetween')]:
                if validity is None:
                    continue
                lower, upper = text(validity, 'FromDate'), text(validity, 'ToDate')
                if lower:
                    active = {day for day in active if day >= iso_day(lower).isoformat()}
                if upper:
                    active = {day for day in active if day <= iso_day(upper).isoformat()}
            pattern_points = {node.get('id'): node for node in descendants(pattern, 'StopPointInJourneyPattern')}
            rows = []
            for sequence, passing in enumerate(descendants(journey, 'TimetabledPassingTime'), 1):
                point = pattern_points.get(ref(passing, 'StopPointInJourneyPatternRef'))
                if point is None:
                    raise ImportErrorDetail('Timing references an unknown pattern point')
                point_id = ref(point, 'ScheduledStopPointRef')
                scheduled = points.get(point_id)
                if scheduled is None:
                    raise ImportErrorDetail('Unknown scheduled stop point')
                place_id = assignments.get(point_id) or ref(scheduled, 'StopPlaceRef')
                place = places.get(place_id)
                name = text(place, 'Name') or text(scheduled, 'Name')
                if not name:
                    raise ImportErrorDetail('Scheduled point has no public name')
                source_stop_id = 'ostrava:' + (scope + ':' if scope else '') + point_id
                # CIS JDF reuses one scheduled point in both directions. It is
                # a timetable stop, not proof that the calls use one platform.
                # Keep each pattern call isolated until physical access is reviewed.
                call = None
                if cis_profile:
                    call = {'scheduledStopPointRef': point_id,
                            'stopPlaceRef': place_id or '',
                            'journeyPatternRef': pattern.get('id'),
                            'pointInJourneyPatternRef': point.get('id')}
                    if not call['journeyPatternRef'] or not call['pointInJourneyPatternRef']:
                        raise ImportErrorDetail('CIS boarding context requires both pattern identities')
                sid = source_stop_id + ':call:' + digest(call)[:20] if call else source_stop_id
                if call:
                    pattern_id = pattern.get('id')
                    if pattern_id not in pattern_signatures:
                        canonical = ET.canonicalize(ET.tostring(pattern, encoding='unicode'),
                                                    strip_text=True, rewrite_prefixes=True)
                        source_points = [ref(node, 'ScheduledStopPointRef')
                                         for node in descendants(pattern, 'StopPointInJourneyPattern')]
                        names = [[key, text(points.get(key), 'Name'),
                                  text(places.get(assignments.get(key)), 'Name')]
                                 for key in source_points]
                        pattern_signatures[pattern_id] = digest([canonical, names])
                    # Keep the call ID stable; pin reviews to the complete
                    # direction/stop pattern, even when CIS reuses all IDs.
                    call['patternSha256'] = pattern_signatures[pattern_id]
                if sid not in self.stops:
                    self.stops[sid] = len(self.network['stops'])
                    self.network['stops'].append({'id': sid, 'name': name, 'lat': 0, 'lon': 0, 'type': 0,
                        'parent': '', 'zone': '', 'platform': '', 'group': 'ostrava:' + (scope + ':' if scope else '') + (place_id or point_id),
                        'source': 'cis-ostrava', 'coordinatesKnown': False})
                    if call:
                        self.network['stops'][-1].update({'sourceStopId': source_stop_id,
                            'sourceCall': call, 'physicalPlatformVerified': False})
                elif self.network['stops'][self.stops[sid]]['name'] != name:
                    raise ImportErrorDetail('Stop identity has conflicting public names')
                arrival, departure = text(passing, 'ArrivalTime'), text(passing, 'DepartureTime')
                if not arrival and not departure:
                    raise ImportErrorDetail('Missing stop timing')
                a = clock(arrival, text(passing, 'ArrivalDayOffset', '0')) if arrival else None
                d = clock(departure, text(passing, 'DepartureDayOffset', '0')) if departure else None
                no_passengers = text(point, 'StopUse').lower() == 'noboardingoralighting'
                pickup = 0 if bool_value(point, 'ForBoarding') and not no_passengers else 1
                dropoff = 0 if bool_value(point, 'ForAlighting') and not no_passengers else 1
                if bool_value(point, 'RequestStop', False):
                    pickup = 3 if pickup == 0 else pickup
                    dropoff = 3 if dropoff == 0 else dropoff
                rows.append([self.stops[sid], a if a is not None else d, d if d is not None else a, pickup, dropoff, sequence, 1])
            passing_times = descendants(journey, 'TimetabledPassingTime')
            if cis_profile and not any(first(t, 'ArrivalDayOffset') is not None or first(t, 'DepartureDayOffset') is not None for t in passing_times):
                rows = unroll_cis_midnight(rows)
                if rows and rows[-1][1] >= 86400:
                    self.report['inferredMidnightJourneys'] += 1
            source_id = journey.get('id')
            if not source_id:
                raise ImportErrorDetail('Journey has no identity')
            trip_id = 'ostrava:' + (scope + ':' if scope else '') + source_id + ':' + journey.get('version', 'unversioned')
            line_code = presentation['label'] if presentation else text(line, 'ShortName') or text(line, 'PublicCode') or text(line, 'Name')
            metadata = {'source': 'cis-ostrava', 'operators': [self.report['operator']], 'realtime': 'not-connected', 'sourceLineCode': public_code, 'sourceReportedMode': mode}
            if presentation:
                metadata['modeEvidence'] = presentation['evidence']
            route = ['ostrava:' + line.get('id') + ':' + operator_id, line_code, route_type, metadata]
            signature = digest([route, rows, sorted(active)])
            if trip_id in self.journeys:
                if self.journeys[trip_id] != signature:
                    raise ImportErrorDetail('Conflicting versions of an Ostrava journey')
                self.report['duplicateJourneys'] += 1
                continue
            self.journeys[trip_id] = signature
            if cis_profile:
                self.trip_offers[trip_id] = (line.get('id'), scope)
            headsign = self.network['stops'][rows[-1][0]]['name'] if rows else ''
            add_service(self.network, trip_id, route, headsign, text(journey, 'Name'), rows, active)
    def archive(self, filename):
        for root, origin in archive_documents(filename):
            try:
                self.add(root, origin)
            except (ValueError, AttributeError, TypeError) as error:
                raise ImportErrorDetail(origin + ': ' + str(error)) from error
    def export(self):
        for rule in self.resolutions:
            seen = self.resolutions_seen.get(rule['lineCode'], set())
            if seen and rule['semanticSha256'][rule['preferredDocument']] not in seen:
                raise ImportErrorDetail('Reviewed source offer is absent: ' + rule['lineCode'])
        winning = {}
        for line_id, offers in self.offers.items():
            for day in self.network['active']:
                relevant = [(lower, scope) for scope, (lower, upper) in offers.items() if lower <= day <= upper]
                if not relevant:
                    continue
                latest = max(lower for lower, _ in relevant)
                scopes = {scope for lower, scope in relevant if lower == latest}
                if len(scopes) > 1:
                    raise ImportErrorDetail('Ambiguous complete line offers have the same validity start')
                winning[line_id, day] = next(iter(scopes))
        dates_by_service = {}
        for day, services in self.network['active'].items():
            for service in services:
                dates_by_service.setdefault(service, set()).add(day)
        output = blank(min(self.network['active']), max(self.network['active']), SOURCE)
        output['stops'] = self.network['stops']
        removed_days = 0
        for trip in sorted(self.network['trips'], key=lambda value: value[0]):
            active = dates_by_service.get(trip[1], set())
            if trip[0] in self.trip_offers:
                line_id, scope = self.trip_offers[trip[0]]
                selected_days = {day for day in active if winning.get((line_id, day)) == scope}
                removed_days += len(active - selected_days)
                active = selected_days
            if active:
                add_service(output, trip[0], self.network['routes'][trip[2]], trip[3], trip[4],
                            [list(row) for row in trip[5]], active)
        used = sorted({row[0] for trip in output['trips'] for row in trip[5]}, key=lambda index: output['stops'][index]['id'])
        remap = {old: new for new, old in enumerate(used)}
        output['stops'] = [output['stops'][index] for index in used]
        for trip in output['trips']:
            for row in trip[5]:
                row[0] = remap[row[0]]
        report = dict(self.report)
        report['activeTrips'] = len(output['trips'])
        report['isolatedCisCallContexts'] = sum('sourceCall' in stop for stop in output['stops'])
        report['physicalPlatformMappingsVerified'] = 0
        report['modes'] = sorted({route[2] for route in output['routes']})
        report['modeCorrectedLines'] = sorted(self.mode_corrections)
        report['supersededServiceDays'] = removed_days
        report['referenceCheckCompleteness'] = 'selected_departures_only'
        report['activeMidnightJourneys'] = sum(bool(trip[5]) and trip[5][-1][1] >= 86400 for trip in output['trips'])
        output['coverage'] = report
        output['candidateOnly'] = True
        return finalize(output), report
