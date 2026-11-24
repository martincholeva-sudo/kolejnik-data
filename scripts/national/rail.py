"""CIS rail import; no allow-list of passenger carriers. Source: DJŘ_CIS 1.09.06."""
from collections import defaultdict
import datetime as dt
import json
import hashlib
import xml.etree.ElementTree as ET
from .common import (ImportErrorDetail, local, children, first, text, descendants,
                     iso_day, bitmap, clock, instant, instant_order, digest, documents, blank, add_service, finalize)
SOURCE = 'https://portal.cisjr.cz/pub/draha/celostatni/szdc/'

def identifiers(root):
    result = {}
    def visit(node):
        if local(node.tag).startswith('Related'):
            return
        kind = text(node, 'ObjectType')
        if kind in ('PA', 'TR'):
            fields = [text(node, name) for name in ('ObjectType', 'Company', 'Core', 'Variant', 'TimetableYear', 'StartDate')]
            if not all(fields[:5]):
                raise ImportErrorDetail('Incomplete train/path identity')
            value = json.dumps(fields, separators=(',', ':'))
            if kind in result and result[kind] != value:
                raise ImportErrorDetail('Ambiguous path identity')
            result[kind] = value
        for child in node:
            visit(child)
    visit(root)
    if set(result) != {'PA', 'TR'}:
        raise ImportErrorDetail('CIS message needs PA and TR identifiers')
    return result

def calendar(root, partial=False):
    found = descendants(root, 'PlannedCalendar')
    if len(found) != 1:
        raise ImportErrorDetail('Expected one PlannedCalendar')
    value = found[0]
    validity = first(value, 'ValidityPeriod')
    if partial:
        day = text(value, 'StartDate') or text(validity, 'StartDate') or text(validity, 'StartDateTime')
        return {iso_day(day).isoformat()}
    start = text(validity, 'StartDateTime') or text(validity, 'StartDayTime')
    end = text(validity, 'EndDateTime') or text(validity, 'EndDayTime')
    bits = text(value, 'BitmapDays') or text(value, 'Bitmapdays')
    return bitmap(start, end, bits)

def location(node):
    if node is None:
        raise ImportErrorDetail('Missing station identity')
    value = first(node, 'Location')
    if value is None:
        value = node
    country = text(value, 'CountryCodeISO')
    code = text(value, 'LocationPrimaryCode') or text(value, 'PrimaryLocationCode')
    if not country or not code:
        raise ImportErrorDetail('Missing station country or primary code')
    return country + ':' + code

def timings(node):
    value = {}
    for item in descendants(node, 'Timing'):
        kind = item.get('TimingQualifierCode', '')
        if kind not in ('ALA', 'ALD'):
            raise ImportErrorDetail('Unknown timing qualifier: ' + kind)
        if kind in value:
            raise ImportErrorDetail('Duplicate timing')
        value[kind] = clock(text(item, 'Time'), text(item, 'Offset', '0'))
    if not value:
        raise ImportErrorDetail('Missing station timing')
    return value.get('ALA', value.get('ALD')), value.get('ALD', value.get('ALA'))

def track_change_annotation(node, points):
    """DJR_CIS 1.09.06 section 5.2: untimed duplicate for departure track."""
    if not points or descendants(node, 'Timing'):
        return None
    if [local(child.tag) for child in node] != ['Location']:
        return None
    station = first(node, 'Location')
    allowed_fields = {'CountryCodeISO', 'LocationPrimaryCode', 'PrimaryLocationCode',
                      'PrimaryLocationName', 'LocationPrimaryName', 'LocationSubsidiaryIdentification'}
    if any(local(child.tag) not in allowed_fields for child in station):
        return None
    if location(node) != points[-1]['id']:
        return None
    subsidiary = children(station, 'LocationSubsidiaryIdentification')
    if len(subsidiary) != 1:
        return None
    code = first(subsidiary[0], 'LocationSubsidiaryCode')
    company = text(subsidiary[0], 'AllocationCompany')
    if code is None or code.get('LocationSubsidiaryTypeCode') != '1' or not (code.text or '').strip():
        return None
    return {'pointIndex': len(points) - 1, 'station': points[-1]['id'],
            'departureTrack': (code.text or '').strip(), 'allocationCompany': company}


