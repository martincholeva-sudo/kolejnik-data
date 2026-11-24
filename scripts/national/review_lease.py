"""Revalidate unchanged published evidence and exact source bindings for a new horizon.

The original visual review time is retained. A changed plan, map, station register,
or source stop/pattern requires a new review instead of an automatic extension.
"""
import copy
import datetime as dt
import hashlib
import html
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
from .common import digest,ImportErrorDetail,iso_day
from .locations import load_catalog

INDEX='https://www.kodis.cz/prestupni-uzly'


def fetch(url,limit=5_000_000):
    request=urllib.request.Request(url,headers={'User-Agent':'Kolejnik evidence revalidation (public transport)'})
    with urllib.request.urlopen(request,timeout=30) as response:
        data=response.read(limit+1)
    if len(data)>limit:raise ImportErrorDetail('Oversized evidence document')
    return data


def check_sources(root,fetch_bytes=fetch):
    root=Path(root)
    access=json.loads((root/'config/svinov-fixed-stair-connections.json').read_text(encoding='utf-8'))
    index=html.unescape(fetch_bytes(INDEX).decode('utf-8')).replace('\\/','/').replace('\\u002F','/')
    plans=set(re.findall(r'https://kodis-files\.s3\.eu-central-1\.amazonaws\.com/Ostrava_Svinov_[A-Za-z0-9]+\.pdf',index))
    if plans!={access['planUrl']} or hashlib.sha256(fetch_bytes(access['planUrl'])).hexdigest()!=access['planSha256']:
        raise ImportErrorDetail('Current Svinov operator plan changed or is no longer the unique current link; review required')
    register=json.loads((root/'reports/station-register-20261027/manifest.json').read_text(encoding='utf-8'))
    article=html.unescape(fetch_bytes(register['indexUrl']).decode('utf-8',errors='replace'))
    oid=urllib.parse.parse_qs(urllib.parse.urlsplit(register['sourceUrl']).query)['oid'][0]
    if 'oid='+oid not in article or hashlib.sha256(fetch_bytes(register['sourceUrl'])).hexdigest()!=register['sourceSha256']:
        raise ImportErrorDetail('Reviewed station register changed or disappeared; review required')
    return {'revalidatedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'operatorIndex':INDEX,'operatorPlan':access['planUrl'],
            'operatorPlanSha256':access['planSha256'],'stationRegisterIndex':register['indexUrl'],
            'stationRegisterSha256':register['sourceSha256'],'visualReviewAtUnchanged':True,
            'scope':'Unchanged documents and exact source bindings; no live access or measured walking duration inferred'}


def renew_location_bindings(catalog,rail,city,original_reviews,first,last,evidence):
    revised=copy.deepcopy(catalog)
    stops={s['id']:s for s in [*rail['stops'],*city['stops']]}
    for row in revised['locations']:
        stop=stops.get(row['stopId'])
        if stop is None:raise ImportErrorDetail('Reviewed coordinate source call disappeared')
        original=copy.deepcopy(stop)
        review_id=original.get('platformEvidence',{}).get('reviewId')
        if review_id:
            if review_id not in original_reviews:raise ImportErrorDetail('Unknown platform review during coordinate renewal')
            for field in ['reviewedFrom','reviewedUntil']:
                if field in original_reviews[review_id]:original['platformEvidence'][field]=original_reviews[review_id][field]
                else:original['platformEvidence'].pop(field,None)
        if digest(original)!=row['stopSha256']:
            raise ImportErrorDetail('Source call changed beyond evidence dates; coordinate re-review required')
        row['priorReviewedStopSha256']=row['stopSha256'];row['stopSha256']=digest(stop)
    revised['review'].update({'reviewedFrom':first,'reviewedUntil':last,'sourceRevalidation':evidence})
    return revised


def lease(root,runtime,first,last,fetch_bytes=fetch):
    root,runtime=Path(root),Path(runtime);runtime.mkdir(parents=True,exist_ok=True)
    if (iso_day(last)-iso_day(first)).days not in range(1,32):raise ImportErrorDetail('Invalid evidence lease horizon')
    evidence=check_sources(root,fetch_bytes)
    original_reviews={};combined={'schema':1,'reviews':[],'calls':[]}
    for name in ['ostrava-platform-mappings.json','svinov-tram-platform-mappings.json']:
        catalog=json.loads((root/'config'/name).read_text(encoding='utf-8'))
        for review in catalog['reviews']:
            original_reviews[review['id']]=copy.deepcopy(review)
            review.update({'reviewedFrom':first,'reviewedUntil':last,'sourceRevalidation':evidence})
        combined['reviews'].extend(catalog['reviews']);combined['calls'].extend(catalog['calls'])
    platform_path=runtime/'platforms.json';platform_path.write_text(json.dumps(combined,ensure_ascii=False),encoding='utf-8')
    access=json.loads((root/'config/svinov-fixed-stair-connections.json').read_text(encoding='utf-8'))
    access.update({'reviewedFrom':first,'reviewedUntil':last,'sourceRevalidation':evidence})
    aliases=json.loads((root/'config/rail-station-reference-aliases.json').read_text(encoding='utf-8'))
    aliases.update({'reviewedFrom':first,'reviewedUntil':last,'sourceRevalidation':evidence})
    base_locations,geometry=load_catalog(root/'config/priority-location-mappings.json')
    def locations(rail,city):
        return renew_location_bindings(base_locations,rail,city,original_reviews,first,last,evidence),geometry
    return platform_path,access,aliases,locations,evidence
