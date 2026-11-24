"""Atomically publish validated PID data to web and installed-app branches."""
import argparse
import json
from pathlib import Path
from national.remote_publication import publish_refs

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--public', type=Path, default=Path('spojeni/data-v2'))
    p.add_argument('--report', type=Path, default=Path('runtime/pid-publish.json'))
    a = p.parse_args()
    # PID web and installed APK share kolejnik.cz. The compatibility branch is
    # required only by the older national APK; avoid six needless preview builds/day.
    result = publish_refs(a.repo, a.public, branches=('main',), channel='spojeni/data-v2')
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))