def point_flag(node, name):
    values = [text(item, 'Value') for item in descendants(node, 'NetworkSpecificParameter')
              if text(item, 'Name') == name]
    if len(values) > 1 or any(value not in ('0', '1') for value in values):
        raise ImportErrorDetail('Invalid or repeated rail point flag: ' + name)
    return values == ['1']


def parse(root):
    kind = local(root.tag)
    if kind not in ('CZPTTCISMessage', 'CZCanceledPTTMessage'):
        raise ImportErrorDetail('Unsupported CIS message: ' + kind)
    ids = identifiers(root)
    canceled = kind == 'CZCanceledPTTMessage'
    stamp = instant(text(root, 'CZPTTCancelation' if canceled else 'CZPTTCreation'), allow_local=True)
    sections = descendants(root, 'CZDeactivatedSection')
    if len(sections) > 1:
        raise ImportErrorDetail('Multiple deactivated sections')
    record = {'key': ids['PA'], 'train': ids['TR'], 'created': stamp, 'cancel': canceled,
              'days': sorted(calendar(root, bool(sections)))}
    if canceled:
        record['section'] = [location(first(sections[0], name)) for name in ('StartLocation', 'EndLocation')] if sections else None
        return record
    info = first(root, 'CZPTTInformation')
    if info is None:
        raise ImportErrorDetail('Missing train information')
    points = []
    track_annotations = []
    for node in children(info, 'CZPTTLocation'):
        annotation = track_change_annotation(node, points)
        if annotation is not None:
            track_annotations.append(annotation)
            continue
        station = first(node, 'Location')
        activities = {text(item, 'TrainActivityType') for item in children(node, 'TrainActivity')}
        arrival, departure = timings(node)
        passenger = bool(activities & {'0001', '0028', '0029', '0030'})
        pickup = 0 if passenger and '0029' not in activities else 1
        dropoff = 0 if passenger and '0028' not in activities else 1
        if activities & {'CZ26', 'CZ01'}:
            raise ImportErrorDetail('Conditional passenger stop requires an explicit calendar')
        public = text(node, 'TrainType') == '1'
        if public and not text(node, 'ResponsibleRU'):
            raise ImportErrorDetail('Missing ResponsibleRU on a public segment')
        name = text(station, 'PrimaryLocationName') or text(station, 'LocationPrimaryName')
        if not name:
            raise ImportErrorDetail('Missing station name')
        points.append({'id': location(node), 'name': name, 'arrival': arrival, 'departure': departure,
                       'pickup': pickup, 'dropoff': dropoff, 'public': public,
                       'operator': text(node, 'ResponsibleRU'), 'number': text(node, 'OperationalTrainNumber'),
                       'replacementTransport': point_flag(node, 'CZAlternativeTransport'),
                       'inconsistentTime': point_flag(node, 'CZInconsistentTime'),
                       'sourceDwellTime': text(first(node, 'TimingAtLocation'), 'DwellTime')})
    if len(points) < 2:
        raise ImportErrorDetail('Train path has fewer than two points')
    record['points'] = points
    if track_annotations:
        record['trackChangeAnnotations'] = track_annotations
    return record

