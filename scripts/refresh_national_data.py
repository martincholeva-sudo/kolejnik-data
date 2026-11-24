"""Refresh the isolated national feed from HTTP-verified official collections."""
import argparse
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from fetch_priority_sources import fetch_all
from prepare_priority_data import prepare
from audit_svinov_tram_pedestrian import audit
from build_svinov_interchanges import build as bus_links
from build_svinov_tram_interchanges import build as tram_links
from national.publication import publish
from national.review_lease import lease

ROOT=Path(__file__).resolve().parents[1]


def refresh(public, runtime, inputs=None, start=None, days=7, year=None):
    start=start or dt.datetime.now(ZoneInfo('Europe/Prague')).date().isoformat()
    runtime=Path(runtime).resolve();runtime.mkdir(parents=True,exist_ok=True)
    inputs=Path(inputs) if inputs else fetch_all(runtime/'sources',year or int(start[:4]),start,workers=8)
    load=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
    first=(dt.date.fromisoformat(start)-dt.timedelta(days=1)).isoformat()
    last=(dt.date.fromisoformat(start)+dt.timedelta(days=days)).isoformat()
    platforms,access,aliases,locations,evidence=lease(ROOT,runtime/'reviews',first,last)
    physical=audit(ROOT/'tests/fixtures/streets/svinov-full-osm-20261027.json.gz',access)
    mappings=load(ROOT/'config/svinov-tram-platform-mappings.json')
    def compile_links(rail,city):
        previous=bus_links(rail,city,physical)
        return tram_links(rail,city,physical,mappings,previous)
    network,report=prepare(inputs,start,days,runtime/'prepare',platform_mappings=platforms,link_builder=compile_links,return_network=True,location_builder=locations,station_aliases=aliases)
    report['reviewSourceRevalidation']=evidence
    metadata=publish(network,report,load(inputs),public,runtime)
    (runtime/'refresh-report.json').write_text(json.dumps({'success':True,'revision':metadata['sha256'],'days':len(metadata['days']),'sourceReceipts':metadata['sourceReceipts'],'productionPidChanged':False},indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'revision':metadata['sha256'],'days':len(metadata['days']),'channel':metadata['channel']}))
    return metadata


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--public',type=Path,default=ROOT/'spojeni/data-national')
    p.add_argument('--runtime',type=Path,default=ROOT/'runtime/national-refresh')
    p.add_argument('--inputs',type=Path)
    p.add_argument('--start');p.add_argument('--days',type=int,default=7);p.add_argument('--year',type=int)
    a=p.parse_args()
    try:refresh(a.public,a.runtime,a.inputs,a.start,a.days,a.year)
    except Exception as error:
        a.runtime.mkdir(parents=True,exist_ok=True)
        (a.runtime/'refresh-report.json').write_text(json.dumps({'success':False,'error':str(error),'previousRevisionRetained':True},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        raise
