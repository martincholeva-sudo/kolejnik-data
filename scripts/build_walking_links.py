"""Build street approaches to PID interchanges from an OSM pedestrian graph.
Requires scipy. Input is an Overpass ways + nodes JSON; no routing by air distance.
Output is an ODbL derived dataset. Distances and walking times remain estimates.
"""
import argparse, collections, datetime, gzip, heapq, json, math, hashlib
from pathlib import Path
from scipy.spatial import cKDTree

parser=argparse.ArgumentParser()
parser.add_argument('--osm',required=True)
parser.add_argument('--bounds',default='49.85,14.1,50.3,14.8')
args=parser.parse_args()
bounds=[float(x) for x in args.bounds.split(',')]
root=Path(__file__).resolve().parents[1]
folder=root/'spojeni/data-v2'
manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
data=json.loads(gzip.decompress((folder/next(iter(manifest['days'].values()))).read_bytes()))
osm=json.loads(Path(args.osm).read_bytes())
xy=lambda lat,lon:(lon*71500,lat*111200)
points={n['id']:xy(n['lat'],n['lon']) for n in osm['elements'] if n['type']=='node'}
blocked=set()
for n in osm['elements']:
 if n['type']!='node':continue
 t=n.get('tags',{})
 if t.get('foot') in {'no','private'} or (t.get('access') in {'no','private'} and t.get('foot') not in {'yes','designated','permissive'}):blocked.add(n['id'])
 if t.get('barrier') in {'fence','wall','retaining_wall','hedge'} and t.get('foot') not in {'yes','designated','permissive'}:blocked.add(n['id'])
graph=collections.defaultdict(list)
allowed={'footway','path','pedestrian','steps','living_street','residential','service','unclassified','tertiary','secondary','primary','track','road','cycleway'}
for way in osm['elements']:
 if way['type']!='way':continue
 t=way.get('tags',{});h=t.get('highway')
 if h not in allowed or t.get('foot') in {'no','private'}:continue
 if t.get('access') in {'no','private'} and t.get('foot') not in {'yes','designated','permissive'}:continue
 if h=='cycleway' and t.get('foot') not in {'yes','designated','permissive'}:continue
 if t.get('construction') or t.get('area')=='yes':continue
 for a,b in zip(way['nodes'],way['nodes'][1:]):
  if a not in points or b not in points or a in blocked or b in blocked:continue
  length=math.dist(points[a],points[b])
  if t.get('oneway:foot')!='-1':graph[a].append((b,length))
  if t.get('oneway:foot')!='yes':graph[b].append((a,length))
osm_timestamp=osm.get('osm3s',{}).get('timestamp_osm_base');del osm
keys=list(graph);tree=cKDTree([points[k] for k in keys])
stops=[s for s in data['stops'] if (s['type']==2 or (s['type']==0 and not s['parent'])) and bounds[0]<=s['lat']<=bounds[2] and bounds[1]<=s['lon']<=bounds[3]]
anchors={};unmatched=[]
for s in stops:
 distance,index=tree.query(xy(s['lat'],s['lon']))
 if distance<=35:anchors[s['id']]=(keys[index],float(distance))
 else:unmatched.append(s['id'])
stop_tree=cKDTree([xy(s['lat'],s['lon']) for s in stops]);edges=[]
for count,s in enumerate(stops):
 if s['id'] not in anchors:continue
 targets=[]
 for j in stop_tree.query_ball_point(xy(s['lat'],s['lon']),450):
  t=stops[j]
  if t['id']==s['id'] or t['id'] not in anchors:continue
  # A real street path may connect differently named stops as well.
  targets.append(t)
 if not targets:continue
 start,snap=anchors[s['id']];dist={start:0};queue=[(0,start)]
 wanted={anchors[t['id']][0] for t in targets}
 while queue and wanted:
  cost,node=heapq.heappop(queue)
  if cost!=dist[node]:continue
  wanted.discard(node)
  for other,length in graph[node]:
   new=cost+length
   if new<=800 and new<dist.get(other,math.inf):dist[other]=new;heapq.heappush(queue,(new,other))
 for t in targets:
  node,end_snap=anchors[t['id']]
  if node not in dist:continue
  meters=dist[node]+snap+end_snap
  # One metre per second plus approach allowance; transfer buffer is separate.
  seconds=max(60,math.ceil(meters+30))
  edges.append([s['id'],t['id'],seconds,round(meters)])
out={'schema':1,'revision':2,'source':'OpenStreetMap contributors','license':'ODbL-1.0','osmTimestamp':osm_timestamp,'sourceUrl':'https://www.openstreetmap.org/copyright','generatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'bounds':bounds,'maxSnapMeters':35,'maxRouteMeters':800,'stops':{s['id']:[s['lat'],s['lon']] for s in stops if s['id'] in anchors},'edges':edges,'unmatchedStops':unmatched}
out['stopHash']=hashlib.sha256(json.dumps(data['stops'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
if len(edges)<5000:raise ValueError('Incomplete pedestrian graph; previous graph retained')
tmp=folder/'walking-links.tmp';tmp.write_text(json.dumps(out,separators=(',',':')),encoding='utf-8');tmp.replace(folder/'walking-links.json')
print(json.dumps({'edges':len(edges),'anchors':len(anchors),'unmatched':len(unmatched)}))