class RailMessages:
    def __init__(self, start=None, end=None):
        self.paths = {}
        self.cancellations = defaultdict(dict)
        self.documents = 0
        self.inactive_cancellations = 0
        self.horizon = None
        if start is not None or end is not None:
            if start is None or end is None:
                raise ImportErrorDetail('Both rail horizon endpoints are required')
            first_day, last_day = iso_day(start), iso_day(end)
            count = (last_day - first_day).days + 1
            if not 1 <= count <= 32:
                raise ImportErrorDetail('Rail horizon must contain 1 to 32 days')
            self.horizon = {(first_day + dt.timedelta(days=i)).isoformat() for i in range(count)}

    def add(self, root):
        if self.horizon is not None and local(root.tag) == 'CZCanceledPTTMessage':
            # A cancellation outside the exported days cannot affect a journey
            # in this horizon. Validate its calendar without guessing an old
            # ambiguous local revision clock or retaining annual calendars.
            if not self.horizon.intersection(calendar(root, bool(descendants(root, 'CZDeactivatedSection')))):
                self.documents += 1
                self.inactive_cancellations += 1
                return
        if self.horizon is not None and local(root.tag) == 'CZPTTCISMessage' and not self.horizon.intersection(calendar(root)):
            ids = identifiers(root)
            canonical = ET.canonicalize(ET.tostring(root, encoding='unicode'), strip_text=True, rewrite_prefixes=True)
            record = {'key': ids['PA'], 'train': ids['TR'],
                      'created': instant(text(root, 'CZPTTCreation'), allow_local=True),
                      'cancel': False, 'days': [], 'points': [],
                      'semanticIdentity': 'xml:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()}
        else:
            record = parse(root)
        if self.horizon is not None:
            # Preserve complete semantics for same-timestamp conflict checks,
            # even when a newer inactive path must replace an older active one.
            if 'semanticIdentity' not in record:
                record['semanticIdentity'] = digest(record)
            record['days'] = sorted(self.horizon.intersection(record['days']))
            if not record['cancel'] and not record['days']:
                record['points'] = []
        self.documents += 1
        key = record['key']
        related = list(self.cancellations.get(key, {}).values())
        if key in self.paths:
            related.append(self.paths[key])
        for prior in related:
            instant_order(record['created'], prior['created'])
        if record['cancel']:
            self.cancellations[key][digest(record)] = record
            return
        old = self.paths.get(key)
        if old and old['train'] != record['train']:
            raise ImportErrorDetail('One path belongs to multiple train identities')
        if old and old['created'] == record['created'] and old != record:
            raise ImportErrorDetail('Conflicting messages with the same timestamp')
        if not old or instant_order(record['created'], old['created']) > 0:
            self.paths[key] = record

    def archive(self, filename):
        for root, origin in documents(filename):
            try:
                self.add(root)
            except (ValueError, TypeError, AttributeError) as error:
                raise ImportErrorDetail(origin + ': ' + str(error)) from error

    def export(self, start, end, operator_names=None, allow_incomplete=False):
        network = blank(start, end, SOURCE)
        def register_counter_service(trip_id, route, headsign, number, rows, dates):
            # Ordinary GTFS stays strict in common.add_service. CIS section 5.12
            # explicitly permits these declared overlaps; validate both event
            # streams below before registering their original, unchanged rows.
            dates = sorted(set(dates) & network['active'].keys())
            seen_pickup = False
            rideable = False
            for row in rows:
                if seen_pickup and row[4] in (0, 3):
                    rideable = True
                    break
                seen_pickup = seen_pickup or row[3] in (0, 3)
            if not dates or not rideable:
                return
            route_index = len(network['routes'])
            network['routes'].append(route)
            if '_routeIndexes' in network:
                network['_routeIndexes'][digest(route)] = route_index
            service = trip_id + ':calendar'
            network['trips'].append([trip_id, service, route_index, headsign, number, rows])
            for day in dates:
                network['active'][day].append(service)
        if self.horizon is not None and not set(network['active']).issubset(self.horizon):
            raise ImportErrorDetail('Cannot export outside the retained rail horizon')
        unmatched_full = []
        for key, messages in self.cancellations.items():
            if key not in self.paths:
                relevant = [message for message in messages.values() if set(message['days']) & network['active'].keys()]
                if any(message['section'] is not None for message in relevant):
                    raise ImportErrorDetail('Partial cancellation references a missing base path in this horizon')
                # A full cancellation is a tombstone for this exact PA, even
                # if its original path is absent from the published collection.
                # It creates no journey and never cancels another PA of that TR.
                if relevant:
                    unmatched_full.append(key)
        indexes = {}
        names = operator_names or {}
        report = {'documents': self.documents, 'pathIdentities': len(self.paths),
                  'cancelMessages': sum(map(len, self.cancellations.values())),
                  'inactiveCancellationsOutsideHorizon': self.inactive_cancellations,
                  'fullCancellationPathsWithoutBase': sorted(unmatched_full),
                  'publicOperatorsSeen': set(), 'activeOperatorsExported': set(),
                  'fullCancelledServiceDays': 0, 'partialCancelledServiceDays': 0,
                  'privateSegmentsExcluded': 0, 'duplicatePublicJourneysMerged': 0,
                  'replacementJourneyVariants': 0, 'unrepresentableJourneys': [],
                  'completeTimingsExported': True, 'nonPassengerTimingPointVisitsExcluded': 0,
                  'declaredCounterTimeJourneyVariants': 0}
        pending = {}
        for key, record in sorted(self.paths.items()):
            for day in sorted(set(record['days']) & network['active'].keys()):
                points = record['points']
                allowed = [point['public'] for point in points[:-1]]
                for i, point in enumerate(points[:-1]):
                    if allowed[i]:
                        report['publicOperatorsSeen'].add(point['operator'])
                    elif not point['public']:
                        report['privateSegmentsExcluded'] += 1
                full_cancel = False
                for cancel in self.cancellations.get(key, {}).values():
                    if cancel['train'] != record['train']:
                        raise ImportErrorDetail('Cancellation TR does not match its PA path')
                    if instant_order(cancel['created'], record['created']) < 0 or day not in cancel['days']:
                        continue
                    section = cancel['section']
                    if section is None:
                        full_cancel = True
                        break
                    starts = [i for i, p in enumerate(points) if p['id'] == section[0]]
                    ends = [i for i, p in enumerate(points) if p['id'] == section[1]]
                    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
                        raise ImportErrorDetail('Partial cancellation has ambiguous/reversed endpoints')
                    if starts[0] != 0 and ends[0] != len(points)-1:
                        raise ImportErrorDetail('Unsupported interior cancellation')
                    for i in range(starts[0], ends[0]):
                        allowed[i] = False
                    report['partialCancelledServiceDays'] += 1
                if full_cancel:
                    report['fullCancelledServiceDays'] += 1
                    continue
                i = 0
                while i < len(allowed):
                    if not allowed[i]:
                        i += 1
                        continue
                    beginning = i
                    replacement = points[beginning].get('replacementTransport', False)
                    while i < len(allowed) and allowed[i] and points[i].get('replacementTransport', False) == replacement:
                        i += 1
                    segment = points[beginning:i+1]
                    domestic = [j for j, p in enumerate(segment) if p['id'].startswith('CZ:')]
                    if len(domestic) < 2:
                        continue
                    segment = segment[domestic[0]:domestic[-1]+1]
                    operators = sorted({p['operator'] for p in segment[:-1]})
                    operator_runs = []
                    for point in segment[:-1]:
                        if not operator_runs or operator_runs[-1] != point['operator']:
                            operator_runs.append(point['operator'])
                    # Preserve the full source path for cancellation boundaries,
                    # mode changes and carrier responsibility above. Public
                    # timetables contain only passenger stops (DJR_CIS 8.5).
                    passenger_points = [point for point in segment
                                        if point['pickup'] in (0, 3) or point['dropoff'] in (0, 3)]
                    report['nonPassengerTimingPointVisitsExcluded'] += len(segment) - len(passenger_points)
                    segment = passenger_points
                    if len(segment) < 2:
                        continue
                    replacement_scope = digest([record['train'], operator_runs,
                        [[p['id'], p['arrival'], p['departure'], p['pickup'], p['dropoff'],
                          p.get('inconsistentTime', False), p.get('sourceDwellTime', '')]
                         for p in segment], segment[0]['number']])[:20] if replacement else ''
                    rows = []
                    for sequence, point in enumerate(segment, 1):
                        source_stop_id = 'czrail:' + point['id']
                        sid = source_stop_id + ':replacement:' + replacement_scope if replacement else source_stop_id
                        if sid not in indexes:
                            indexes[sid] = len(network['stops'])
                            network['stops'].append({'id': sid, 'name': point['name'], 'lat': 0, 'lon': 0,
                                'type': 0 if point['id'].startswith('CZ:') else 3, 'parent': '', 'zone': '', 'platform': '', 'group': source_stop_id,
                                'source': 'cis-rail', 'coordinatesKnown': False})
                            if replacement:
                                network['stops'][-1].update({'sourceStopId': source_stop_id,
                                    'boardingMode': 'replacement', 'physicalPlatformVerified': False})
                        elif network['stops'][indexes[sid]]['name'] != point['name']:
                            raise ImportErrorDetail('One station identity has conflicting names')
                        rows.append([indexes[sid], point['arrival'], point['departure'], point['pickup'] if point['id'].startswith('CZ:') else 1, point['dropoff'] if point['id'].startswith('CZ:') else 1, sequence, 1])
                    counter_times = [{'sequence': row[5], 'arrival': row[1], 'departure': row[2],
                                      'dwellTime': point.get('sourceDwellTime', '')}
                                     for point, row in zip(segment, rows)
                                     if point.get('inconsistentTime', False) and row[2] < row[1]]
                    signature = digest([record['train'], operator_runs, [p['id'] for p in segment], [r[1:] for r in rows], segment[0]['number'], replacement])
                    if counter_times:
                        signature = digest([signature, counter_times])
                    if signature not in pending:
                        metadata = {'source': 'cis-rail',
                            'operators': [{'id': code, 'name': names.get(code, '')} for code in operators],
                            'realtime': 'not-connected', 'trainIdentity': record['train'],
                            'replacementTransport': replacement, 'replacementVehicleType': 'unknown' if replacement else None}
                        if counter_times:
                            metadata['declaredCounterTimes'] = counter_times
                        label = ('NAD ' if replacement else '') + (segment[0]['number'] or 'Vlak')
                        route = ['czrail:route:' + signature[:20], label, 2, metadata]
                        pending[signature] = {'route': route, 'rows': rows, 'headsign': segment[-1]['name'],
                                              'number': segment[0]['number'], 'days': set(), 'pathKeys': set(),
                                              'sourcePoints': segment}
                    elif day in pending[signature]['days']:
                        report['duplicatePublicJourneysMerged'] += 1
                    pending[signature]['days'].add(day)
                    pending[signature]['pathKeys'].add(key)
        for signature, journey in sorted(pending.items()):
            rows = journey['rows']
            counter_times = journey['route'][3].get('declaredCounterTimes', [])
            counter_sequences = {point['sequence'] for point in counter_times}
            invalid = [i for i, row in enumerate(rows)
                       if row[1] < 0 or row[2] < 0
                       or (row[2] < row[1] and row[5] not in counter_sequences)
                       or (i and (row[1] < rows[i-1][2] or row[1] < rows[i-1][1]
                                  or row[2] < rows[i-1][2]))]
            if invalid:
                issue = {'trainIdentity': journey['route'][3]['trainIdentity'],
                         'pathIdentities': sorted(journey['pathKeys']), 'number': journey['number'],
                         'serviceDays': sorted(journey['days']),
                         'points': [{'station': journey['sourcePoints'][i]['id'],
                                     'arrival': rows[i][1], 'departure': rows[i][2],
                                     'sourceDeclaresInconsistentTime': journey['sourcePoints'][i].get('inconsistentTime', False),
                                     'sourceDwellTime': journey['sourcePoints'][i].get('sourceDwellTime', '')}
                                    for i in invalid]}
                report['unrepresentableJourneys'].append(issue)
                report['completeTimingsExported'] = False
                if not allow_incomplete:
                    raise ImportErrorDetail('Non-monotonic public journey requires source-time review: '
                                            + journey['number'] + '; ' + json.dumps(issue, ensure_ascii=False))
                continue
            before = len(network['trips'])
            if counter_times:
                register_counter_service('czrail:trip:' + signature[:24], journey['route'], journey['headsign'],
                                         journey['number'], rows, journey['days'])
            else:
                add_service(network, 'czrail:trip:' + signature[:24], journey['route'], journey['headsign'],
                            journey['number'], rows, journey['days'])
            if len(network['trips']) > before:
                if counter_times:
                    report['declaredCounterTimeJourneyVariants'] += 1
                report['activeOperatorsExported'].update(operator['id'] for operator in journey['route'][3]['operators'])
                if journey['route'][3]['replacementTransport']:
                    report['replacementJourneyVariants'] += 1
        if report['unrepresentableJourneys']:
            network['candidateOnly'] = True
            network['incompleteReasons'] = ['Unrepresentable source rail timings; see coverage.unrepresentableJourneys']
        report['publicOperatorsSeen'] = sorted(report['publicOperatorsSeen'])
        report['activeOperatorsExported'] = sorted(report['activeOperatorsExported'])
        report['activeTrips'] = len(network['trips'])
        report['realSourceValidated'] = False
        report['liveDelayCoverage'] = 'not-connected'
        network['coverage'] = report
        return finalize(network), report
