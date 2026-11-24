"""Compile source-bound Svinov tram access; merge unchanged existing bus links."""
import argparse
import datetime as dt
import json
from pathlib import Path
from national.common import ImportErrorDetail, digest, iso_day
from build_svinov_interchanges import read

ROOT = Path(__file__).resolve().parents[1]


def build(rail, ostrava, audit, mappings, existing):
    start, end = max(rail['start'], ostrava['start']), min(rail['end'], ostrava['end'])
    review = audit.get('fixedStairAccessReview', {})
    try:
        if iso_day(review['reviewedFrom']) > iso_day(start) or iso_day(review['reviewedUntil']) < iso_day(end):
            raise ValueError('review does not cover timetable horizon')
        if review['reviewed'] is not True or review['durationMeasured'] is not False or review['usesLift'] is not False or review['usesEscalator'] is not False:
            raise ValueError('incompatible access evidence')
        if audit['sourceSnapshotSha256'] != review['sourceFileSha256'] or audit['sourceContentSha256'] != review['sourceContentSha256']:
            raise ValueError('source revision mismatch')
        if len(review['planSha256']) != 64 or not review['planUrl'].startswith('https://'):
            raise ValueError('missing official plan')
    except (KeyError, TypeError, ValueError) as error:
        raise ImportErrorDetail('Invalid or expired fixed-stair source review') from error
    if set(audit.get('railPlatformGroundAccess', {})) != {'1','2','3','4'} or any(not x for x in audit['railPlatformGroundAccess'].values()):
        raise ImportErrorDetail('All four rail platforms require actual mapped access')
    station = next((s for s in rail['stops'] if s['id']=='czrail:CZ:34434'), None)
    if station is None or station['name']!='Ostrava-Svinov' or station.get('boardingMode')=='replacement':
        raise ImportErrorDetail('Svinov rail identity changed or refers to NAD')
    mapped = {r['stopId']:r for r in mappings['calls']}
    tram = [s for s in ostrava['stops'] if s['id'] in mapped]
    if len(mapped)!=46 or len(tram)!=46 or {p:sum(s.get('platform')==p for s in tram) for p in ['H1','H2']}!={'H1':20,'H2':26}:
        raise ImportErrorDetail('The reviewed 46 tram source calls changed')
    for stop in tram:
        row = mapped[stop['id']]
        original = {k:v for k,v in stop.items() if k not in ['physicalPlatformId','platformEvidence']}
        original.update({'platform':'','physicalPlatformVerified':False})
        if digest(original) != row['stopSha256']:
            raise ImportErrorDetail('Tram source call changed; re-verification required')
        if stop.get('physicalPlatformVerified') is not True or stop.get('platform')!=row['platform'] or stop.get('physicalPlatformId')!=row['physicalPlatformId'] or not stop.get('platformEvidence',{}).get('url','').startswith('https://'):
            raise ImportErrorDetail('Tram physical platform mapping changed')
    bus = [s for s in ostrava['stops'] if s.get('physicalPlatformVerified') is True and s.get('sourceCall',{}).get('scheduledStopPointRef')=='CZ:cisjr_jdf:ScheduledStopPoint:915037__13']
    if len(bus)!=6 or {s.get('platform') for s in bus}!={'N1','N2'}:
        raise ImportErrorDetail('Reviewed bus37 source calls changed')
    values = {}
    for row in audit['results']:
        key = (row['direction'],row.get('fromPlatform'),row['platform'])
        seconds = row.get('conservativeWalkingSeconds')
        if key in values or row.get('unreachableRailPlatforms') or not row.get('wayWitness') or type(seconds) is not int or not 30<=seconds<=1800:
            raise ImportErrorDetail('Incomplete or invalid physical walking audit')
        values[key] = seconds
    links = [dict(row) for row in existing['links']]
    if existing.get('schema')!=1 or len(links)!=28:
        raise ImportErrorDetail('Expected the unchanged 28 prior bus links')
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    model_hash = digest(audit)
    plan_costs = {r['platform']:r['seconds'] for r in review['connections']}
    def add(a,b,key):
        if key not in values:
            raise ImportErrorDetail('Missing audited physical direction')
        links.append({'from':a['id'],'to':b['id'],'seconds':values[key],
            'estimated':True,'verified':True,'evidence':review['planUrl'],
            'verifiedAt':now,'reviewedFrom':review['reviewedFrom'],'reviewedUntil':review['reviewedUntil'],
            'fromStopSha256':digest(a),'toStopSha256':digest(b),
            'durationMeasured':False,'walkingModelSha256':model_hash,
            'sourceSnapshotSha256':audit['sourceSnapshotSha256'],'operatorPlanSha256':review['planSha256'],
            'geometry':'mapped footways and platform surfaces with explicit plan-confirmed fixed-stair topology',
            'planStairConnectionSeconds':plan_costs,'planStairGeometrySurveyed':False,
            'railPlatformAssumption':'worst across all four full platform surfaces',
            'usesLift':False,'usesEscalator':False})
    for s in tram:
        add(station,s,('rail-to-tram',None,s['platform']))
        add(s,station,('tram-to-rail',None,s['platform']))
    for a in tram:
        for b in tram:
            if a['platform']!=b['platform']:
                add(a,b,('tram-to-tram',a['platform'],b['platform']))
    for a in bus:
        for b in tram:
            add(a,b,('bus-to-tram',a['platform'],b['platform']))
            add(b,a,('tram-to-bus',b['platform'],a['platform']))
    if len(links)!=1712 or len({(r['from'],r['to']) for r in links})!=1712:
        raise ImportErrorDetail('Unexpected or duplicate interchange collection')
    return {'schema':1,'candidateOnly':True,'sourceSnapshotSha256':audit['sourceSnapshotSha256'],
            'walkingModelSha256':model_hash,'model':audit['model'],
            'attribution':'© OpenStreetMap contributors, ODbL 1.0; KODIS; CIS JŘ; Dopravní podnik Ostrava',
            'reviewedFrom':review['reviewedFrom'],'reviewedUntil':review['reviewedUntil'],
            'previousDirectedLinks':28,'addedDirectedLinks':1684,'links':links}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['rail','ostrava','audit','existing','out']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--mappings',type=Path,default=ROOT/'config/svinov-tram-platform-mappings.json')
    args=parser.parse_args()
    load=lambda path:json.loads(path.read_text(encoding='utf-8'))
    result=build(read(args.rail),read(args.ostrava),load(args.audit),load(args.mappings),load(args.existing))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'directedLinks':len(result['links']),'added':result['addedDirectedLinks'],'candidateOnly':True}))
