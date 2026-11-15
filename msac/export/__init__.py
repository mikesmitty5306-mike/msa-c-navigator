"""Deterministic, public-only version-one exports. No user database is opened."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import parse_qs, urlsplit

from .. import analysis, config, geo, mechanisms
from ..storage import Store

LIB_COLUMNS = ['uid','title','year','journal','first_author','evidence','outcome','subtype',
               'preprint','solid','mechanisms','url','keywords']
DEFINITIONS = {
    'solid_trial': 'A trial in people that was randomized and controlled (placebo, sham or comparison treatment), past the first safety stage, and not a pilot, open-label or single-group study.',
    'major_failure': "A solid human trial that found no benefit, or a trial stopped for futility, lack of effect, safety or its results. Trials stopped for funding or slow recruitment don't count.",
    'outcomes': "Positive, negative, mixed and unclear are what each treatment study's own conclusion says. Unclear includes studies that only reported safety.",
    'grade_modifier': analysis.MODIFIER_MEANING,
    'curated': "A few key human results are checked by hand. They replace the automatic verdict, count only toward the treatment's own mechanism, and are marked on the mechanism pages. Studies of 20 people or fewer are listed but don't count toward a grade.",
}


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8')


def nullable(value):
    return value if value != '' else None


def day(value):
    if not value or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        return None
    return value


def utc(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')


def study(article):
    return dict(uid=article.uid,title=nullable(article.title),year=article.year,journal=nullable(article.journal),
        first_author=article.authors[0] if article.authors else None,url=article.url,evidence=article.evidence,
        outcome=analysis.OUTCOME_COUNT.get(article.outcome),solid=article.solid_trial,
        preprint=article.is_preprint,subtype=article.subtype)


def curated_study(c, article=None):
    """A hand-checked human result as a study row: from the database article when there is one, else from the entry."""
    base = study(article) if article else dict(uid=c.key,title=c.title,year=c.year,journal=c.journal,first_author=None,
        url=c.url,evidence='clinical',preprint=False,subtype=c.subtype)
    return dict(base,evidence='clinical',outcome=c.outcome,solid=c.solid,curated=True,source=c.source,
                design=c.design_text,note=c.note,counted=c.counts)


def contact(value):
    return {key:nullable(value.get(key)) for key in ('name','role','phone','phone_ext','email')}


def detail(trial, mids):
    info = analysis.enrollment_info(trial)
    query = parse_qs(urlsplit(info['mailto']).query) if info['mailto'] else {}
    inquiry = dict(email=(info['mailto'][7:].split('?')[0] if info['mailto'] else None),
        subject=query.get('subject',[f'Inquiry about participating in {trial.nct_id}'])[0],
        body=query.get('body',[f'Hello,\n\nI am interested in learning about eligibility for {trial.title} ({trial.nct_id}).\n\nThank you.'])[0])
    return dict(schema_version=1,id=trial.nct_id,title=nullable(trial.title),official_title=nullable(trial.official_title),
        summary=nullable(trial.summary),status=trial.status,status_label=trial.status_label,
        why_stopped=nullable(trial.why_stopped),has_results=trial.has_results,phase=trial.phase_label,
        type=nullable(trial.study_type),sponsor=nullable(trial.sponsor),enrollment=trial.enrollment,
        design={k:nullable(getattr(trial,k)) for k in ('allocation','masking','primary_purpose')},
        start_date=day(trial.start_date),completion_date=day(trial.completion_date),last_update=day(trial.last_update),
        registry_url=trial.url,conditions=sorted(trial.conditions),mechanisms=mids,fit=trial.subtype_fit,
        interventions=[{k:nullable(v.get(k)) for k in ('type','name','description')} for v in trial.interventions],
        eligibility=dict(min_age=nullable(trial.min_age),max_age=nullable(trial.max_age),sex=nullable(trial.sex),
            healthy_volunteers=trial.healthy_volunteers,inclusion=info['inclusion'],exclusion=info['exclusion']),
        contacts=[contact(c) for c in trial.central_contacts],
        sites=[{**{k:nullable(loc.get(k)) for k in ('facility','city','state','country','status','lat','lon')},
                'contacts':[contact(c) for c in loc.get('contacts',[])]} for loc in trial.locations],inquiry=inquiry)


def library_shard(year):
    if year is None or year < 1990:
        return 'before-1990'
    if year >= 2015:
        return str(year)
    start = year//5*5
    return f'{start}-{start+4}'


def _load(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def documents(store, previous, *, build_time=None, cities_path=None, mechanism_ids=None):
    articles = sorted(store.iter_articles(), key=lambda a:(-(a.year or 0),a.uid))
    trials = sorted(store.iter_trials(),key=lambda t:t.nct_id)
    tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    state = dict(store.conn.execute('SELECT key,value FROM maintenance_state')) if 'maintenance_state' in tables else {}
    history = {r['nct_id']:dict(from_=r['previous_status'],to=r['status'],date=r['changed_date'])
               for r in store.conn.execute('SELECT * FROM trial_status_history')} if 'trial_status_history' in tables else {}
    runs = list(store.conn.execute("SELECT * FROM ingest_runs WHERE status='ok' ORDER BY id"))
    last = {r['source']:r for r in runs}
    dates = [r['finished_at'] for r in runs if r['source'] in ('clinicaltrials','pubmed','europepmc')]
    for table in ('articles','trials'):
        value = store.conn.execute(f'SELECT MAX(updated_at) FROM {table}').fetchone()[0]
        if value:
            dates.append(value)
    stamp = utc(build_time or max(filter(None,dates),default='1970-01-01T00:00:00Z'))
    curated = analysis.load_curated()
    by_curated = analysis.curated_index(curated)
    scores = analysis.rank_mechanisms(store,today=datetime.fromisoformat(stamp.replace('Z','+00:00')).date(),curated=curated)
    if mechanism_ids is not None:
        scores = [s for s in scores if s.mechanism.id in mechanism_ids]
    allowed = {s.mechanism.id for s in scores}
    # Hand-checked studies count toward their one mechanism only (analysis.restrict_tags).
    atags = {k:sorted(set(v)&allowed) for k,v in analysis.restrict_tags(store.all_tags('article'),curated).items()}
    ttags = {k:sorted(set(v)&allowed) for k,v in analysis.restrict_tags(store.all_tags('trial'),curated).items()}
    by_article = {a.uid:a for a in articles}
    # The existing ranking is reused, with deterministic ID ties.
    ranking = sorted(analysis.rank_open_trials(store,scores),key=lambda r:(-r['score'],r['trial'].nct_id))
    ranks = {r['trial'].nct_id:dict(position=i,score=r['score'],target=r['target']) for i,r in enumerate(ranking,1)}
    files = {}
    grades = []
    by_trial = {t.nct_id:t for t in trials}
    for score in scores:
        mech = score.mechanism
        grades.append(dict(id=mech.id,name=mech.name,kind=mech.kind,description=nullable(mech.description),
            agents=list(mech.agents),grade=score.grade,modifier=score.grade_modifier,reason=score.grade_reason,results=score.results,
            trials=score.trial_counts,activity=dict(papers=score.n_articles,msa_c_papers=score.n_msa_c,
                                                 recent_3y=score.n_recent,prior_3y=score.n_prior)))
        studies = {c:{o:[] for o in analysis.OUTCOME_KEYS} for c in analysis.RESULT_CATEGORIES}
        relevant = [a for a in articles if mech.id in atags.get(a.uid,[])]
        for a in relevant:
            if a.uid in by_curated:
                continue                                  # listed from its curated entry below
            category,outcome = analysis.EVIDENCE_TO_CATEGORY.get(a.evidence),analysis.OUTCOME_COUNT.get(a.outcome)
            if category and outcome:
                studies[category][outcome].append(study(a))
        # Hand-checked results first (counted ones before those of 20 people or fewer), in curated-file order.
        for outcome in analysis.OUTCOME_KEYS:
            checked = [curated_study(c,next((by_article[i] for i in c.ids if i in by_article),None))
                       for c in sorted(score.curated,key=lambda c:not c.counts) if c.outcome==outcome]
            studies['human'][outcome] = checked + studies['human'][outcome]
        groups = {g:sorted(ids) for g,ids in score.trial_groups.items()}
        groups['stopped'] = [dict(id=n,why_stopped=nullable(by_trial[n].why_stopped),
            counts_as_failure=mechanisms.trial_stopped_for_failure(by_trial[n].status,by_trial[n].why_stopped)) for n in groups['stopped']]
        files[f'mechanisms/{mech.id}.json'] = dict(schema_version=1,id=mech.id,studies=studies,trials=groups,
                                               msa_c_recent=[curated_study(by_curated[a.uid],a) if a.uid in by_curated else study(a)
                                                             for a in relevant if a.subtype=='msa_c'][:8])
    files['grades.json'] = dict(schema_version=1,meanings=analysis.GRADE_MEANINGS,definitions=DEFINITIONS,mechanisms=grades)
    compact = []
    old_trials = {t['id']:t for t in previous.get('trials',[])}
    for t in trials:
        old = old_trials.get(t.nct_id)
        changed = old.get('status_changed') if old else None
        if t.nct_id in history:
            h = history[t.nct_id]
            changed = {'from':h['from_'],'to':h['to'],'date':h['date']}
        if old and old['status'] != t.status:
            if not (changed and changed['from']==old['status'] and changed['to']==t.status):
                changed = {'from':old['status'],'to':t.status,'date':stamp[:10]}
        mids = ttags.get(t.nct_id,[])
        compact.append(dict(id=t.nct_id,title=nullable(t.title),status=t.status,status_label=t.status_label,
            open=t.is_open,active=t.is_active,phase=t.phase_label,max_phase=t.max_phase,type=nullable(t.study_type),
            sponsor=nullable(t.sponsor),fit=t.subtype_fit,mechanisms=mids,min_age=analysis.parse_age_years(t.min_age),
            max_age=analysis.parse_age_years(t.max_age),sex=nullable(t.sex),
            sites=[[loc.get('lat'),loc.get('lon'),nullable(loc.get('city')),nullable(loc.get('country')),
                    analysis._site_is_open(t,loc)] for loc in t.locations],
            last_update=day(t.last_update),status_changed=changed,rank=ranks.get(t.nct_id)))
        files[f'trials/{t.nct_id}.json'] = detail(t,mids)
    files['trials.json'] = dict(schema_version=1,trials=compact)
    library = defaultdict(list)
    abstracts = {f'{i:02x}':{} for i in range(256)}
    for a in articles:
        s = curated_study(by_curated[a.uid],a) if a.uid in by_curated else study(a)
        library[library_shard(a.year)].append([s[k] for k in LIB_COLUMNS[:5]] + [s['evidence'],s['outcome'],s['subtype'],
            s['preprint'],s['solid'],atags.get(a.uid,[]),s['url'],nullable(' '.join(sorted(set(a.keywords+a.mesh_terms))))])
        abstracts[hashlib.sha1(a.uid.encode()).hexdigest()[:2]][a.uid] = nullable(a.abstract)
    index = []
    for name,rows in sorted(library.items(),key=lambda pair: (pair[0]=='before-1990',-int(pair[0].split('-')[0]) if pair[0]!='before-1990' else 0)):
        content = dict(schema_version=1,rows=rows)
        files[f'library/{name}.json'] = content
        index.append(dict(name=name,rows=len(rows),sha256=hashlib.sha256(canonical(content)).hexdigest()))
    files['library/index.json'] = dict(schema_version=1,columns=LIB_COLUMNS,shards=index)
    for key,values in abstracts.items():
        files[f'abstracts/{key}.json'] = dict(schema_version=1,abstracts=values)
    city_groups = {chr(i):[] for i in range(ord('a'),ord('z')+1)}
    city_groups['other'] = []
    for name,city in geo._load(str(cities_path or config.CITIES_PATH)):
        key = name[:1] if name[:1] in city_groups and name[:1] else 'other'
        city_groups[key].append([city.name,nullable(city.region),nullable(city.country),city.lat,city.lon])
    for key,rows in city_groups.items():
        files[f'cities/{key}.json'] = dict(schema_version=1,columns=['name','region','country','lat','lon'],rows=rows)
    sources = {}
    for source in ('clinicaltrials','pubmed','europepmc'):
        r = last.get(source)
        sources[source] = dict(refreshed_at=utc(r['finished_at']) if r else None,
            records=len(trials) if source=='clinicaltrials' else sum(source in a.sources for a in articles))
    sources['clinicaltrials'].update(data_timestamp=state.get('trials_timestamp'),
        checked_at=utc(last['clinicaltrials-version']['finished_at']) if 'clinicaltrials-version' in last else sources['clinicaltrials']['refreshed_at'])
    meta = dict(schema_version=1,build_id=stamp,generated_at=stamp,sources=sources,counts=store.stats(),files={},
        disclaimer=config.DISCLAIMER,data_as_of={k:v['refreshed_at'] for k,v in sources.items()},
        push_public_key=os.environ.get('VAPID_PUBLIC_KEY') or None)
    return files,meta


def export_static(db, out, *, build_time=None, cities_path=None, heartbeat=False):
    """Stage and validate a complete export before replacing generated files; meta goes last."""
    from ..maintenance import maintenance_lock
    from .validate import validate_export
    out = Path(out).resolve()
    with maintenance_lock(db):
        previous = _load(out/'trials.json',{})
        old_meta = _load(out/'meta.json',{})
        store = Store(db,readonly=True)
        try:
            files,meta = documents(store,previous,build_time=build_time,cities_path=cities_path)
        finally:
            store.close()
        # Public key stays stable during local builds without configured secrets.
        if not meta['push_public_key']:
            meta['push_public_key'] = old_meta.get('push_public_key')
        out.parent.mkdir(parents=True,exist_ok=True)
        pending = Path(tempfile.mkdtemp(prefix='.export-',dir=out.parent))
        try:
            for name,value in sorted(files.items()):
                raw = canonical(value)
                path = pending/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(raw)
                meta['files'][name] = dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            # Re-exporting identical state preserves build identity and byte equality.
            comparable = lambda value:{k:v for k,v in value.items() if k not in ('generated_at','build_id')}
            heartbeat_due = heartbeat and old_meta.get('generated_at','')[:7] != meta['generated_at'][:7]
            if old_meta and comparable(old_meta)==comparable(meta) and not heartbeat_due:
                meta['generated_at'],meta['build_id'] = old_meta['generated_at'],old_meta['build_id']
            (pending/'meta.json').write_bytes(canonical(meta))
            validate_export(pending)
            out.mkdir(parents=True,exist_ok=True)
            for name in sorted(files)+['meta.json']:
                target = out/name
                target.parent.mkdir(parents=True,exist_ok=True)
                raw = (pending/name).read_bytes()
                if not target.exists() or target.read_bytes()!=raw:
                    (pending/name).replace(target)
            # Only remove files declared by the old exporter, never arbitrary user files.
            for name in set(old_meta.get('files',{}))-set(files):
                path = (out/name).resolve()
                if path.is_relative_to(out) and path.is_file():
                    path.unlink()
            return meta
        finally:
            shutil.rmtree(pending)
