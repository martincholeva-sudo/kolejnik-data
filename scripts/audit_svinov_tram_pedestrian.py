"""Audit the retained Svinov map with explicitly reviewed plan stair connections."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from national.common import ImportErrorDetail
from national.pedestrian import analyse_svinov

ROOT = Path(__file__).resolve().parents[1]


def audit(source, access, plan=None):
    raw = Path(source).read_bytes()
    if hashlib.sha256(raw).hexdigest() != access.get('sourceFileSha256'):
        raise ImportErrorDetail('Retained pedestrian source file changed')
    if plan is not None and hashlib.sha256(Path(plan).read_bytes()).hexdigest() != access.get('planSha256'):
        raise ImportErrorDetail('Retained official plan changed')
    report = analyse_svinov(json.loads(gzip.decompress(raw)), access)
    report['sourceSnapshotFile'] = Path(source).name
    report['sourceSnapshotSha256'] = hashlib.sha256(raw).hexdigest()
    report['sourceContentSha256'] = access['sourceContentSha256']
    report['model'] = {
        'flatMetresPerSecond': 1.15, 'stairMetresPerSecond': 0.6,
        'additionalSecondsPerMappedStairWay': 20,
        'planConnectionSeconds': {r['platform']: r['seconds'] for r in access['connections']},
        'planConnectionGeometrySurveyed': False, 'planConnectionMetresIncluded': False,
        'boardingAlightingUncertaintySeconds': 60, 'samplingPaddingMetres': 7.5,
        'roundingSeconds': 30, 'surveyedWalkingDuration': False,
        'escalatorOperationAssumed': False, 'liftOperationAssumed': False,
        'unknownTrainPosition': 'worst across the full extent of all four mapped platforms',
        'durationLimit': 'Modeled estimate; not a measured time or a guaranteed upper bound.'}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'tests/fixtures/streets/svinov-full-osm-20261027.json.gz')
    parser.add_argument('--access', type=Path, default=ROOT/'config/svinov-fixed-stair-connections.json')
    parser.add_argument('--plan', type=Path, help='Optionally verify retained PDF bytes as well')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.source, json.loads(args.access.read_text(encoding='utf-8')), args.plan)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'physicalRoutes': len(report['results']), 'estimatedDurations': True,
                      'railToTramSeconds': {r['platform']:r.get('conservativeWalkingSeconds') for r in report['results'] if r['direction']=='rail-to-tram'}}))
