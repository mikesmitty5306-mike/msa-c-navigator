"""Materialize the version-one JSON schemas and a small public-data fixture subset."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://msac-navigator.invalid/schemas/'
S = {'type': 'string', 'minLength': 1}
N = {'type': ['string', 'null'], 'minLength': 1}
I = {'type': 'integer', 'minimum': 0}
NI = {'type': ['integer', 'null']}
NUM = {'type': ['number', 'null']}
B = {'type': 'boolean'}
DATE = {'type': ['string', 'null'], 'format': 'date'}
TIME = {'type': ['string', 'null'], 'format': 'date-time', 'pattern': 'Z$'}
HASH = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
STATUS = {'enum': ['RECRUITING', 'NOT_YET_RECRUITING', 'ENROLLING_BY_INVITATION', 'ACTIVE_NOT_RECRUITING',
                   'COMPLETED', 'TERMINATED', 'SUSPENDED', 'WITHDRAWN', 'UNKNOWN', 'AVAILABLE', 'NO_LONGER_AVAILABLE']}
FIT = {'enum': ['includes_msa_c', 'any_msa', 'msa_p_focused', 'excludes_msa_c']}
EVIDENCE = {'enum': ['clinical', 'human_observational', 'postmortem', 'animal', 'in_vitro', 'review', 'other']}
OUTCOME = {'enum': ['positive', 'negative', 'mixed', 'unclear', None]}
SUBTYPE = {'enum': ['msa_c', 'msa_p', 'msa_general']}
OUTCOMES = ['positive', 'negative', 'mixed', 'unclear']
CATEGORIES = ['human', 'animal', 'in_vitro']
GROUPS = ['recruiting', 'completed', 'stopped', 'ongoing']
LIB_COLUMNS = ['uid','title','year','journal','first_author','evidence','outcome','subtype',
               'preprint','solid','mechanisms','url','keywords']


def obj(properties, *, version=False, optional=()):
    properties = dict(properties)
    if version:
        properties['schema_version'] = {'const': 1}
    return {'type': 'object', 'properties': properties,
            'required': [k for k in properties if k not in optional], 'additionalProperties': False}


def arr(item):
    return {'type': 'array', 'items': item}


def tuple_row(items):
    return {'type': 'array', 'prefixItems': items, 'items': False, 'minItems': len(items), 'maxItems': len(items)}


def ref(name):
    return {'$ref': 'common.json#/$defs/' + name}


def nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def schemas():
    # curated/source/design/note/counted: hand-checked results (contract/curated_results.json); optional.
    study = obj(dict(uid=S,title=N,year=NI,journal=N,first_author=N,url=N,evidence=EVIDENCE,
                     outcome=OUTCOME,solid=B,preprint=B,subtype=SUBTYPE,curated=B,
                     source={'enum':['peer_reviewed','company_topline']},design=S,note=S,counted=B),
                optional=('curated','source','design','note','counted'))
    contact = obj({k: N for k in ['name','role','phone','phone_ext','email']})
    site = obj(dict(facility=N,city=N,state=N,country=N,status=nullable(STATUS),lat=NUM,lon=NUM,contacts=arr(ref('contact'))))
    result = obj({c: obj({o: I for o in OUTCOMES}) for c in CATEGORIES})
    grade = obj(dict(id=S,name=S,kind={'enum':['mechanism','symptomatic','biomarker']},description=N,agents=arr(S),
        grade={'enum':['A','B','C','D','n/a']},modifier={'enum':['+','-',None]},reason=S,results=result,
        trials=obj({g:I for g in GROUPS}),activity=obj({k:I for k in ['papers','msa_c_papers','recent_3y','prior_3y']})),
        optional=('modifier',))
    compact = obj(dict(id=S,title=N,status=STATUS,status_label=S,open=B,active=B,phase=S,
        max_phase={'enum':[0,0.5,1,2,3]},type=N,sponsor=N,fit=FIT,mechanisms=arr(S),min_age=NUM,max_age=NUM,sex=N,
        sites=arr(tuple_row([NUM,NUM,N,N,B])),last_update=DATE,
        status_changed=nullable(obj({'from': STATUS,'to':STATUS,'date':DATE})),
        rank=nullable(obj(dict(position={'type':'integer','minimum':1},score={'type':'number'},target=S)))))
    source = obj(dict(refreshed_at=TIME,records=I,checked_at=TIME,data_timestamp=N), optional=('checked_at','data_timestamp'))
    common = {'$defs': dict(study=study,contact=contact,site=site,grade=grade,trial=compact)}
    docs = {
        'common': common,
        'meta': obj(dict(build_id=S,generated_at=TIME,sources=obj({k:source for k in ['clinicaltrials','pubmed','europepmc']}),
            counts=obj({k:I for k in ['articles','msa_c_articles','preprints','trials','open_trials']}),
            files={'type':'object','additionalProperties':obj(dict(bytes=I,sha256=HASH))},disclaimer=S,
            data_as_of=obj({k:TIME for k in ['clinicaltrials','pubmed','europepmc']}),push_public_key=N),version=True,
            optional=('data_as_of','push_public_key')),
        'grades': obj(dict(meanings=obj({k:S for k in ['A','B','C','D','n/a']}),
            definitions=obj({k:S for k in ['solid_trial','major_failure','outcomes','grade_modifier','curated']},
                            optional=('grade_modifier','curated')),mechanisms=arr(ref('grade'))),version=True),
        'mechanism': obj(dict(id=S,studies=obj({c:obj({o:arr(ref('study')) for o in OUTCOMES}) for c in CATEGORIES}),
            trials=obj({g:arr(obj(dict(id=S,why_stopped=N,counts_as_failure=B)) if g=='stopped' else S) for g in GROUPS}),
            msa_c_recent=dict(arr(ref('study')),maxItems=8)),version=True),
        'trials': obj(dict(trials=arr(ref('trial'))),version=True),
        'trial': obj(dict(id=S,title=N,official_title=N,summary=N,status=STATUS,status_label=S,why_stopped=N,has_results=B,
            phase=S,type=N,sponsor=N,enrollment=NI,design=obj({k:N for k in ['allocation','masking','primary_purpose']}),
            start_date=DATE,completion_date=DATE,last_update=DATE,registry_url=S,conditions=arr(S),mechanisms=arr(S),fit=FIT,
            interventions=arr(obj(dict(type=N,name=N,description=N))),
            eligibility=obj(dict(min_age=N,max_age=N,sex=N,healthy_volunteers={'type':['boolean','null']},inclusion=arr(S),exclusion=arr(S))),
            contacts=arr(ref('contact')),sites=arr(ref('site')),inquiry=obj(dict(email=N,subject=S,body=S))),version=True),
        'library-index': obj(dict(columns={'const':LIB_COLUMNS},shards=arr(obj(dict(name=S,rows=I,sha256=HASH)))),version=True),
        'library': obj(dict(rows=arr(tuple_row([S,N,NI,N,N,EVIDENCE,OUTCOME,SUBTYPE,B,B,arr(S),N,N]))),version=True),
        'abstracts': obj(dict(abstracts={'type':'object','additionalProperties':N}),version=True),
        'cities': obj(dict(columns={'const':['name','region','country','lat','lon']},rows=arr(tuple_row([S,N,N,NUM,NUM]))),version=True),
        'phone-backup': obj(dict(app={'const':'msac-navigator'},backup_version={'const':1},exported_at=TIME,
            home_city=nullable(obj(dict(name=S,region=N,country=N,lat={'type':'number'},lon={'type':'number'}))),
            saved=arr(obj(dict(type={'enum':['trial','article']},id=S,title=N,url=N,note={'type':'string'},saved_at=TIME,last_seen_status=N)))))
    }
    for name, document in docs.items():
        write(ROOT / 'contract/schemas' / (name + '.json'), {
            '$schema':'https://json-schema.org/draft/2020-12/schema', '$id':BASE + name + '.json', **document})


def sample(source):
    """Subset existing public research fixtures; no personal or runtime state is copied."""
    out = ROOT / 'contract/sample'
    old_meta = json.loads((out/'meta.json').read_text(encoding='utf-8')) if (out/'meta.json').exists() else {}
    import sys
    sys.path.insert(0,str(ROOT))
    from msac.export import library_shard
    def read(name):
        return json.loads((source / name).read_text(encoding='utf-8'))
    def clean(v):
        if isinstance(v, str):
            return v or None
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {k:(x if x and re.fullmatch(r'\d{4}-\d{2}-\d{2}',x) else None)
                    if k in ('start_date','completion_date','last_update') else clean(x) for k,x in v.items()}
        return v
    payloads = {}
    grades = read('grades.json')
    wanted_mechanisms = {'asyn_aggregation','proteostasis','biomarkers'}
    grades['mechanisms'] = [m for m in grades['mechanisms'] if m['id'] in wanted_mechanisms]
    mids = {m['id'] for m in grades['mechanisms']}
    trials = read('trials.json')['trials']
    wanted_trials = {'NCT04165486','NCT00997672','NCT03589976','NCT07514923','NCT01793168'}
    selected = sorted([t for t in trials if t['id'] in wanted_trials],key=lambda t:t['id'])
    if len(selected)!=5 or len(grades['mechanisms'])!=3:
        raise ValueError('Sample source is missing required smoke-test mechanisms or trials')
    ids = {t['id'] for t in selected}
    for t in selected:
        t['mechanisms'] = [m for m in t['mechanisms'] if m in mids]
        detail = read('trials/' + t['id'] + '.json')
        detail['mechanisms'] = t['mechanisms']
        payloads['trials/' + t['id'] + '.json'] = detail
    payloads['trials.json'] = {'schema_version':1,'trials':selected}
    index = read('library/index.json')
    rows = []
    for shard in index['shards']:
        rows.extend(read('library/' + shard['name'] + '.json')['rows'])
    # Include the actual negative human studies opened by the smoke tests, plus
    # sirolimus search results, before filling the remaining sample with recent papers.
    wanted_papers = {s['uid'] for mid in ('asyn_aggregation','proteostasis')
                     for s in read('mechanisms/'+mid+'.json')['studies']['human']['negative']}
    wanted_papers.update(r[0] for r in rows if 'sirolimus' in json.dumps(r).casefold())
    required_rows = [r for r in rows if r[0] in wanted_papers][:50]
    selected_papers = {r[0] for r in required_rows}
    rows = required_rows + [r for r in rows if r[0] not in selected_papers][:50-len(required_rows)]
    rows.sort(key=lambda r:(-(r[2] or 0),r[0]))
    uids = {r[0] for r in rows}
    for r in rows:
        r[10] = [m for m in r[10] if m in mids]
    shards = {}
    for row in rows:
        shards.setdefault(library_shard(row[2]),[]).append(row)
    index_shards = []
    for name,values in shards.items():
        content = {'schema_version':1,'rows':values}
        payloads['library/' + name + '.json'] = content
        shard_hash = hashlib.sha256(canonical(clean(content))).hexdigest()
        index_shards.append({'name':name,'rows':len(values),'sha256':shard_hash})
    payloads['library/index.json'] = {'schema_version':1,'columns':LIB_COLUMNS,'shards':index_shards}
    for i in range(256):
        key = f'{i:02x}'
        contents = read('abstracts/' + key + '.json')['abstracts']
        payloads['abstracts/' + key + '.json'] = {'schema_version':1,'abstracts':{k:v for k,v in contents.items() if k in uids}}
    for key in ['b','s']:
        payloads['cities/' + key + '.json'] = read('cities/' + key + '.json')
    for mid in sorted(mids):
        detail = read('mechanisms/' + mid + '.json')
        for outcomes in detail['studies'].values():
            for outcome in outcomes:
                outcomes[outcome] = [s for s in outcomes[outcome] if s['uid'] in uids]
        detail['msa_c_recent'] = [s for s in detail['msa_c_recent'] if s['uid'] in uids]
        for group, values in detail['trials'].items():
            detail['trials'][group] = [t for t in values if (t['id'] if isinstance(t,dict) else t) in ids]
        payloads['mechanisms/' + mid + '.json'] = detail
        grade = next(g for g in grades['mechanisms'] if g['id']==mid)
        grade['results'] = {c:{o:len(v) for o,v in groups.items()} for c,groups in detail['studies'].items()}
        grade['trials'] = {g:len(v) for g,v in detail['trials'].items()}
        relevant = [r for r in rows if mid in r[10]]
        year = int(read('meta.json')['generated_at'][:4])
        grade['activity'] = {'papers':len(relevant),'msa_c_papers':sum(r[7]=='msa_c' for r in relevant),
                             'recent_3y':sum(r[2] is not None and year-r[2]<3 for r in relevant),
                             'prior_3y':sum(r[2] is not None and 3<=year-r[2]<6 for r in relevant)}
    payloads['grades.json'] = grades
    # Recompute sample grade explanations from its own study subset, not the full dataset.
    import sys
    sys.path.insert(0,str(ROOT))
    from msac import analysis,mechanisms
    ranking = {}
    for grade in grades['mechanisms']:
        score = analysis.MechanismScore(mechanisms.BY_ID[grade['id']])
        content = payloads['mechanisms/' + grade['id'] + '.json']
        score.results = grade['results']
        score.n_articles = grade['activity']['papers']
        score.solid_positive = [s['uid'] for s in content['studies']['human']['positive'] if s['solid']]
        score.solid_negative = [s['uid'] for s in content['studies']['human']['negative'] if s['solid']]
        score.failed_trials = [t['id'] for t in content['trials']['stopped'] if t['counts_as_failure']]
        grade['grade'],grade['reason'] = score.grade,score.grade_reason
        ranking[grade['id']] = score.sort_key()
    grades['mechanisms'].sort(key=lambda g:ranking[g['id']])
    meta = read('meta.json')
    meta['counts'] = dict(articles=50,msa_c_articles=sum(r[7]=='msa_c' for r in rows),preprints=sum(r[8] for r in rows),
                          trials=5,open_trials=sum(t['open'] for t in selected))
    meta['data_as_of'] = {k:v['refreshed_at'] for k,v in meta['sources'].items()}
    for k,v in meta['sources'].items():
        v['records'] = 5 if k=='clinicaltrials' else 50
    meta['files'] = {}
    for path,value in sorted(payloads.items()):
        value = clean(value)
        write(out / path,value)
        raw = canonical(value)
        meta['files'][path] = {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    write(out / 'meta.json', meta)
    for name in set(old_meta.get('files',{}))-set(payloads):
        obsolete = (out/name).resolve()
        if obsolete.is_relative_to(out.resolve()) and obsolete.is_file():
            obsolete.unlink()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sample-from',type=Path)
    args = parser.parse_args()
    schemas()
    if args.sample_from:
        sample(args.sample_from)
