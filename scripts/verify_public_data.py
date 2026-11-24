"""Verify local snapshots, and the actual files served after deployment."""
import argparse,json,time
from pathlib import Path
from data_validation import validate_public,verify_deployed

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--deployed',action='store_true');parser.add_argument('--url',default='https://kolejnik.cz/spojeni/data-v2/');args=parser.parse_args()
    manifest=validate_public(Path('spojeni/data-v2'));print('Validated daily timetable bundles:',len(manifest['days']))
    if args.deployed:
        deadline=time.monotonic()+480;last=None
        while time.monotonic()<deadline:
            try:
                verify_deployed(manifest,args.url);print('Verified public manifest, current daily bundle and walking links:',manifest['sha256']);return
            except Exception as error:last=error
            time.sleep(min(15,max(0,deadline-time.monotonic())))
        raise RuntimeError('Validated timetable is not completely available on the public host') from last

if __name__=='__main__':main()
