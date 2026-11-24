"""Safe JSON checkpoints for expensive rail parsing; no executable serialization."""
from collections import defaultdict
import gzip
import hashlib
import inspect
import json
import os
from pathlib import Path
import uuid
import types
from . import rail
from .common import digest


def stable_value(value):
    if isinstance(value, types.CodeType):
        fields = ('co_argcount', 'co_posonlyargcount', 'co_kwonlyargcount', 'co_nlocals',
                  'co_stacksize', 'co_flags', 'co_code', 'co_consts', 'co_names',
                  'co_varnames', 'co_freevars', 'co_cellvars', 'co_exceptiontable')
        return {'code': {name: stable_value(getattr(value, name)) for name in fields}}
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if isinstance(value, tuple):
        return {'tuple': [stable_value(item) for item in value]}
    if isinstance(value, frozenset):
        return {'frozenset': sorted((stable_value(item) for item in value), key=lambda item: json.dumps(item,sort_keys=True))}
    if isinstance(value, dict):
        return {key: stable_value(item) for key, item in value.items()}
    if isinstance(value, float):
        return {'float': value.hex()}
    if isinstance(value, complex):
        return {'complex': [value.real.hex(), value.imag.hex()]}
    if value is Ellipsis:
        return {'ellipsis': True}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError('Unsupported parser fingerprint constant: ' + type(value).__name__)


def checkpoint_key(entries,start,end):
    sources=hashlib.sha256()
    for item in sorted(entries,key=lambda row:(row.get('url',''),row['sha256'])):
        sources.update((item.get('url','')+'\0'+item['sha256']+'\n').encode('utf-8'))
    functions=[value for name,value in inspect.getmembers(rail,inspect.isfunction)]
    functions.extend([rail.RailMessages.__init__,rail.RailMessages.add,rail.RailMessages.archive])
    parser=digest([[stable_value(value.__code__),stable_value(value.__defaults__),stable_value(value.__kwdefaults__)] for value in functions])
    return digest({'sources':sources.hexdigest(),'parser':parser,'start':start,'end':end})


def load_checkpoint(path,key,start,end):
    path=Path(path)
    if not path.is_file():
        return None
    try:
        with gzip.open(path,'rt',encoding='utf-8') as stream:
            state=json.load(stream)
        if state.get('schema')!=1 or state.get('key')!=key:
            return None
        store=rail.RailMessages(start,end)
        store.paths=state['paths']
        store.cancellations=defaultdict(dict,state['cancellations'])
        store.documents=state['documents']
        store.inactive_cancellations=state['inactiveCancellations']
        if not isinstance(store.paths,dict) or not isinstance(store.documents,int) or store.documents<0:
            return None
        if any(record['key']!=identity or not set(record['days']).issubset(store.horizon)
               for identity,record in store.paths.items()):
            return None
        return store
    except (OSError,ValueError,KeyError,TypeError):
        return None


def save_checkpoint(path,key,store):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    state={'schema':1,'key':key,'paths':store.paths,'cancellations':dict(store.cancellations),
           'documents':store.documents,'inactiveCancellations':store.inactive_cancellations}
    try:
        with gzip.open(temporary,'wt',encoding='utf-8',compresslevel=3) as stream:
            json.dump(state,stream,ensure_ascii=False,separators=(',',':'))
        from fetch_priority_sources import replace_file
        replace_file(temporary,path)
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass
