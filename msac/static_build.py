"""GitHub Pages build orchestration, reusing the serialized refresh and backup commands."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from . import http, maintenance, pipeline
from .export import export_static, utc
from .push import send_pending
from .storage import Store


def check_trials(out, *, return_timestamp=False):
    stamp = http.get_json('https://clinicaltrials.gov/api/v2/version').get('dataTimestamp')
    if not isinstance(stamp,str) or not stamp.strip():
        raise ValueError('ClinicalTrials.gov version response has no dataTimestamp')
    path = Path(out)/'meta.json'
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    changed = stamp != old.get('sources',{}).get('clinicaltrials',{}).get('data_timestamp')
    print('Trials: changed' if changed else 'Trials: unchanged')
    return (changed,stamp) if return_timestamp else changed


def scheduled(db, user_db, *, out='public-data', backup_dir=None, force=False, now=None):
    # Fail closed, including when an API call or the entire refresh step times out.
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as result:
            result.write('deploy=false\n')
    now = now or datetime.now(timezone.utc)
    today,month = now.date().isoformat(),now.strftime('%Y-%m')
    out = Path(out)
    # This outer lock serializes orchestration; inner refresh/backup locks still
    # coordinate safely with independently invoked maintenance commands.
    with maintenance.maintenance_lock(str(db)+'.schedule'):
        meta_path = out/'meta.json'
        old_meta_raw = meta_path.read_bytes() if meta_path.exists() else None
        old = json.loads(old_meta_raw) if old_meta_raw else {}
        changed,stamp = check_trials(out,return_timestamp=True)
        with maintenance.maintenance_lock(db):
            store = Store(db)
            try:
                maintenance.initialize(store)
                missing_papers = any(maintenance.state(store,s+'_since') is None for s in ('pubmed','europepmc'))
                daily_due = missing_papers or (now.hour>=6 and maintenance.state(store,'daily_day')!=today)
                cache_trials_missing = maintenance.state(store,'trials_timestamp') is None
            finally:
                store.close()
        # Always record the hourly check. Unchanged timestamps avoid the studies request.
        maintenance.refresh(db,user_db,'trials',public_dir=out,notify=False,checked_timestamp=stamp)
        if daily_due:
            maintenance.refresh(db,user_db,'papers',backup_dir=backup_dir,daily_day=today,notify=False)
        with maintenance.maintenance_lock(db):
            store = Store(db)
            try:
                latest = {r['source']:utc(r['finished_at']) for r in store.conn.execute(
                    "SELECT source, MAX(finished_at) AS finished_at FROM ingest_runs WHERE status='ok' "
                    "AND source IN ('clinicaltrials','pubmed','europepmc') GROUP BY source")}
                unpublished = any(old.get('sources',{}).get(s,{}).get('refreshed_at')!=t for s,t in latest.items())
                public_key_changed = bool(os.environ.get('VAPID_PUBLIC_KEY')) and os.environ['VAPID_PUBLIC_KEY']!=old.get('push_public_key')
                publish = bool(force or changed or cache_trials_missing or daily_due or unpublished
                               or public_key_changed or old.get('generated_at','')[:7]!=month)
                if publish:
                    counts = store.stats()
                    if counts['trials']==0 and old.get('counts',{}).get('trials',0)>0:
                        raise RuntimeError('Refresh produced no trials; retaining the last published export')
                    if counts['articles']==0 and old.get('counts',{}).get('articles',0)>0:
                        raise RuntimeError('Refresh produced no papers; retaining the last published export')
                    run = store.start_run('recompute')
                    try:
                        articles,trials = pipeline.retag(store)
                        store.finish_run(run,articles+trials,'ok')
                    except BaseException as exc:
                        store.conn.rollback()
                        store.finish_run(run,0,'error',str(exc))
                        raise
            finally:
                store.close()
        if publish:
            export_static(db,out,build_time=now.isoformat(),heartbeat=True)
        # Sending follows a validated export, never a failed or partially published build.
        with maintenance.maintenance_lock(db):
            store = Store(db)
            try:
                send_pending(store)
            finally:
                store.close()
        deployed_change = force or (meta_path.exists() and meta_path.read_bytes()!=old_meta_raw)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as result:
                result.write('deploy='+str(bool(deployed_change)).lower()+'\n')
        print('Build ready for deployment.' if deployed_change else 'No public data changes; no commit or deployment needed.')
        return bool(deployed_change)
