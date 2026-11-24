"""Validate a whole national revision before atomically replacing its public pointer."""
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from zoneinfo import ZoneInfo
from .common import ImportErrorDetail, digest, iso_day
from .bundle import write_candidate

MAX_BYTES = 20_000_000
# Conservative lower bounds reject truncated collections before replacing a working revision.
MINIMUMS = {'pid':10000, 'rail':5000, 'ostrava':1000, 'stops':10000, 'dailyTrips':10000}


def source_receipt(inputs, now):
    if inputs.get('schema') != 1 or inputs.get('sourcePlanComplete') is not True or inputs.get('synthetic') is True or set(inputs.get('sources',{})) != {'pid','rail','ostrava'}:
        raise ImportErrorDetail('A complete real source collection is required')
    summary = {}
    stamps = []
    for kind, rows in inputs['sources'].items():
        if not rows:
            raise ImportErrorDetail('Empty source section: '+kind)
        times = []
        for row in rows:
            try:
                stamp = dt.datetime.fromisoformat(row['verifiedAt'].replace('Z','+00:00'))
                if stamp.tzinfo is None or stamp > now+dt.timedelta(minutes=5) or now-stamp > dt.timedelta(hours=48):
                    raise ValueError('stale verification')
                if row.get('httpStatus') not in (200,304) or not re.fullmatch('[a-f0-9]{64}',row['sha256']):
                    raise ValueError('unverified receipt')
            except (KeyError,ValueError,TypeError,AttributeError) as error:
                raise ImportErrorDetail('A source was not recently HTTP-verified: '+kind) from error
            times.append(stamp)
        stamps.extend(times)
        summary[kind] = {'files':len(rows),'oldestVerifiedAt':min(times).isoformat()}
    return min(stamps).isoformat(), summary


