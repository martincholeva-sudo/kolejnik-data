"""Compile the reviewed Svinov source geometry into isolated candidate links.

Durations are model estimates, not measured or guaranteed interchange times.
NAD calls have separate nodes and cannot inherit a railway-platform corridor.
"""
import argparse
import datetime as dt
import gzip
import json
from pathlib import Path
from national.common import digest, ImportErrorDetail


def read(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:return json.load(stream)


def build(rail,ostrava,audit):
    stops={s['id']:s for s in [*rail['stops'],*ostrava['stops']]}
    station=stops.get('czrail:CZ:34434')
    if station is None or station.get('boardingMode')=='replacement' or station['name']!='Ostrava-Svinov':
        raise ImportErrorDetail('Svinov railway source identity requires review')
    calls=[s for s in ostrava['stops'] if s.get('physicalPlatformVerified') is True and s.get('sourceCall',{}).get('scheduledStopPointRef')=='CZ:cisjr_jdf:ScheduledStopPoint:915037__13']
    if len(calls)!=6 or {s['platform'] for s in calls}!={'N1','N2'}:
        raise ImportErrorDetail('Svinov reviewed platform-call collection changed')
    if set(audit.get('railPlatformGroundAccess',{}))!={'1','2','3','4'} or any(not values for values in audit['railPlatformGroundAccess'].values()):
        raise ImportErrorDetail('Every railway platform requires actual mapped pedestrian access')
    values={}
    for row in audit['results']:
        if row.get('unreachableRailPlatforms') or not row.get('wayWitness'):
            raise ImportErrorDetail('Incomplete physical interchange geometry')
        seconds=row.get('conservativeWalkingSeconds')
        if type(seconds) is not int or not 30<=seconds<=1800:
            raise ImportErrorDetail('Invalid modeled interchange time')
        values[row['direction'],row.get('fromPlatform'),row['platform']]=seconds
    now=dt.datetime.now(dt.timezone.utc).isoformat();start=max(rail['start'],ostrava['start']);end=min(rail['end'],ostrava['end'])
    links=[]
    def add(source,target,seconds):
        links.append({'from':source['id'],'to':target['id'],'seconds':seconds,'estimated':True,'verified':True,
            'evidence':'https://api.openstreetmap.org/api/0.6/map.json?bbox=18.208,49.8195,18.213,49.822',
            'operatorPlan':'https://www.dpo.cz/soubory/jr/stan-mhd-svinov-nadrazi-2026-10-20.jpg',
            'verifiedAt':now,'reviewedFrom':start,'reviewedUntil':end,'fromStopSha256':digest(source),'toStopSha256':digest(target),
            'durationMeasured':False,'railPlatformAssumption':'worst across all four platform surfaces; uncertain escalator directions excluded'})
    for stop in calls:
        add(station,stop,values['rail-to-bus',None,stop['platform']]);add(stop,station,values['bus-to-rail',None,stop['platform']])
    for a in calls:
        for b in calls:
            if a['platform']!=b['platform']:add(a,b,values['bus-to-bus',a['platform'],b['platform']])
    return {'schema':1,'candidateOnly':True,'geometryReview':'DPO platform directions, exact CIS call/direction signatures, OSM route membership and mapped pedestrian connectivity','sourceSnapshotSha256':audit['sourceSnapshotSha256'],'model':audit['model'],'attribution':'© OpenStreetMap contributors, ODbL 1.0; CIS JŘ; Dopravní podnik Ostrava','links':links}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rail',type=Path,required=True);parser.add_argument('--ostrava',type=Path,required=True)
    parser.add_argument('--audit',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();result=build(read(args.rail),read(args.ostrava),json.loads(args.audit.read_text(encoding='utf-8')))
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'reviewedDirectedLinks':len(result['links']),'estimatedDurations':True,'candidateOnly':True}))
