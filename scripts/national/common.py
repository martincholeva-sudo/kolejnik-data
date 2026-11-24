from __future__ import annotations
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

class ImportErrorDetail(ValueError):
    pass

def local(tag):
    return tag.rsplit('}', 1)[-1]

def children(node, name):
    return [child for child in node if local(child.tag) == name]

def first(node, name):
    return next((child for child in node if local(child.tag) == name), None) if node is not None else None

def text(node, name, default=''):
    item = first(node, name)
    return (item.text or '').strip() if item is not None else default

def descendants(node, name):
    return [item for item in node.iter() if local(item.tag) == name]

def ref(node, name):
    item = first(node, name)
    return item.get('ref', '') if item is not None else ''

def iso_day(value):
    if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}(?:$|T)', value):
        raise ImportErrorDetail('Invalid service date: ' + str(value))
    return dt.date.fromisoformat(value[:10])

def bitmap(start, end, bits):
    start, end = iso_day(start), iso_day(end)
    count = (end - start).days + 1
    if count < 1 or count > 800 or len(bits) != count or set(bits) - {'0', '1'}:
        raise ImportErrorDetail('Calendar bitmap does not match its declared period')
    return {(start + dt.timedelta(days=i)).isoformat() for i, bit in enumerate(bits) if bit == '1'}

def clock(value, offset=0):
    match = re.fullmatch(r'(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?', value)
    if not match:
        raise ImportErrorDetail('Unsupported clock: ' + value)
    h, m, s = map(int, match.groups())
    if h > 23 or m > 59 or s > 59 or not -2 <= int(offset) <= 3:
        raise ImportErrorDetail('Invalid clock or service-day offset')
    return h * 3600 + m * 60 + s + int(offset) * 86400

def instant(value, allow_local=False):
    """Keep unzoned CIS revision times in the source clock; never assume UTC."""
    value = value.strip()
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})?', value)
    if not match:
        raise ImportErrorDetail('Invalid creation/cancellation timestamp')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ImportErrorDetail('Invalid creation/cancellation timestamp') from error
    suffix = ''
    if parsed.tzinfo is None:
        if not allow_local:
            raise ImportErrorDetail('Creation/cancellation timestamp needs an explicit timezone')
        # The source uses local wall time. Do not guess chronology in a Czech
        # clock-change hour; an explicit offset is required for that case.
        if parsed.month in (3, 10) and parsed.weekday() == 6 and parsed.day >= 25 and parsed.hour == 2:
            raise ImportErrorDetail('Clock-change timestamp needs an explicit timezone')
    else:
        parsed = parsed.astimezone(dt.timezone.utc)
        suffix = '+00:00'
    # Preserve .NET's seventh fractional digit (and up to nanoseconds), which
    # datetime alone would truncate and could merge distinct revisions.
    fraction = (match.group(2) or '').ljust(9, '0')
    return parsed.strftime('%Y-%m-%dT%H:%M:%S') + '.' + fraction + suffix


def instant_order(left, right):
    if left.endswith('+00:00') != right.endswith('+00:00'):
        raise ImportErrorDetail('Mixed source-local and UTC revision clocks need an explicit source timezone')
    return (left > right) - (left < right)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def sha_file(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()

def documents(path, max_xml_bytes=16_000_000, xml_header_filter=None):
    """Read bounded XML/ZIP/GZIP inputs without extracting archive paths."""
    total = 0
    def parse(raw, label):
        nonlocal total
        total += len(raw)
        if len(raw) > max_xml_bytes or total > 4_000_000_000:
            raise ImportErrorDetail('XML input exceeds the preparation limit')
        if b'\x00' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ImportErrorDetail('DTD/entity declarations are not accepted')
        return ET.fromstring(raw), label
    def visit(stream, label, depth):
        if depth > 3:
            raise ImportErrorDetail('Archive nesting exceeds the preparation limit')
        signature = stream.read(4)
        stream.seek(0)
        if signature[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=stream) as compressed:
                raw = compressed.read(max_xml_bytes + 1)
            if len(raw) > max_xml_bytes:
                raise ImportErrorDetail('GZIP content exceeds the XML preparation limit')
            yield from visit(io.BytesIO(raw), label + '!gzip', depth + 1)
        elif signature[:2] == b'PK':
            with zipfile.ZipFile(stream) as archive:
                for info in sorted(archive.infolist(), key=lambda value: value.filename):
                    name = info.filename.replace('\\', '/')
                    if name.startswith('/') or '..' in name.split('/'):
                        raise ImportErrorDetail('Unsafe archive entry name')
                    if info.is_dir() or not name.lower().endswith(('.xml', '.zip', '.gz')):
                        continue
                    limit = 512_000_000 if name.lower().endswith('.zip') else max_xml_bytes
                    if xml_header_filter is not None and name.lower().endswith('.xml'):
                        with archive.open(info) as member:
                            prefix = member.read(min(info.file_size, 65536))
                            if xml_header_filter(prefix) is False:
                                continue
                            if info.file_size > limit:
                                raise ImportErrorDetail('Oversized archive member: ' + name)
                            raw = prefix + member.read(limit + 1 - len(prefix))
                    else:
                        if info.file_size > limit:
                            raise ImportErrorDetail('Oversized archive member: ' + name)
                        raw = archive.read(info)
                    yield from visit(io.BytesIO(raw), label + '!' + name, depth + 1)
        else:
            yield parse(stream.read(max_xml_bytes + 1), label)
    with Path(path).open('rb') as stream:
        yield from visit(stream, str(path), 0)

def blank(start, end, source):
    days = (iso_day(end) - iso_day(start)).days + 1
    if not 1 <= days <= 32:
        raise ImportErrorDetail('Prepare between 1 and 32 service days at a time')
    return {'schema': 2, 'source': source, 'attribution': 'CIS JŘ / Ministerstvo dopravy; převedeno Kolejníkem',
            'start': start, 'end': end, 'stops': [], 'routes': [], 'trips': [], 'paths': [], 'transfers': [],
            'active': {(iso_day(start) + dt.timedelta(days=i)).isoformat(): [] for i in range(days)}}

def add_service(network, trip_id, route, headsign, number, rows, dates):
    dates = sorted(set(dates) & network['active'].keys())
    if not dates or len(rows) < 2:
        return
    if any(row[1] < 0 or row[2] < row[1] or (i and row[1] < rows[i-1][2]) for i, row in enumerate(rows)):
        raise ImportErrorDetail('Non-monotonic public journey: ' + trip_id)
    if not any(row[3] in (0, 3) for row in rows[:-1]) or not any(row[4] in (0, 3) for row in rows[1:]):
        return
    cache = network.get('_routeIndexes')
    if cache is None:
        cache = {digest(old): i for i, old in enumerate(network['routes'])}
        network['_routeIndexes'] = cache
    key = digest(route)
    route_index = cache.get(key)
    if route_index is None:
        route_index = len(network['routes']); network['routes'].append(route); cache[key] = route_index
    service = trip_id + ':calendar'
    network['trips'].append([trip_id, service, route_index, headsign, number, rows])
    for day in dates:
        network['active'][day].append(service)

def finalize(network):
    network.pop('_routeIndexes', None)
    network.pop('sha256', None)
    for day in network['active']:
        network['active'][day] = sorted(set(network['active'][day]))
    network['sha256'] = digest(network)
    return network
