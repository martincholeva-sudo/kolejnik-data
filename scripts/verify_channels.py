"""Verify public files and byte-identical installed-app branch publication."""
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo
from data_validation import fetch, timestamp, verify_deployed
from national.remote_publication import git
from national.publication import validate as validate_national

def verify_mobile(channel, manifest, repo=Path('target')):
    branches = ['main', 'work/rail-counter-times-20261027']
    git(repo, 'fetch', '--depth=1', '--filter=blob:none', '--no-tags', 'origin', *[
        '+refs/heads/'+b+':refs/remotes/origin/'+b for b in branches])
    expected = (Path('spojeni') / channel / 'manifest.json').read_bytes()
    for branch in branches:
        ref = 'refs/remotes/origin/'+branch+':spojeni/'+channel+'/'
        if git(repo, 'show', ref+'manifest.json') != expected:
            raise ValueError('Installed-app data channel does not match publication')
        today = dt.datetime.now(ZoneInfo('Europe/Prague')).date().isoformat()
        name = manifest['days'][today]
        if hashlib.sha256(git(repo, 'show', ref+name)).digest() != hashlib.sha256((Path('spojeni')/channel/name).read_bytes()).digest():
            raise ValueError('Installed-app daily data differ')

def verify_web_national(manifest, fetch_bytes=fetch):
    base = 'https://kolejnik.cz/spojeni/data-national/'
    current = json.loads(fetch_bytes(base+'manifest.json?verify='+str(int(time.time()))))
    if current['sha256'] != manifest['sha256'] or timestamp(current) < timestamp(manifest):
        raise ValueError('National publication has not reached the public website')
    today = dt.datetime.now(ZoneInfo('Europe/Prague')).date().isoformat()
    for name in [current['days'][today], current['walking']['file']]:
        data = fetch_bytes(base+name)
        wanted = current['files'].get(name, current['walking'])
        if len(data) != wanted['bytes'] or hashlib.sha256(data).hexdigest() != wanted['sha256']:
            raise ValueError('Published national bundle differs from the validated file')
    return current

if __name__ == '__main__':
    channel = sys.argv[1]
    if channel not in {'data-v2', 'data-national'}:
        raise ValueError('Unexpected publication channel')
    folder = Path('spojeni') / channel
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if channel == 'data-national':
        validate_national(folder, manifest)
    deadline = time.monotonic()+900
    while True:
        try:
            if channel == 'data-national':
                verify_web_national(manifest)
            else:
                verify_deployed(manifest)
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(20)
    verify_mobile(channel, manifest)
    summary = f"Verified {channel}: web and installed-app branches; source check {manifest['checkedAt']}; {len(manifest['days'])} service days.\n"
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(summary)
    receipt = {'channel': channel, 'revision': manifest['sha256'],
        'sourceCheckedAt': manifest['checkedAt'], 'start': manifest['start'], 'end': manifest['end'],
        'deliveryVerifiedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
        'webAndMobileVerified': True, 'updaterCommit': os.environ.get('GITHUB_SHA'),
        'run': os.environ.get('GITHUB_RUN_ID')}
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
            f.write('receipt='+json.dumps(receipt,separators=(',',':'))+'\n')
