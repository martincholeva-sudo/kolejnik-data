"""Refresh the Prague OSM interchange graph at most once a week."""
import datetime,json,subprocess,sys,urllib.request,urllib.parse,gzip,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
output=root/'spojeni/data-v2/walking-links.json'
manifest=json.loads((output.parent/'manifest.json').read_text(encoding='utf-8'))
with gzip.open(output.parent/next(iter(manifest['days'].values())),'rt',encoding='utf-8') as stream:stops=json.load(stream)['stops']
stop_hash=hashlib.sha256(json.dumps(stops,sort_keys=True,separators=(',',':')).encode()).hexdigest()
folder=root/'runtime';folder.mkdir(exist_ok=True)
path=folder/'prague-walking-osm.json'
if output.exists():
    old=json.loads(output.read_text(encoding='utf-8'))
    generated=datetime.datetime.fromisoformat(old['generatedAt'])
    young=old.get('revision')==2 and (datetime.datetime.now(datetime.timezone.utc)-generated).total_seconds()<7*86400
    if young and old.get('stopHash')==stop_hash:
        print('Walking graph is younger than a week; kept.');sys.exit(0)
    if young and path.exists():
        subprocess.run([sys.executable,str(root/'scripts/build_walking_links.py'),'--osm',str(path)],check=True);sys.exit(0)
query='[out:json][timeout:180];way["highway"]["area"!="yes"](49.85,14.1,50.3,14.8);out body;>;out body qt;'
request=urllib.request.Request('https://overpass-api.de/api/interpreter',data=urllib.parse.urlencode({'data':query}).encode(),headers={'User-Agent':'Kolejnik-footpaths/0.3'})
with urllib.request.urlopen(request,timeout=210) as response:raw=response.read()
parsed=json.loads(raw)
if parsed.get('remark') or not parsed.get('elements'):raise ValueError('Incomplete OSM extract; previous graph retained')
path.write_bytes(raw)
subprocess.run([sys.executable,str(root/'scripts/build_walking_links.py'),'--osm',str(path)],check=True)
