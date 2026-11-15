"""Portable, serialized maintenance. Network fetching never holds a research write lock."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from . import config, http, pipeline
from .sources import clinicaltrials, pubmed, europepmc
from .storage import Store
from .userdata import UserStore


@contextmanager
def maintenance_lock(db):
    """OS releases SQLite's lock even after kill/crash; shared by all maintenance commands."""
    path = Path(db).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path) + '.maintenance-lock', timeout=3600)
    try:
        conn.execute('BEGIN IMMEDIATE')
        yield
    finally:
        conn.rollback()
        conn.close()


def initialize(store):
    store.conn.executescript('''
        CREATE TABLE IF NOT EXISTS maintenance_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS push_events (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS trial_status_history (
            nct_id TEXT PRIMARY KEY, previous_status TEXT NOT NULL, status TEXT NOT NULL, changed_date TEXT NOT NULL);
    ''')
    # Holding the exclusive maintenance lock proves no older run is still alive.
    with store.conn:
        store.conn.execute("UPDATE ingest_runs SET status='error', finished_at=?, "
                           "message='Interrupted before completion; retrying is safe' WHERE status='running'",
                           (datetime.now(timezone.utc).isoformat(timespec='seconds'),))


def state(store, key):
    row = store.conn.execute('SELECT value FROM maintenance_state WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def set_state(store, key, value):
    store.conn.execute('INSERT INTO maintenance_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, value))


def _refresh_source(store, source, *, timestamp=None, previous_trials=None):
    run = store.start_run(source)
    started_day = date.today()
    count = 0
    try:
        if source == 'clinicaltrials':
            if not timestamp or not isinstance(timestamp, str):
                raise ValueError('ClinicalTrials.gov returned no dataTimestamp')
            if state(store, 'trials_timestamp') == timestamp:
                store.finish_run(run, 0, 'unchanged', timestamp)
                return
            items = list(clinicaltrials.search())
        else:
            previous = state(store, source + '_since')
            since = date.fromisoformat(previous) - timedelta(days=7) if previous else None
            items = list({'pubmed': pubmed, 'europepmc': europepmc}[source].search(since=since))
        # All network requests completed. Apply one source atomically, with WAL readers continuing.
        with store.conn:
            if source == 'clinicaltrials':
                from .push import detect_events
                before = {t.nct_id: t for t in store.iter_trials()}
                if previous_trials:
                    before.update(previous_trials)
                for item in items:
                    count += pipeline.save_trial(store, item)
                after = {t.nct_id: t for t in store.iter_trials()}
                for nct_id,trial in after.items():
                    old = before.get(nct_id)
                    if old and old.status != trial.status:
                        store.conn.execute('INSERT INTO trial_status_history VALUES (?,?,?,?) '
                            'ON CONFLICT(nct_id) DO UPDATE SET previous_status=excluded.previous_status, '
                            'status=excluded.status, changed_date=excluded.changed_date',
                            (nct_id,old.status,trial.status,datetime.now(timezone.utc).date().isoformat()))
                # First import establishes a baseline rather than notifying about historical trials.
                if state(store, 'trials_timestamp') is not None or previous_trials:
                    for event in detect_events(before, after, timestamp):
                        store.conn.execute('INSERT OR IGNORE INTO push_events(id,payload) VALUES (?,?)',
                                           (event['id'], json.dumps(event['payload'])))
                set_state(store, 'trials_timestamp', timestamp)
            else:
                for item in items:
                    pipeline.save_article(store, item)
                    count += 1
                set_state(store, source + '_since', started_day.isoformat())
        store.finish_run(run, count, 'ok', timestamp or '')
    except BaseException as exc:
        store.conn.rollback()
        store.finish_run(run, 0, 'error', f'{type(exc).__name__}: {exc}')
        raise


def _backup(db, user_db, folder, keep=10):
    """Online SQLite snapshots; publish only complete, validated sets, then rotate."""
    if keep < 1:
        raise ValueError('keep must be positive')
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix='.pending-', dir=folder))
    try:
        files = []
        # Personal data is copied first. Missing files are never silently created.
        for label, source in [('user.sqlite3', user_db), ('msac.sqlite3', db)]:
            source = Path(source).resolve()
            if not source.exists():
                if label == 'user.sqlite3':
                    continue  # static deployments have no server-side personal database
                raise FileNotFoundError(source)
            src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30)
            dst = sqlite3.connect(pending / label)
            try:
                src.backup(dst, pages=256, sleep=0.05)
                if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed: ' + label)
            finally:
                dst.close()
                src.close()
            files.append(label)
        (pending / 'manifest.json').write_text(json.dumps({'files': files, 'missing_user_database': 'user.sqlite3' not in files}), encoding='utf-8')
        target = folder / datetime.now(timezone.utc).strftime('backup-%Y%m%dT%H%M%S%fZ')
        pending.rename(target)
        for old in sorted(folder.glob('backup-*'), reverse=True)[keep:]:
            if old.is_dir() and (old / 'manifest.json').is_file():
                shutil.rmtree(old)
        return target
    finally:
        if pending.exists():
            shutil.rmtree(pending)


