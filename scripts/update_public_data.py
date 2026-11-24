"""Publish only validated revisions; a provider/check failure preserves the old manifest."""
import hashlib,json,urllib.request,datetime as dt,os,tempfile,shutil
from pathlib import Path
from import_gtfs import build
from data_validation import validate_public

def download():
    with urllib.request.urlopen('https://data.pid.cz/PID_GTFS.zip',timeout=120) as response:raw=response.read(200_000_001)
    if len(raw)>200_000_000:raise ValueError('Oversized GTFS')
    return raw

def update(public=Path('spojeni/data-v2'),runtime=Path('runtime'),fetch=download,build_snapshot=build,validate=validate_public):
    public=Path(public).resolve();runtime=Path(runtime).resolve();public.mkdir(parents=True,exist_ok=True);runtime.mkdir(parents=True,exist_ok=True)
    manifest=public/'manifest.json';old=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    raw=fetch();sha=hashlib.sha256(raw).hexdigest();checked=dt.datetime.now(dt.timezone.utc).isoformat()
    if old.get('sha256')==sha:
        candidate={**old,'checkedAt':checked};validate(public,manifest=candidate)
        pending=public/'manifest.tmp';pending.write_text(json.dumps(candidate,ensure_ascii=False),encoding='utf-8');os.replace(pending,manifest)
        print('Provider snapshot unchanged; existing files verified.');return candidate
    source=runtime/'PID_GTFS.zip';source.write_bytes(raw)
    with tempfile.TemporaryDirectory(prefix='pid-stage-',dir=runtime) as tmp:
        stage=Path(tmp).resolve();assert stage.is_relative_to(runtime) and stage!=runtime
        walking=public/'walking-links.json'
        if walking.exists():shutil.copy2(walking,stage/'walking-links.json')
        build_snapshot(source,runtime/'network.json.gz',stage,carry_from=public)
        candidate=json.loads((stage/'manifest.json').read_text(encoding='utf-8'));candidate['checkedAt']=checked
        if candidate.get('sha256')!=sha:raise ValueError('Imported revision differs from the provider snapshot')
        validate(stage,manifest=candidate)
        for name in candidate['days'].values():os.replace(stage/name,public/name)
        pending=public/'manifest.tmp';pending.write_text(json.dumps(candidate,ensure_ascii=False),encoding='utf-8');os.replace(pending,manifest)
    keep=set(candidate['days'].values())|set(old.get('days',{}).values())
    for file in public.glob('*.json.gz'):
        if file.name not in keep and file.resolve().parent==public:file.unlink()
    print('Validated revision published:',candidate['sha256']);return candidate

if __name__=='__main__':update()
