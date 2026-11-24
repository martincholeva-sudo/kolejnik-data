"""Collect a bounded real-format sample; failures never activate routing data."""
import argparse
import datetime as dt
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from fetch_priority_sources import sample_plan, download
from national.common import descendants, local, documents, sha_file
from national.rail import RailMessages
from national.ostrava import OstravaNetex, is_dpo, archive_documents


def audit(destination, year, through, existing_cache=None):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    cache = destination / 'downloads'
    cache.mkdir(exist_ok=True)
    samples = destination / 'samples'
    samples.mkdir(exist_ok=True)
    result = {
        'auditVersion': '2026-10-26.6', 'scope': 'cz-rail-ostrava',
        'sampleOnly': True, 'fullCoverageVerified': False,
        'nationalRealtimeConnected': False, 'ostravaRealtimeConnected': False,
        'apkActivated': False, 'formatSamplePassed': False,
        'rail': {'samples': 0, 'parsed': 0, 'archivesRead': 0, 'problems': []},
        'ostrava': {'selectedDocuments': 0, 'parsed': 0, 'archivesRead': 0,
                    'modes': [], 'problems': []}, 'sources': []}

    def problem(section, url, phase, error):
        message = type(error).__name__ + ': ' + str(error)
        if len(result[section]['problems']) < 30:
            result[section]['problems'].append({'source': url, 'phase': phase, 'error': message})
        result.setdefault('blockedBy', message)

    try:
        print('1/4 Kontrola vstupu oficialnich zdroju...', flush=True)
        receipts = {}
        if existing_cache is None:
            sources = sample_plan(year, through)
        else:
            for receipt_path in Path(existing_cache).glob('*.receipt.json'):
                receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
                data_path = receipt_path.with_name(receipt_path.name[:-len('.receipt.json')])
                if not data_path.is_file() or sha_file(data_path) != receipt.get('sha256'):
                    raise ValueError('Offline source checksum differs: ' + receipt_path.name)
                receipt['path'] = str(data_path.resolve())
                receipts[receipt['url']] = receipt
            sources = {'rail': sorted(url for url in receipts if '/draha/celostatni/szdc/' in url),
                       'ostrava': ['https://portal.cisjr.cz/pub/netex/NeTEx_DrahyMestske.zip',
                                   'https://portal.cisjr.cz/pub/netex/NeTEx_VerejnaLinkovaDoprava.zip']}
        def acquire(url):
            if existing_cache is None:
                return download(url, cache)
            if url not in receipts:
                raise ValueError('Required offline source is absent: ' + url)
            return dict(receipts[url])
        recent = [url for url in sources['rail'] if '/' + through[:7] + '/' in url]
        cancelled = [url for url in recent if url.rsplit('/', 1)[-1].startswith('cancel_')][:3]
        ordinary = [url for url in recent if not url.rsplit('/', 1)[-1].startswith('cancel_')][:3]
        if not ordinary or not cancelled:
            problem('rail', '', 'selection', ValueError('The selected rail update period has no train/cancellation sample'))
        rail = RailMessages()
        for index, url in enumerate(ordinary + cancelled, 1):
            print(('Ctu ulozeny vlakovy vzorek ' if existing_cache else 'Stahuji vlakovy vzorek ') + str(index) + ': ' + url, flush=True)
            try:
                receipt = acquire(url)
                result['sources'].append(receipt)
                count = 0
                for root, origin in documents(receipt['path']):
                    count += 1
                    if count > 20:
                        raise ValueError('Rail sample archive contains too many documents')
                    result['rail']['samples'] += 1
                    suffix = '' if count == 1 else '-' + str(count)
                    ET.ElementTree(root).write(samples / f'rail-{index}{suffix}.xml', encoding='utf-8', xml_declaration=True)
                    try:
                        rail.add(root)
                        result['rail']['parsed'] += 1
                    except (ValueError, TypeError, AttributeError) as error:
                        problem('rail', url, 'parse-' + local(root.tag), error)
                if not count:
                    raise ValueError('Rail sample archive contains no XML documents')
                result['rail']['archivesRead'] += 1
            except Exception as error:
                problem('rail', url, 'download-or-container', error)

        print('2/4 Kontroluji oba archivy DPO; chyba vlaku tuto cast nepreskoci.', flush=True)
        end = (dt.date.fromisoformat(through) + dt.timedelta(days=13)).isoformat()
        city = OstravaNetex(through, end)
        for archive_index, url in enumerate(sources['ostrava']):
            print(('Ctu ulozeny archiv MHD: ' if existing_cache else 'Stahuji archiv MHD: ') + url, flush=True)
            try:
                receipt = acquire(url)
                result['sources'].append(receipt)
                for root, origin in archive_documents(receipt['path']):
                    if not any(is_dpo(operator) for operator in descendants(root, 'Operator')):
                        continue
                    result['ostrava']['selectedDocuments'] += 1
                    count = result['ostrava']['selectedDocuments']
                    if count <= 6:
                        ET.ElementTree(root).write(samples / f'ostrava-{archive_index}-{count}.xml', encoding='utf-8', xml_declaration=True)
                    if count % 25 == 0:
                        print('Zkontrolovano dokumentu DPO: ' + str(count), flush=True)
                    try:
                        city.add(root, origin)
                        result['ostrava']['parsed'] += 1
                    except (ValueError, TypeError, AttributeError) as error:
                        problem('ostrava', url, 'parse-' + origin.rsplit('!', 1)[-1], error)
                result['ostrava']['archivesRead'] += 1
            except Exception as error:
                problem('ostrava', url, 'download-or-container', error)
        print('3/4 Kontrola provoznich kalendaru DPO...', flush=True)
        try:
            _, coverage = city.export()
            result['ostrava']['modes'] = coverage['modes']
            result['ostrava']['activeSampleTrips'] = coverage['activeTrips']
            result['ostrava']['sourceQuality'] = {key: coverage[key] for key in ('productionReady', 'reviewedOffersExcluded', 'referenceCheckCompleteness', 'modeCorrectedLines', 'activeMidnightJourneys', 'supersededServiceDays')}
        except Exception as error:
            problem('ostrava', '', 'export', error)
        result['formatSamplePassed'] = bool(
            ordinary and cancelled and result['rail']['parsed'] and
            result['rail']['archivesRead'] == len(ordinary + cancelled) and
            not result['rail']['problems'] and
            result['ostrava']['archivesRead'] == 2 and
            set(result['ostrava']['modes']) == {0, 3, 11} and
            not result['ostrava']['problems'])
    except Exception as error:
        result['blockedBy'] = type(error).__name__ + ': ' + str(error)
    print('4/4 Ukladam zpravu; kontrola nic neaktivuje v aplikaci.', flush=True)
    (destination / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='runtime/priority-audit')
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--through', required=True)
    parser.add_argument('--offline-cache')
    args = parser.parse_args()
    result = audit(args.out, args.year, args.through, args.offline_cache)
    raise SystemExit(0 if result.get('formatSamplePassed') else 1)