def backup(db, user_db, folder=None):
    with maintenance_lock(db):
        if not Path(db).is_file():
            raise FileNotFoundError(f'Research database does not exist: {db}')
        store = Store(db)
        try:
            run = store.start_run('backup')
            try:
                target = _backup(db, user_db, folder or config.APP_DIR / 'backups')
                store.finish_run(run, len(list(target.glob('*.sqlite3'))), 'ok', target.name)
                return target
            except BaseException as exc:
                store.finish_run(run, 0, 'error', str(exc))
                raise
        finally:
            store.close()


def write_meta(store, path):
    path = Path(path).resolve()
    if any(part.casefold() in ('public-data','_site','web') for part in path.parts):
        raise ValueError('Internal maintenance metadata cannot be written into public or site directories')
    meta = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if meta.get('schema_version')==1 and 'files' in meta:
        raise ValueError('Refusing to overwrite public export metadata with internal run diagnostics')
    path.parent.mkdir(parents=True, exist_ok=True)
    meta['refresh_runs'] = [dict(r) for r in store.recent_runs(100)]
    meta['data_as_of'] = {r['source']: r['finished_at'] for r in store.conn.execute(
        "SELECT source, MAX(finished_at) AS finished_at FROM ingest_runs "
        "WHERE status='ok' AND source IN ('clinicaltrials','pubmed','europepmc') GROUP BY source")}
    meta['trials_data_timestamp'] = state(store, 'trials_timestamp')
    if os.environ.get('VAPID_PUBLIC_KEY'):
        meta['push_public_key'] = os.environ['VAPID_PUBLIC_KEY']
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def refresh(db, user_db, kind, *, backup_dir=None, meta_path=None, daily_day=None, public_dir=None, notify=True,
            checked_timestamp=None):
    with maintenance_lock(db):
        store = Store(db)
        try:
            initialize(store)
            if daily_day and state(store, 'daily_day') == daily_day:
                return
            if kind == 'trials':
                check = store.start_run('clinicaltrials-version')
                try:
                    timestamp = checked_timestamp or http.get_json('https://clinicaltrials.gov/api/v2/version')['dataTimestamp']
                    store.finish_run(check, 0, 'ok', timestamp)
                except BaseException as exc:
                    store.finish_run(check, 0, 'error', str(exc))
                    raise
                previous = None
                if public_dir and (Path(public_dir)/'trials.json').exists():
                    from .models import Trial
                    previous = {t['id']:Trial(nct_id=t['id'],status=t['status'],title=t.get('title') or '',
                        subtype_fit=t['fit']) for t in json.loads((Path(public_dir)/'trials.json').read_text(encoding='utf-8'))['trials']}
                _refresh_source(store, 'clinicaltrials', timestamp=timestamp,previous_trials=previous)
                if Path(user_db).exists():
                    users = UserStore(user_db)
                    try:
                        users.sync_trial_statuses(store)
                    finally:
                        users.close()
            else:
                errors = []
                for source in ('pubmed', 'europepmc'):
                    try:
                        _refresh_source(store, source)
                    except Exception as exc:
                        errors.append(exc)
                run = store.start_run('backup')
                try:
                    target = _backup(db, user_db, backup_dir or config.APP_DIR / 'backups')
                    store.finish_run(run, len(list(target.glob('*.sqlite3'))), 'ok', target.name)
                except BaseException as exc:
                    store.finish_run(run, 0, 'error', str(exc))
                    raise
                if errors:
                    raise RuntimeError('Literature refresh failed; see ingest_runs') from errors[0]
                if daily_day:
                    with store.conn:
                        set_state(store, 'daily_day', daily_day)
            if notify:
                from .push import send_pending
                send_pending(store)
        finally:
            try:
                if meta_path:
                    write_meta(store, meta_path)
            finally:
                store.close()
