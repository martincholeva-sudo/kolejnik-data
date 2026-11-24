"""One archive avoids actions/cache's argument/stack limit on 210k source files."""
from pathlib import Path, PurePosixPath
import sys
import tarfile

ROOT = Path('runtime/national-refresh')
ARCHIVE = Path('runtime/national-inputs.tar')


def allowed(name):
    p = PurePosixPath(name)
    return (not p.is_absolute() and '..' not in p.parts and bool(p.parts)
            and (p.parts[0] == 'sources' or
                 (len(p.parts) == 2 and p.parts[0] == 'prepare'
                  and p.name.startswith('rail-state-') and p.name.endswith('.json.gz'))))


def pack(root=ROOT, archive=ARCHIVE):
    root, archive = Path(root), Path(archive)
    if not (root/'sources').is_dir():
        raise ValueError('No source cache to pack')
    archive.parent.mkdir(parents=True, exist_ok=True)
    def public_inputs(info):
        if not allowed(info.name) or not (info.isfile() or info.isdir()):
            raise ValueError('Unexpected cache member')
        return info
    with tarfile.open(archive, 'w|') as out:
        out.add(root/'sources', arcname='sources', filter=public_inputs)
        for checkpoint in sorted((root/'prepare').glob('rail-state-*.json.gz')):
            out.add(checkpoint, arcname='prepare/'+checkpoint.name, filter=public_inputs)


def restore(root=ROOT, archive=ARCHIVE):
    root, archive = Path(root), Path(archive)
    if not archive.exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r|') as source:
        for member in source:
            if not allowed(member.name) or not (member.isfile() or member.isdir()):
                raise ValueError('Unexpected cache member')
            source.extract(member, root, filter='data')


if __name__ == '__main__':
    {'pack': pack, 'restore': restore}[sys.argv[1]]()
