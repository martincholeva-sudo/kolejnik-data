"""Publish one validated feed to main and the installed APK compatibility branch."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from .common import ImportErrorDetail
from .publication import validate
from data_validation import validate_public

CHANNEL = 'spojeni/data-national'
NAMES = re.compile(r'(?:manifest\.json|[a-f0-9]{12}-\d{4}-\d{2}-\d{2}\.json\.gz|walking-[a-f0-9]{16}\.json)')


def git(repo, *args, env=None, data=None):
    result = subprocess.run(['git', '-C', str(repo), *args], input=data,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise ImportErrorDetail('Git '+args[0]+': '+result.stderr.decode('utf-8', 'replace').strip())
    return result.stdout


PID_NAMES = re.compile(r'(?:manifest\.json|[a-f0-9]{12}-\d{4}-\d{2}-\d{2}\.json\.gz|walking-links\.json)')


def tracked(repo, parent, channel=CHANNEL, names=NAMES):
    result = {}
    for row in git(repo, 'ls-tree', '-r', '-z', parent, '--', channel).split(b'\0'):
        if not row:
            continue
        header, path = row.split(b'\t', 1)
        mode, kind, sha = header.decode().split()
        name = path.decode('utf-8').removeprefix(channel+'/')
        if mode != '100644' or kind != 'blob' or not names.fullmatch(name):
            raise ImportErrorDetail('Unexpected content in the generated channel: '+name)
        result[name] = sha
    return result


def stamp(metadata):
    value = dt.datetime.fromisoformat(metadata['checkedAt'].replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ImportErrorDetail('Publication verification needs a timezone')
    return value


def candidate(repo, parent, blobs, metadata, index, channel=CHANNEL, names=NAMES):
    previous = tracked(repo, parent, channel, names)
    if 'manifest.json' in previous:
        old = json.loads(git(repo, 'cat-file', 'blob', previous['manifest.json']))
        if stamp(old) > stamp(metadata):
            raise ImportErrorDetail('Refusing to replace newer remote source verification')
    for name, sha in blobs.items():
        if name not in {'manifest.json', 'walking-links.json'} and name in previous and previous[name] != sha:
            raise ImportErrorDetail('Immutable remote data differ: '+name)
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    git(repo, 'read-tree', parent, env=env)
    changes = b''.join(('0 '+('0'*40)+'\t'+channel+'/'+name+'\0').encode() for name in previous)
    changes += b''.join(('100644 '+sha+'\t'+channel+'/'+name+'\0').encode() for name, sha in blobs.items())
    git(repo, 'update-index', '-z', '--index-info', env=env, data=changes)
    tree = git(repo, 'write-tree', env=env).decode().strip()
    if tree == git(repo, 'rev-parse', parent+'^{tree}').decode().strip():
        return parent
    return git(repo, 'commit-tree', tree, '-p', parent, '-m',
        'Refresh verified timetable: '+channel).decode().strip()


def publish_refs(repo, public, branches=('main', 'work/rail-counter-times-20261027'), *,
                 remote='origin', now=None, before_push=None, attempts=3, channel=CHANNEL):
    repo, public = Path(repo).resolve(), Path(public).resolve()
    if channel not in {CHANNEL, 'spojeni/data-v2'} or public.name != channel.rsplit('/', 1)[1] or len(branches) != 2 or len(set(branches)) != 2:
        raise ImportErrorDetail('Two distinct national publication branches are required')
    for branch in branches:
        git(repo, 'check-ref-format', 'refs/heads/'+branch)
    names = NAMES if channel == CHANNEL else PID_NAMES
    runtime = repo/'runtime'
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='national-git-', dir=runtime) as name:
        root = Path(name).resolve()
        assert root.is_relative_to(runtime.resolve()) and root != runtime.resolve()
        snapshot = root/public.name
        snapshot.mkdir()
        for source in public.iterdir():
            if source.is_symlink() or source.is_junction() or not source.is_file() or not names.fullmatch(source.name):
                raise ImportErrorDetail('Unexpected prepared publication file: '+source.name)
            shutil.copyfile(source, snapshot/source.name)
        metadata = json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
        if channel == CHANNEL:
            validate(snapshot, metadata, now)
        else:
            validate_public(snapshot, manifest=metadata, now=now)
        blobs = {path.name:git(repo, 'hash-object', '-w', '--', str(path)).decode().strip()
                 for path in sorted(snapshot.iterdir())}
        last = None
        for attempt in range(attempts):
            git(repo, 'fetch', '--depth=1', '--filter=blob:none', '--no-tags', remote, *[
                '+refs/heads/'+b+':refs/remotes/'+remote+'/'+b for b in branches])
            parents = {b:git(repo, 'rev-parse', 'refs/remotes/'+remote+'/'+b).decode().strip() for b in branches}
            commits = {b:candidate(repo, parents[b], blobs, metadata, root/('index-'+str(i)), channel, names)
                       for i, b in enumerate(branches)}
            if before_push:
                before_push(attempt, commits)
            try:
                # A concurrent change rejects both updates; never force either history.
                git(repo, 'push', '--atomic', remote, *[
                    commits[b]+':refs/heads/'+b for b in branches])
            except ImportErrorDetail as error:
                last = error
                continue
            return {'success':True, 'revision':metadata['sha256'], 'branches':commits,
                    'previousHeads':parents, 'atomic':True, 'attempts':attempt+1}
        raise ImportErrorDetail('Shared publication failed; remote histories were not forced: '+str(last))
