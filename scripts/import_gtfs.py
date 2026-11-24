"""Keep original stop/trip identities and seconds. Publish one validated snapshot atomically."""
import argparse, csv, datetime as dt, gzip, hashlib, io, json, math, os
from pathlib import Path
import urllib.request, zipfile

def seconds(value):
    h, m, s = map(int, value.split(':'))
    return h * 3600 + m * 60 + s

def build(source, destination, public_out=None, carry_from=None):
    if str(source).startswith('https://'):
        with urllib.request.urlopen(source, timeout=120) as response:
            raw = response.read(200_000_001)
        if len(raw) > 200_000_000: raise ValueError('GTFS exceeds size limit')
    else: raw = Path(source).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        def rows(name, optional=False):
            if optional and name not in archive.namelist(): return
            with archive.open(name) as file:
                yield from csv.DictReader(io.TextIOWrapper(file, encoding='utf-8-sig'))
        info = next(rows('feed_info.txt'))
        start = dt.datetime.strptime(info['feed_start_date'], '%Y%m%d').date().isoformat()
        end = dt.datetime.strptime(info['feed_end_date'], '%Y%m%d').date().isoformat()
        if end < start: raise ValueError('Invalid feed validity')
        stops, indexes = [], {}
        for row in rows('stops.txt'):
            indexes[row['stop_id']] = len(stops)
            stops.append({'id': row['stop_id'], 'name': row['stop_name'],
                'lat': float(row['stop_lat'] or 0), 'lon': float(row['stop_lon'] or 0),
                'type': int(row.get('location_type') or 0), 'parent': row.get('parent_station', ''),
                'zone': row.get('zone_id', ''), 'platform': row.get('platform_code', ''),
                'group': row.get('asw_node_id') or row.get('parent_station') or row['stop_id']})
        routes = [[r['route_id'], r['route_short_name'] or r['route_long_name'], int(r['route_type'])] for r in rows('routes.txt')]
        route_index = {r[0]: i for i, r in enumerate(routes)}
        trips, by_id = [], {}
        for r in rows('trips.txt'):
            by_id[r['trip_id']] = len(trips)
            trips.append([r['trip_id'], r['service_id'], route_index[r['route_id']], r['trip_headsign'], r.get('trip_short_name',''), []])
        for r in rows('stop_times.txt'):
            if not r['arrival_time'] or not r['departure_time']:
                raise ValueError('Missing stop times: interpolation must be implemented explicitly')
            trips[by_id[r['trip_id']]][5].append([indexes[r['stop_id']], seconds(r['arrival_time']), seconds(r['departure_time']),
                int(r.get('pickup_type') or 0), int(r.get('drop_off_type') or 0), int(r['stop_sequence']), int(r.get('trip_operation_type') or 1)])
        for trip in trips:
            trip[5].sort(key=lambda r:r[5])
            last = -1
            for row in trip[5]:
                if row[1] < last or row[2] < row[1]: raise ValueError('Non-monotonic trip '+trip[0])
                last = row[2]
        # Only explicit pedestrian paths. No straight line shortcuts across rails/roads.
        paths = []
        for r in rows('pathways.txt', True):
            a, b = indexes[r['from_stop_id']], indexes[r['to_stop_id']]
            duration = r.get('traversal_time')
            estimated = not duration
            if not duration:
                x, y = stops[a], stops[b]
                meters = math.hypot((x['lat']-y['lat'])*111320, (x['lon']-y['lon'])*111320*math.cos(math.radians(x['lat'])))
                # Connectivity is official; missing traversal time remains explicitly estimated.
                duration = max(30, math.ceil(meters / 1.0))
            paths.append([a,b,int(duration),estimated])
            if r.get('is_bidirectional') == '1': paths.append([b,a,int(duration),estimated])
        transfers=[]
        for r in rows('transfers.txt', True):
            transfers.append([indexes[r['from_stop_id']],indexes[r['to_stop_id']],int(r['transfer_type']),
                int(r.get('min_transfer_time') or 0),r.get('from_trip_id',''),r.get('to_trip_id','')])
        active={}
        calendar=list(rows('calendar.txt', True)); exceptions=list(rows('calendar_dates.txt', True))
        day=dt.date.fromisoformat(start)
        while day.isoformat()<=end:
            key=day.strftime('%Y%m%d'); weekday=['monday','tuesday','wednesday','thursday','friday','saturday','sunday'][day.weekday()]
            services={r['service_id'] for r in calendar if r['start_date']<=key<=r['end_date'] and r[weekday]=='1'}
            for r in exceptions:
                if r['date']==key:
                    if r['exception_type']=='1': services.add(r['service_id'])
                    else: services.discard(r['service_id'])
            active[day.isoformat()]=sorted(services)
            day+=dt.timedelta(days=1)
        snapshot={'schema':2,'source':'https://data.pid.cz/PID_GTFS.zip','attribution':'ROPID / PID, CC BY; převedeno Kolejníkem',
            'importedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'sha256':hashlib.sha256(raw).hexdigest(),
            'start':start,'end':end,'stops':stops,'routes':routes,'trips':trips,'paths':paths,'transfers':transfers,'active':active}
        if not trips or not stops or not active: raise ValueError('Empty feed')
    if public_out:
        from gtfs_carryover import carry_previous_day
        carry_previous_day(snapshot,carry_from or public_out)
    destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_suffix('.tmp')
    with gzip.open(temp,'wt',encoding='utf-8',compresslevel=5) as f:
        json.dump(snapshot,f,ensure_ascii=False,separators=(',',':'))
    os.replace(temp,destination)
    if public_out:
        public=Path(public_out);public.mkdir(parents=True,exist_ok=True)
        manifest={k:snapshot[k] for k in ('schema','source','attribution','importedAt','sha256','start','end')}
        manifest['days']={}
        for day, services in active.items():
            if day<start:continue
            date=dt.date.fromisoformat(day)
            previous=(date-dt.timedelta(days=1)).isoformat();following=(date+dt.timedelta(days=1)).isoformat()
            current=set(services);before=set(active.get(previous,[]));after=set(active.get(following,[]))
            selected=[t for t in trips if t[1] in current or (t[1] in before and t[5][-1][2]>=86400) or (t[1] in after and t[5][0][1]<=43200)]
            bundle={**snapshot,'trips':selected,'active':{d:active[d] for d in (previous,day,following) if d in active}}
            name=f"{snapshot['sha256'][:12]}-{day}.json.gz"
            with gzip.open(public/name,'wt',encoding='utf-8',compresslevel=5) as f:json.dump(bundle,f,ensure_ascii=False,separators=(',',':'))
            manifest['days'][day]=name
        # Publish the manifest last. Existing readers retain an immutable revision.
        pending=public/'manifest.tmp'
        pending.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
        os.replace(pending,public/'manifest.json')
    print(json.dumps({'stops':len(stops),'trips':len(trips),'start':start,'end':end,'sha256':snapshot['sha256']}))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',default='https://data.pid.cz/PID_GTFS.zip')
    parser.add_argument('--out',default='runtime/network.json.gz')
    parser.add_argument('--public-out')
    args=parser.parse_args(); build(args.source,args.out,args.public_out)
