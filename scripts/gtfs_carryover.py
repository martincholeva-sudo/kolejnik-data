"""Retain the actual preceding service-day trips after a provider feed rollover.

Do not infer yesterday from today's weekday pattern: PID can change schedules.
The previous published snapshot supplies only rides continuing after midnight.
"""
import datetime as dt,gzip,json
from pathlib import Path

def carry_previous_day(snapshot,public):
    public=Path(public);manifest=public/'manifest.json'
    if not manifest.exists():return 0
    previous=(dt.date.fromisoformat(snapshot['start'])-dt.timedelta(days=1)).isoformat()
    m=json.loads(manifest.read_text(encoding='utf-8'));name=m.get('days',{}).get(previous) or m.get('days',{}).get(snapshot['start'])
    if not name or not (public/name).exists():return 0
    with gzip.open(public/name,'rt',encoding='utf-8') as stream:old=json.load(stream)
    services=set(old['active'].get(previous,[]))
    rides=[t for t in old['trips'] if t[1] in services and t[5][-1][2]>=86400]
    if not rides:return 0
    stops={s['id']:i for i,s in enumerate(snapshot['stops'])};mapped={};added=set()
    for i,s in enumerate(old['stops']):
        if s['id'] not in stops:
            stops[s['id']]=len(snapshot['stops']);added.add(stops[s['id']]);snapshot['stops'].append(s)
        mapped[i]=stops[s['id']]
    routes={tuple(r):i for i,r in enumerate(snapshot['routes'])};route_map={}
    for i,r in enumerate(old['routes']):
        if tuple(r) not in routes:routes[tuple(r)]=len(snapshot['routes']);snapshot['routes'].append(r)
        route_map[i]=routes[tuple(r)]
    prefix='carry:'+previous+':'
    service=lambda name:name if name.startswith(prefix) else prefix+name
    snapshot['active'][previous]=sorted({service(t[1]) for t in rides})
    for t in rides:
        snapshot['trips'].append([t[0],service(t[1]),route_map[t[2]],t[3],t[4],[[mapped[r[0]],*r[1:]] for r in t[5]]])
    # Existing platforms use today's official corridors. Retained old-only nodes
    # need their explicit official paths, never a guessed coordinate shortcut.
    for a,b,seconds,estimated in old['paths']:
        if mapped[a] in added or mapped[b] in added:snapshot['paths'].append([mapped[a],mapped[b],seconds,estimated])
    snapshot['carryover']={'date':previous,'trips':len(rides),'sourceSha256':old['sha256']}
    return len(rides)
