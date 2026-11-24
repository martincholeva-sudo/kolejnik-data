"""Copy only the existing generated channel, never private application sources."""
import shutil
import sys
from pathlib import Path

if __name__ == '__main__':
    channel = sys.argv[1]
    if channel not in {'data-v2', 'data-national'}:
        raise ValueError('Unexpected data channel')
    source = Path('target/spojeni') / channel
    destination = Path('spojeni') / channel
    if not (source / 'manifest.json').is_file():
        raise ValueError('The previous valid publication is required')
    shutil.copytree(source, destination, dirs_exist_ok=True)
    print('Previous generated channel available:', channel)
