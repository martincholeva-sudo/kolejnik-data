"""Prepare rail + DPO + PID candidate files; never activate them in an installed APK."""
import argparse
import datetime as dt
import gzip
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from national.common import ImportErrorDetail, iso_day, sha_file, documents
from national.rail import RailMessages
from national.rail_cache import checkpoint_key, load_checkpoint, save_checkpoint
from national.ostrava import OstravaNetex
from national.bundle import compose, write_candidate


def prepare(inputs_file, start, days, destination, interchanges=None, operator_names=None, diagnostic_incomplete=False, platform_mappings=None, locations=None, station_register=None, link_builder=None, return_network=False, location_builder=None, station_aliases=None):
    inputs_file = Path(inputs_file).resolve()
    inputs = json.loads(inputs_file.read_text(encoding='utf-8-sig'))
    if inputs.get('schema') != 1 or set(inputs.get('sources', {})) != {'rail', 'ostrava', 'pid'}:
        raise ImportErrorDetail('Input receipt must include rail, Ostrava and PID sources')
    if not inputs.get('sourcePlanComplete'):
        raise ImportErrorDetail('Incomplete input source plan')
    end = (iso_day(start) + dt.timedelta(days=days-1)).isoformat()
    if not 1 <= days <= 30:
        raise ImportErrorDetail('Prepare 1 to 30 requested days')
    # Include adjacent service days for public journeys crossing midnight.
    load_start = (iso_day(start) - dt.timedelta(days=1)).isoformat()
    load_end = (iso_day(end) + dt.timedelta(days=1)).isoformat()
    destination = Path(destination).resolve()
    if destination.name in ('data-v2', 'data'):
        raise ImportErrorDetail('Use an isolated candidate directory')
    destination.mkdir(parents=True, exist_ok=True)
    for kind, entries in inputs['sources'].items():
        if not entries:
            raise ImportErrorDetail('Source plan contains an empty section: ' + kind)
        def verify(entry):
            path = Path(entry['path'])
            if not path.is_absolute():
                path = inputs_file.parent / path
            # Receipts already contain absolute paths. Avoid resolving every
            # ancestor of 190,000 files again; hash the actual opened bytes.
            entry['resolvedPath'] = os.path.abspath(path)
            try:
                actual = sha_file(path)
            except OSError as error:
                raise ImportErrorDetail('Cannot read input source: ' + kind + ': ' + path.name) from error
            if actual != entry.get('sha256'):
                raise ImportErrorDetail('Input checksum differs from its download receipt: ' + kind)
        with ThreadPoolExecutor(max_workers=4) as workers:
            # Bounded batches avoid retaining 190,000 future objects.
            for start_index in range(0, len(entries), 500):
                list(workers.map(verify, entries[start_index:start_index + 500]))
                count = min(start_index + 500, len(entries))
                if count == len(entries) or count % 10000 == 0:
                    print(f'Overuji {kind}: {count}/{len(entries)} kontrolnich souctu', flush=True)
    names_path = Path(operator_names) if operator_names else Path(__file__).resolve().parents[1] / 'config/rail-operator-names.json'
    names = json.loads(names_path.read_text(encoding='utf-8-sig')) if names_path.exists() else {}
    if not isinstance(names, dict) or any(not isinstance(code, str) or not isinstance(name, str) or not name.strip()
                                          for code, name in names.items()):
        raise ImportErrorDetail('Operator names must map source codes to nonempty names')
    key = checkpoint_key(inputs['sources']['rail'], load_start, load_end)
    rail_state = destination / ('rail-state-' + key[:16] + '.json.gz')
    rail = load_checkpoint(rail_state, key, load_start, load_end)
    if rail is None:
        rail = RailMessages(load_start, load_end)
        entries = inputs['sources']['rail']
        def read_update(entry):
            values = list(documents(entry['resolvedPath']))
            if len(values) != 1:
                raise ImportErrorDetail('An individual rail update must contain exactly one XML message')
            return values[0]
        with ThreadPoolExecutor(max_workers=4) as workers:
            # Bound retained XML trees to eight individual update messages.
            # Large base archives stay streamed; all store mutations stay ordered.
            for offset in range(0, len(entries), 8):
                batch = entries[offset:offset+8]
                pending = [workers.submit(read_update, entry) if entry.get('url', '').endswith('.xml.zip') else None
                           for entry in batch]
                for position, (entry, future) in enumerate(zip(batch, pending), offset+1):
                    if future is None:
                        rail.archive(entry['resolvedPath'])
                    else:
                        root, origin = future.result()
                        try:
                            rail.add(root)
                        except (ValueError, TypeError, AttributeError) as error:
                            raise ImportErrorDetail(origin + ': ' + str(error)) from error
                    if position == 1 or position % 5000 == 0:
                        print(f'Vlaky: {position}/{len(entries)} archivu, {len(rail.paths)} identit tras', flush=True)
        save_checkpoint(rail_state, key, rail)
        print('Ulozeny kontrolni bod nacitani vlaku.', flush=True)
    else:
        print('Pouzivam overeny kontrolni bod nacitani vlaku.', flush=True)
    rail_network, rail_report = rail.export(load_start, load_end, names, allow_incomplete=diagnostic_incomplete)
    ostrava = OstravaNetex(load_start, load_end)
    for entry in inputs['sources']['ostrava']:
        ostrava.archive(entry['resolvedPath'])
    ostrava_network, ostrava_report = ostrava.export()
    mappings_path = Path(platform_mappings) if platform_mappings else Path(__file__).resolve().parents[1] / 'config/ostrava-platform-mappings.json'
    if platform_mappings or (inputs.get('synthetic') is not True and mappings_path.exists()):
        from national.platforms import apply_platform_mappings
        mappings = json.loads(mappings_path.read_text(encoding='utf-8-sig'))
        ostrava_network = apply_platform_mappings(ostrava_network, mappings)
        # A custom catalog fully overrides the reviewed real-source defaults.
        tram_mappings = Path(__file__).resolve().parents[1] / 'config/svinov-tram-platform-mappings.json'
        if not platform_mappings and inputs.get('synthetic') is not True and tram_mappings.exists():
            ostrava_network = apply_platform_mappings(ostrava_network,
                json.loads(tram_mappings.read_text(encoding='utf-8-sig')))
        ostrava_report = ostrava_network['coverage']
    locations_path = Path(locations) if locations else Path(__file__).resolve().parents[1] / 'config/priority-location-mappings.json'
    if locations or (inputs.get('synthetic') is not True and locations_path.exists()):
        from national.locations import load_catalog, apply_locations
        location_catalog, geometry = location_builder(rail_network, ostrava_network) if location_builder else load_catalog(locations_path)
        rail_network = apply_locations(rail_network, location_catalog, geometry, 'cis-rail')
        ostrava_network = apply_locations(ostrava_network, location_catalog, geometry, 'cis-ostrava')
        rail_report['stopsWithKnownCoordinates'] = rail_network['coverage']['stopsWithKnownCoordinates']
        ostrava_report = ostrava_network['coverage']
    register_path = Path(station_register) if station_register else Path(__file__).resolve().parents[1] / 'reports/station-register-20261027'
    if station_register or (inputs.get('synthetic') is not True and register_path.exists()):
        from national.station_locations import load_register, apply_station_locations
        register_manifest, register_rows = load_register(register_path)
        rail_network, register_report = apply_station_locations(rail_network, register_manifest, register_rows, station_aliases)
        rail_report['stationReferenceLocations'] = register_report
        rail_report['stopsWithKnownCoordinates'] = rail_network['coverage']['stopsWithKnownCoordinates']
    if not rail_network['trips'] or not ostrava_network['trips']:
        raise ImportErrorDetail('Rail or Ostrava scope is empty in the requested horizon')
    if set(ostrava_report['modes']) != {0, 3, 11}:
        raise ImportErrorDetail('Ostrava candidate is missing tram, bus or trolleybus services')
    if len(inputs['sources']['pid']) != 1:
        raise ImportErrorDetail('Expected one complete current PID archive')
    from import_gtfs import build
    destination = Path(destination).resolve()
    if destination.name in ('data-v2', 'data'):
        raise ImportErrorDetail('Use an isolated candidate directory')
    destination.mkdir(parents=True, exist_ok=True)
    pid_snapshot = destination / 'pid-preparation.json.gz'
    build(inputs['sources']['pid'][0]['resolvedPath'], pid_snapshot)
    with gzip.open(pid_snapshot, 'rt', encoding='utf-8') as stream:
        pid_network = json.load(stream)
    if pid_network['start'] > start or pid_network['end'] < end:
        raise ImportErrorDetail('PID source does not cover the requested horizon')
    if link_builder and interchanges:
        raise ImportErrorDetail('Choose a link builder or a retained interchange file')
    links = link_builder(rail_network, ostrava_network) if link_builder else (json.loads(Path(interchanges).read_text(encoding='utf-8-sig')) if interchanges else None)
    combined = compose([('pid', pid_network), ('rail', rail_network), ('ostrava', ostrava_network)], load_start, load_end, links)
    # Keep adjacent calendars in the files, but expose only the requested dates.
    combined['start'], combined['end'] = start, end
    synthetic = inputs.get('synthetic') is True
    report = {'scope': 'cz-rail-ostrava', 'realDataImported': not synthetic, 'syntheticInputs': synthetic,
        'rail': rail_report, 'ostrava': ostrava_report, 'inputSourcePlanComplete': True,
        'downloadReceiptsVerified': True, 'verifiedInterchangeLinks': len(combined['interchangeEvidence']),
        'diagnosticIncompleteTimingsAllowed': diagnostic_incomplete,
        'completeRailTimingsExported': rail_report['completeTimingsExported'],
        'nationalRealtimeConnected': False, 'ostravaRealtimeConnected': False,
        'referenceJourneysVerified': False, 'apkActivated': False, 'productionPublished': False,
        'remaining': ['Compare imported calendars and services with real reference journeys',
                      'Verify station identities and pedestrian interchanges at Prague and Ostrava hubs',
                      'Verify same-station transfer minima and Ostrava stop/platform connections',
                      'Attach only licensed, explicitly matched realtime feeds',
                      'Deploy the server and enable remote nationwide routing in the APK after verification']}
    rail_report['realSourceValidated'] = not synthetic
    ostrava_report['realSourceValidated'] = not synthetic
    from national.common import digest
    combined.pop('sha256', None)
    combined['sha256'] = digest(combined)
    if return_network:
        return combined, report
    metadata = write_candidate(combined, destination / 'candidate', report)
    # Carry adjacent calendar/trip records for daily bundling, but not extra dates in the manifest.
    public_days = {day: name for day, name in metadata['days'].items() if start <= day <= end}
    metadata['days'] = public_days
    (destination / 'candidate/manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'preparation-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--days', type=int, default=14)
    parser.add_argument('--out', default='runtime/priority-preparation')
    parser.add_argument('--interchanges')
    parser.add_argument('--operator-names')
    parser.add_argument('--platform-mappings', help='Reviewed exact CIS-call to physical-platform labels')
    parser.add_argument('--locations', help='Reviewed exact source-call/reference geometry catalog')
    parser.add_argument('--station-register', help='Reviewed exact-revision SR70 reference directory')
    parser.add_argument('--diagnostic-incomplete', action='store_true', help='Only for isolated diagnostics; never release omitted timings')
    args = parser.parse_args()
    print(json.dumps(prepare(args.inputs, args.start, args.days, args.out, args.interchanges, args.operator_names, args.diagnostic_incomplete, args.platform_mappings, args.locations, args.station_register), ensure_ascii=False, indent=2))