def file_record(path):
    raw = Path(path).read_bytes()
    if len(raw)>MAX_BYTES:
        raise ImportErrorDetail('Daily national file exceeds the Android download limit')
    return {'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}


def validate(folder, metadata, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    folder = Path(folder)
    if metadata.get('schema')!=2 or metadata.get('scope')!='cz-rail-ostrava' or metadata.get('candidateOnly') is not True or metadata.get('channel')!='national-preview':
        raise ImportErrorDetail('Wrong national publication channel')
    if not re.fullmatch('[a-f0-9]{64}',metadata.get('sha256','')):
        raise ImportErrorDetail('Invalid national revision')
    first,last=iso_day(metadata['start']),iso_day(metadata['end'])
    expected={(first+dt.timedelta(days=i)).isoformat() for i in range((last-first).days+1)}
    today=now.astimezone(ZoneInfo('Europe/Prague')).date().isoformat()
    if not metadata['start']<=today<=metadata['end'] or not 1<=len(expected)<=30 or set(metadata.get('days',{}))!=expected:
        raise ImportErrorDetail('Incomplete or expired national horizon')
    if set(metadata.get('sourceCoverage',{}))!={'pid','rail','ostrava'} or any(metadata['sourceCoverage'][k].get('trips',0)<MINIMUMS[k] for k in ['pid','rail','ostrava']):
        raise ImportErrorDetail('Missing national source coverage')
    try:
        checked=dt.datetime.fromisoformat(metadata['checkedAt'].replace('Z','+00:00'))
        if checked.tzinfo is None or checked>now+dt.timedelta(minutes=5) or now-checked>dt.timedelta(hours=48):
            raise ValueError('invalid verification time')
    except (KeyError,ValueError,TypeError) as error:
        raise ImportErrorDetail('National sources are not fresh') from error
    for day,name in metadata['days'].items():
        if name != metadata['sha256'][:12]+'-'+day+'.json.gz' or metadata['files'].get(name)!=file_record(folder/name):
            raise ImportErrorDetail('Missing, changed or mismatched daily file')
        with gzip.open(folder/name,'rt',encoding='utf-8') as stream:
            raw=json.load(stream)
        if raw.get('sha256')!=metadata['sha256'] or raw.get('scope')!='cz-rail-ostrava' or raw.get('candidateOnly') is not True or day not in raw.get('active',{}) or len(raw.get('trips',[]))<MINIMUMS['dailyTrips'] or len(raw.get('stops',[]))<MINIMUMS['stops']:
            raise ImportErrorDetail('Invalid or empty daily national bundle')
        if raw.get('sourceCoverage')!=metadata['sourceCoverage']:
            raise ImportErrorDetail('Daily source coverage differs from the manifest')
    walking=metadata['walking']
    if not re.fullmatch(r'walking-[a-f0-9]{16}\.json',walking['file']) or walking['sha256'][:16] not in walking['file']:
        raise ImportErrorDetail('Invalid versioned walking filename')
    if file_record(folder/walking['file'])!={k:walking[k] for k in ['sha256','bytes']} or json.loads((folder/walking['file']).read_text(encoding='utf-8')).get('schema')!=1:
        raise ImportErrorDetail('Missing or changed walking data')
    return metadata


def publish(network, report, inputs, public, runtime, now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    checked,sources=source_receipt(inputs,now)
    if network.get('candidateOnly') is not True or network.get('scope')!='cz-rail-ostrava' or report.get('diagnosticIncompleteTimingsAllowed') is not False or report.get('completeRailTimingsExported') is not True:
        raise ImportErrorDetail('Incomplete or diagnostic candidates cannot be published')
    if digest({k:v for k,v in network.items() if k!='sha256'})!=network.get('sha256'):
        raise ImportErrorDetail('Prepared national content differs from its revision')
    coverage=network.get('sourceCoverage',{})
    if set(coverage)!={'pid','rail','ostrava'} or any(coverage[k].get('trips',0)<MINIMUMS[k] for k in MINIMUMS if k in coverage):
        raise ImportErrorDetail('National source coverage is incomplete or unexpectedly small')
    if len(network.get('trips',[]))!=sum(v['trips'] for v in coverage.values()) or len(network.get('stops',[]))<MINIMUMS['stops']:
        raise ImportErrorDetail('National source counts differ from the prepared network')
    if all('stops' in v for v in coverage.values()) and len(network['stops'])!=sum(v['stops'] for v in coverage.values()):
        raise ImportErrorDetail('National stop counts differ from the prepared network')
    public,runtime=Path(public).resolve(),Path(runtime).resolve()
    if public.name in {'data','data-v2'}:
        raise ImportErrorDetail('The national preview cannot overwrite production PID')
    public.mkdir(parents=True,exist_ok=True);runtime.mkdir(parents=True,exist_ok=True)
    old=json.loads((public/'manifest.json').read_text(encoding='utf-8')) if (public/'manifest.json').exists() else {}
    with tempfile.TemporaryDirectory(prefix='national-stage-',dir=runtime) as directory:
        stage=Path(directory)
        metadata=write_candidate(network,stage,report)
        metadata['days']={d:n for d,n in metadata['days'].items() if network['start']<=d<=network['end']}
        metadata.update({'preview':True,'channel':'national-preview','checkedAt':checked,'importedAt':now.isoformat(),'sourceReceipts':sources,
            'files':{name:file_record(stage/name) for name in metadata['days'].values()},'publicationSchema':1,'reviewSourceRevalidation':report.get('reviewSourceRevalidation')})
        # Explicit interchanges already live inside each bundle; no proximity graph is added.
        walking=json.dumps({'schema':1,'stops':{},'edges':[]},separators=(',',':')).encode()
        walking_sha=hashlib.sha256(walking).hexdigest();walking_name='walking-'+walking_sha[:16]+'.json'
        (stage/walking_name).write_bytes(walking)
        metadata['walking']={'file':walking_name,'sha256':walking_sha,'bytes':len(walking)}
        validate(stage,metadata,now)
        # Move immutable data first, then replace the only pointer readers follow.
        for name in [*metadata['days'].values(),walking_name]:
            if (public/name).exists() and file_record(public/name)!=file_record(stage/name):
                raise ImportErrorDetail('An immutable publication filename already has different bytes')
        for name in [*metadata['days'].values(),walking_name]:
            shutil.copyfile(stage/name,public/(name+'.pending'))
            os.replace(public/(name+'.pending'),public/name)
        pending=public/'manifest.pending'
        pending.write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(pending,public/'manifest.json')
        keep=set(metadata['days'].values())|set(old.get('days',{}).values())
        for path in public.glob('*.json.gz'):
            if path.name not in keep:path.unlink()
    return metadata
