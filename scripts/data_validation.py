"""Validate a complete revision before publication and check real served bundles."""
import datetime as dt, gzip, json, re, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_AGE=dt.timedelta(hours=48)
def timestamp(m):
    value=m.get('checkedAt') or m.get('importedAt')
    try:
        t=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if t.tzinfo is None: raise ValueError()
        return t
    except (ValueError,TypeError,AttributeError): raise ValueError('Missing or invalid source verification timestamp')

def validate_manifest(m,today=None,now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    today=today or now.astimezone(ZoneInfo('Europe/Prague')).date().isoformat()
    if m.get('schema')!=2 or not re.fullmatch('[a-f0-9]{64}',m.get('sha256','')): raise ValueError('Invalid manifest schema or revision')
    start=dt.date.fromisoformat(m['start']);end=dt.date.fromisoformat(m['end'])
    if not m['start']<=today<=m['end']: raise ValueError('Timetable does not cover today')
    days=m.get('days',{})
    expected={(start+dt.timedelta(days=n)).isoformat() for n in range((end-start).days+1)}
    if len(days)<7 or set(days)!=expected: raise ValueError('Incomplete timetable horizon')
    for day,name in days.items():
        if name!=m['sha256'][:12]+'-'+day+'.json.gz': raise ValueError('Invalid or mismatched daily bundle name')
    checked=timestamp(m)
    if checked>now+dt.timedelta(minutes=5) or now-checked>MAX_AGE: raise ValueError('Source verification is stale or in the future')
    return today

def validate_bundle(raw,m,day,min_stops=10000,min_trips=10000):
    if raw.get('schema')!=2 or raw.get('sha256')!=m['sha256'] or day not in raw.get('active',{}): raise ValueError('Bundle does not match the published revision or service day')
    if len(raw.get('stops',[]))<=min_stops or len(raw.get('trips',[]))<=min_trips: raise ValueError('Suspiciously incomplete PID feed')

def validate_public(folder,manifest=None,today=None,now=None,min_stops=10000,min_trips=10000):
    folder=Path(folder);m=manifest or json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    validate_manifest(m,today,now)
    for day,name in m['days'].items():
        with gzip.open(folder/name,'rt',encoding='utf-8') as stream: raw=json.load(stream)
        validate_bundle(raw,m,day,min_stops,min_trips)
    if json.loads((folder/'walking-links.json').read_text(encoding='utf-8')).get('schema')!=1: raise ValueError('Invalid walking links')
    return m

def fetch(url):
    req=urllib.request.Request(url,headers={'Cache-Control':'no-cache','User-Agent':'Kolejnik-validator/0.6.8'})
    with urllib.request.urlopen(req,timeout=12) as response:
        data=response.read(30_000_001)
    if len(data)>30_000_000: raise ValueError('Oversized published bundle')
    return data

def verify_deployed(expected,base='https://kolejnik.cz/spojeni/data-v2/',fetch_bytes=fetch,today=None,now=None,min_stops=10000,min_trips=10000):
    today=today or dt.datetime.now(ZoneInfo('Europe/Prague')).date().isoformat()
    m=json.loads(fetch_bytes(base+'manifest.json?verify='+str(int(dt.datetime.now().timestamp()))))
    validate_manifest(m,today,now)
    if m['sha256']!=expected['sha256'] or timestamp(m)<timestamp(expected): raise ValueError('The latest validated manifest has not reached the public host')
    name=m['days'][today];raw=json.loads(gzip.decompress(fetch_bytes(base+name)))
    validate_bundle(raw,m,today,min_stops,min_trips)
    walking=json.loads(fetch_bytes(base+'walking-links.json'))
    if walking.get('schema')!=1: raise ValueError('Published walking links are missing or invalid')
    return m
