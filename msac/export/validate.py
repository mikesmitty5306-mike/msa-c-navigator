"""Validate every exported JSON file and its manifest against the binding contract."""
from pathlib import Path
from datetime import datetime
import hashlib
import json

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


def schema_name(relative):
    if relative == 'library/index.json':
        return 'library-index'
    if '/' in relative:
        return {'mechanisms':'mechanism','trials':'trial','library':'library',
                'abstracts':'abstracts','cities':'cities'}[relative.split('/')[0]]
    return relative.removesuffix('.json')


def validate_export(folder, schemas=None):
    folder = Path(folder)
    schemas = Path(schemas or Path(__file__).resolve().parents[2] / 'contract/schemas')
    docs = {p.stem:json.loads(p.read_text(encoding='utf-8')) for p in schemas.glob('*.json')}
    registry = Registry().with_resources((s['$id'],Resource.from_contents(s)) for s in docs.values())
    checker = FormatChecker()
    @checker.checks('date-time',raises=ValueError)
    def valid_time(value):
        if not isinstance(value,str):
            return True
        return value.endswith('Z') and datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is not None
    validators = {k:Draft202012Validator(s,registry=registry,format_checker=checker) for k,s in docs.items()}
    files = {p.relative_to(folder).as_posix():p for p in folder.rglob('*.json')}
    for name,path in sorted(files.items()):
        try:
            validators[schema_name(name)].validate(json.loads(path.read_text(encoding='utf-8')))
        except Exception as exc:
            raise ValueError(f'Invalid export {name}: {exc}') from exc
    meta = json.loads((folder/'meta.json').read_text(encoding='utf-8'))
    if set(meta['files']) != set(files)-{'meta.json'}:
        raise ValueError('Manifest must list every other JSON file exactly once')
    for name,entry in meta['files'].items():
        raw = files[name].read_bytes()
        if entry != {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}:
            raise ValueError('Manifest mismatch: ' + name)
    load = lambda name:json.loads(files[name].read_text(encoding='utf-8'))
    required = {'grades.json','trials.json','library/index.json'} | {f'abstracts/{i:02x}.json' for i in range(256)}
    if not required <= set(files):
        raise ValueError('Export is missing required files or abstract shards')
    grades,trials,index = load('grades.json'),load('trials.json'),load('library/index.json')
    required |= {f"mechanisms/{m['id']}.json" for m in grades['mechanisms']}
    required |= {f"trials/{t['id']}.json" for t in trials['trials']}
    required |= {f"library/{s['name']}.json" for s in index['shards']}
    if not required <= set(files):
        raise ValueError('Export is missing a referenced detail or library shard')
    rows = []
    for shard in index['shards']:
        name = f"library/{shard['name']}.json"
        values = load(name)['rows']
        if len(values)!=shard['rows'] or meta['files'][name]['sha256']!=shard['sha256']:
            raise ValueError('Library index mismatch: ' + name)
        rows.extend(values)
    if meta['counts']['articles']!=len(rows) or meta['counts']['trials']!=len(trials['trials']):
        raise ValueError('Metadata record counts do not match exported rows')
    return len(files)
