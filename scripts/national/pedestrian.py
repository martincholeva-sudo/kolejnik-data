"""Conservative Svinov walking audit on retained OSM topology and platform surfaces.

Default routing stays on mapped pedestrian ways/platform surfaces. Tiny bus
anchors (at most 5 m) are limited to reviewed forecourt footways. An optional
source-bound plan review adds explicit schematic fixed-stair connections.
The model is an estimate; it cannot assert lift/escalator operation or live access.
"""
import math
import heapq
from .common import digest, ImportErrorDetail


def analyse_svinov(s, tram_access=None):
    raw_nodes={str(e['id']):e for e in s['elements'] if e['type']=='node'}
    ways={e['id']:e for e in s['elements'] if e['type']=='way'}
    R=6371000;lat0=math.radians(49.821)
    xy={key:((node['lon']-18.21)*math.pi/180*R*math.cos(lat0),(node['lat']-49.821)*math.pi/180*R) for key,node in raw_nodes.items()}
    graph={};reverse={};edges=[]
    def link(a,b,kind,way,twoway=True):
     distance=math.dist(xy[a],xy[b]);seconds=distance/(0.6 if kind=='steps' else 1.15)
     if kind=='steps':seconds+=20/max(1,len(ways[way]['nodes'])-1)
     edge={'from':a,'to':b,'metres':distance,'seconds':seconds,'kind':kind,'way':way};edges.append(edge)
     graph.setdefault(a,[]).append((b,seconds,edge));reverse.setdefault(b,[]).append((a,seconds,edge))
     if twoway:
      back={**edge,'from':b,'to':a};graph.setdefault(b,[]).append((a,seconds,back));reverse.setdefault(a,[]).append((b,seconds,back))
    foot=[];lower=set();excluded=[]
    for w in ways.values():
     t=w.get('tags',{});h=t.get('highway')
     if h not in ['footway','pedestrian','steps','path','corridor']:continue
     ids=[str(i) for i in w['nodes']]
     if t.get('layer','0').startswith('-') or t.get('tunnel')=='yes':lower.update(ids)
     if any(key.endswith(':conditional') for key in t) or t.get('conveying','no')!='no' or t.get('foot') in ['no','private'] or (t.get('access') in ['no','private'] and t.get('foot') not in ['yes','designated','permissive']):excluded.append(w['id']);continue
     if any(raw_nodes[i].get('tags',{}).get('barrier') and raw_nodes[i].get('tags',{}).get('foot') not in ['yes','designated'] for i in ids):excluded.append(w['id']);continue
     foot.append(w)
     one=t.get('oneway:foot',t.get('oneway','no'))
     if one=='-1':ids.reverse()
     for a,b in zip(ids,ids[1:]):link(a,b,'steps' if h=='steps' else 'footway',w['id'],one not in ['yes','1','-1'])

    def inside(p,poly):
     x,y=p;result=False
     for a,b in zip(poly,poly[1:]+poly[:1]):
      if abs((x-a[0])*(b[1]-a[1])-(y-a[1])*(b[0]-a[0]))<1e-6 and min(a[0],b[0])-1e-6<=x<=max(a[0],b[0])+1e-6 and min(a[1],b[1])-1e-6<=y<=max(a[1],b[1])+1e-6:return True
      if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:result=not result
     return result

    ground={i for w in foot for i in map(str,w['nodes']) if i not in lower and not raw_nodes[i].get('tags',{}).get('level','0').startswith('-')}
    platforms={};joins={}
    for way in [65617857,65617849,65617843,65617837]:
     w=ways[way];poly=[xy[str(i)] for i in w['nodes'][:-1]];samples=[]
     for j,(a,b) in enumerate(zip(poly,poly[1:]+poly[:1])):
      count=max(1,math.ceil(math.dist(a,b)/10));row=[]
      for k in range(count):
       key=f'rail:{way}:{j}:{k}';xy[key]=(a[0]+(b[0]-a[0])*k/count,a[1]+(b[1]-a[1])*k/count);samples.append(key)
     for a,b in zip(samples,samples[1:]+samples[:1]):link(a,b,'platform-surface',way)
     anchors=[i for i in ground if inside(xy[i],poly)]
     for sample in samples:
      for anchor in anchors:
       a,b=xy[sample],xy[anchor]
       if all(inside((a[0]+(b[0]-a[0])*u/30,a[1]+(b[1]-a[1])*u/30),poly) for u in range(31)):link(sample,anchor,'platform-surface',way)
     platforms[w['tags']['local_ref']]=samples;joins[w['tags']['local_ref']]=anchors
    bus={};connectors=[]
    for label,way in [('N2',267299716),('N1',267299717)]:
     w=ways[way];a,b=[xy[str(i)] for i in w['nodes']];count=max(1,math.ceil(math.dist(a,b)/5));samples=[]
     for k in range(count+1):
      key=f'bus:{way}:{k}';xy[key]=(a[0]+(b[0]-a[0])*k/count,a[1]+(b[1]-a[1])*k/count);samples.append(key)
     for a,b in zip(samples,samples[1:]):link(a,b,'platform-surface',way)
     for k,key in enumerate(samples):
      best=None
      for footway in foot:
       if footway['id'] not in [493676929,493676970]:continue
       for av,bv in zip(footway['nodes'],footway['nodes'][1:]):
        u,v=str(av),str(bv);a,b=xy[u],xy[v];dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
        if den==0:continue
        f=max(0,min(1,((xy[key][0]-a[0])*dx+(xy[key][1]-a[1])*dy)/den));p=(a[0]+dx*f,a[1]+dy*f);distance=math.dist(xy[key],p)
        if best is None or distance<best[0]:best=(distance,p,u,v,footway['id'])
      assert best is not None and best[0]<=5,(label,k,best)
      distance,p,u,v,source=best;snap=f'snap:{way}:{k}';xy[snap]=p;link(key,snap,'platform-anchor',way);link(snap,u,'footway',source);link(snap,v,'footway',source)
      connectors.append({'label':label,'sourceWay':source,'distanceMetres':round(distance,2)})
     bus[label]=samples

    if tram_access is not None:
     if not isinstance(tram_access,dict) or type(tram_access.get('schema')) is not int or tram_access.get('schema')!=1 or tram_access.get('reviewed') is not True:
      raise ImportErrorDetail('Fixed stair access needs an explicit reviewed catalog')
     if tram_access.get('sourceContentSha256')!=digest(s):
      raise ImportErrorDetail('Pedestrian source changed; fixed stair re-review required')
     if not tram_access.get('planUrl','').startswith('https://') or len(tram_access.get('planSha256',''))!=64:
      raise ImportErrorDetail('Retained official stair-plan evidence is required')
     if tram_access.get('usesLift') is not False or tram_access.get('usesEscalator') is not False or tram_access.get('durationMeasured') is not False:
      raise ImportErrorDetail('Plan stairs cannot establish lift/escalator operation or measured duration')
     rows=tram_access.get('connections',[])
     if len(rows)!=2 or {r.get('platform') for r in rows}!={'H1','H2'}:
      raise ImportErrorDetail('Both distinct tram-platform stair connections are required')
     for row in rows:
      label=row['platform'];way=row.get('platformWay');concourse=row.get('concourseWay')
      if way!={'H1':233687851,'H2':233687850}[label] or concourse!=1436799696:
       raise ImportErrorDetail('Reviewed tram platform or concourse identity changed')
      upper,lower=str(row.get('upperNode')),str(row.get('lowerNode'))
      if not upper.isdigit() or not lower.isdigit() or way not in ways or concourse not in ways or int(upper) not in ways[way]['nodes'] or int(lower) not in ways[concourse]['nodes']:
       raise ImportErrorDetail('Stair anchors must belong to their exact platform and concourse')
      if ways[way].get('tags',{}).get('layer')!='2' or ways[concourse].get('tags',{}).get('level')!='1' or row.get('upperLevel')!='2' or row.get('lowerLevel')!='1':
       raise ImportErrorDetail('Stair floor transition requires explicit separate upper and lower levels')
      seconds=row.get('seconds')
      if type(seconds) is not int or not 60<=seconds<=300 or row.get('bidirectional') is not True:
       raise ImportErrorDetail('Invalid reviewed fixed-stair time or direction')
      edge={'from':upper,'to':lower,'metres':0,'seconds':seconds,
       'kind':'plan-fixed-stair-connection','way':'plan:svinov:'+label+':fixed-stairs',
       'schematic':True,'planSha256':tram_access['planSha256'],
       'referenceAnchorSeparationMetres':round(math.dist(xy[upper],xy[lower]),2)}
      # The plan establishes the connection, not a surveyed stair polyline.
      # Keep its modeled time separate from all measured map segments.
      graph.setdefault(upper,[]).append((lower,seconds,edge));reverse.setdefault(lower,[]).append((upper,seconds,edge))
      back={**edge,'from':lower,'to':upper}
      graph.setdefault(lower,[]).append((upper,seconds,back));reverse.setdefault(upper,[]).append((lower,seconds,back))
      w=ways[way];a,b=[xy[str(i)] for i in w['nodes']]
      count=max(1,math.ceil(math.dist(a,b)/5));samples=[]
      for k in range(count+1):
       key=f'tram:{way}:{k}';xy[key]=(a[0]+(b[0]-a[0])*k/count,a[1]+(b[1]-a[1])*k/count);samples.append(key)
      for a,b in zip(samples,samples[1:]):link(a,b,'platform-surface',way)
      link(samples[0],str(w['nodes'][0]),'platform-surface',way)
      link(samples[-1],str(w['nodes'][-1]),'platform-surface',way)
      bus[label]=samples

    def witness_row(edge):
     row={'way':edge['way'],'kind':edge['kind'],'metres':round(edge['metres'],2)}
     if edge.get('schematic'):
      row.update({'seconds':edge['seconds'],'geometry':'plan-topology-not-surveyed-polyline',
       'planSha256':edge['planSha256'],'referenceAnchorSeparationMetres':edge['referenceAnchorSeparationMetres']})
     return row

    def distances(start,rev=False):
     graph_here=reverse if rev else graph;dist={start:0};parent={};queue=[(0,start)]
     while queue:
      value,key=heapq.heappop(queue)
      if value!=dist[key]:continue
      for to,cost,edge in graph_here.get(key,[]):
       if value+cost<dist.get(to,float('inf')):dist[to]=value+cost;parent[to]=(key,edge);heapq.heappush(queue,(value+cost,to))
     return dist,parent
    results=[]
    for label,samples in bus.items():
     kind='tram' if label in {'H1','H2'} else 'bus'
     for direction in ['rail-to-'+kind,kind+'-to-rail']:
      worst=(-1,None);missing=[]
      for sample in samples:
       dist,parent=distances(sample,rev=direction.startswith('rail-to-'))
       for platform,sources in platforms.items():
        unreachable=[key for key in sources if key not in dist]
        if unreachable:missing.append(platform);continue
        key=max(sources,key=dist.get)
        if dist[key]>worst[0]:worst=(dist[key],(sample,key,platform,parent))
      if missing:results.append({'platform':label,'direction':direction,'unreachableRailPlatforms':sorted(set(missing))});continue
      seconds,(sample,key,platform,parent)=worst;witness=[];current=key
      while current!=sample:previous,edge=parent[current];witness.append(edge);current=previous
      # 60 seconds for alighting/boarding uncertainty, plus <= 7.5 m sampling error.
      bound=math.ceil((seconds+60+7.5/1.15)/30)*30
      results.append({'platform':label,'direction':direction,'conservativeWalkingSeconds':bound,'modelSecondsBeforeReserve':round(seconds,2),'worstRailPlatform':platform,'pathMetres':round(sum(e['metres'] for e in witness),2),'wayWitness':[witness_row(e) for e in witness]})
    pairs=[('N1','N2'),('N2','N1')]
    if tram_access is not None:
     pairs.extend((a,b) for a in bus for b in bus if a!=b and (a in {'H1','H2'} or b in {'H1','H2'}))
    for origin,target in pairs:
     worst=(-1,None)
     for sample in bus[origin]:
      dist,parent=distances(sample)
      if any(key not in dist for key in bus[target]):
       raise ImportErrorDetail('Reviewed surface platforms are disconnected; no lift/escalator fallback')
      key=max(bus[target],key=dist.get)
      if dist[key]>worst[0]:worst=(dist[key],(sample,key,parent))
     seconds,(sample,key,parent)=worst;witness=[];current=key
     while current!=sample:previous,edge=parent[current];witness.append(edge);current=previous
     results.append({'platform':target,'fromPlatform':origin,'direction':('tram' if origin in {'H1','H2'} else 'bus')+'-to-'+('tram' if target in {'H1','H2'} else 'bus'),'conservativeWalkingSeconds':math.ceil((seconds+60+7.5/1.15)/30)*30,'modelSecondsBeforeReserve':round(seconds,2),'pathMetres':round(sum(e['metres'] for e in witness),2),'wayWitness':[witness_row(e) for e in witness]})
    report={'candidateOnly':True,'walkingTimesAreEstimates':True,'source':'OpenStreetMap','sourceSnapshotFile':'testing/svinov-osm-source.json','platformSurfaceModel':'Only within mapped rail-platform polygons; no cross-track links. Bus anchors constrained to reviewed adjacent footways within 5 m.','excludedEscalatorsOrRestrictedWays':excluded,'railPlatformGroundAccess':joins,'lastMetresAnchors':connectors,'results':results}
    if tram_access is not None:
     report['fixedStairAccessReview']=dict(tram_access)
     report['geometryIncludesPlanTopologicalConnections']=True
     report['liftsUsed']=False;report['escalatorsUsed']=False
     for row in results:
      row['pathMetresExcludeSchematicStairConnections']=any(e['kind']=='plan-fixed-stair-connection' for e in row.get('wayWitness',[]))
    return report
