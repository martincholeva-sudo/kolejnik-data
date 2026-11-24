"""Store only a verified, non-sensitive publication receipt in this public project."""
import json
import os
from pathlib import Path
import re

def save(raw, root=Path('status')):
    data = json.loads(raw)
    allowed = {'channel','revision','sourceCheckedAt','start','end','deliveryVerifiedAt',
               'webAndMobileVerified','updaterCommit','run'}
    if set(data) != allowed or data['channel'] not in {'data-v2','data-national'}:
        raise ValueError('Unexpected receipt fields')
    if data['webAndMobileVerified'] is not True or not re.fullmatch('[a-f0-9]{64}',data['revision']):
        raise ValueError('Only successfully verified deliveries can be recorded')
    root = Path(root)
    root.mkdir(exist_ok=True)
    (root/(data['channel']+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__ == '__main__':
    save(os.environ['PUBLICATION_RECEIPT'])
