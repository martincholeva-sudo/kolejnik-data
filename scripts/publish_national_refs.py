"""Publish verified national files without changing either branch's application code."""
import argparse
import json
from pathlib import Path
from national.remote_publication import publish_refs
ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=ROOT)
    p.add_argument('--public',type=Path,default=ROOT/'spojeni/data-national')
    p.add_argument('--branches',nargs=2,default=['main','work/rail-counter-times-20261027'])
    p.add_argument('--report',type=Path,default=ROOT/'runtime/national-refresh/publish-report.json')
    a=p.parse_args()
    result=publish_refs(a.repo,a.public,tuple(a.branches))
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
