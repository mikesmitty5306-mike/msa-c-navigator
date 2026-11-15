"""Offline maintenance, atomicity, online backup and notification regressions."""
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from msac import maintenance as m
from msac import push
from msac.models import Trial, Article
from msac.storage import Store
from msac.userdata import UserStore


def trial(**kwargs):
    return Trial(nct_id='NCT1', title='MSA trial', status='RECRUITING', subtype_fit='any_msa', **kwargs)


def test_events():
    t = trial()
    assert len(push.detect_events({}, {'NCT1': t}, 'v1')) == 1
    assert not push.detect_events({}, {'NCT1': replace(t, subtype_fit='excludes_msa_c')}, 'v1')
    assert not push.detect_events({}, {'NCT1': replace(t, status='COMPLETED')}, 'v1')
    changed = replace(t, status='TERMINATED')
    event = push.detect_events({'NCT1': t}, {'NCT1': changed}, 'v2')[0]
    assert event['payload']['url'] == '#/trials/NCT1'
    assert not push.detect_events({'NCT1': t}, {'NCT1': t}, 'v2')
    assert push.detect_events({'NCT1': t}, {'NCT1': changed}, 'v2')[0]['id'] == event['id']
    excluded = replace(t, subtype_fit='excludes_msa_c', status='COMPLETED')
    assert not push.detect_events({'NCT1': excluded}, {'NCT1': replace(excluded, status='UNKNOWN')}, 'v2')


def queued(n=1):
    s = Store(':memory:')
    m.initialize(s)
    for i in range(n):
        s.conn.execute('INSERT INTO push_events(id,payload) VALUES (?,?)',
                       (str(i), json.dumps({'title': 'Update', 'body': str(i), 'url': '#/trials/NCT1'})))
    s.commit()
    return s


ENV = {'PUSH_SUBSCRIPTION': json.dumps({'endpoint': 'https://example.test', 'keys': {'auth': 'x', 'p256dh': 'y'}}),
       'VAPID_PRIVATE_KEY': 'secret', 'VAPID_PUBLIC_KEY': 'public', 'VAPID_SUBJECT': 'mailto:test@example.org'}


def test_push_skip_dedupe_and_limit():
    s = queued(9)
    calls = []
    sender = lambda **kw: calls.append(kw)
    assert push.send_pending(s, environ={}, sender=sender) == 0
    assert not calls
    assert push.send_pending(s, environ=ENV, sender=sender) == 5
    assert '5 additional' in json.loads(calls[-1]['data'])['body']
    assert push.send_pending(s, environ=ENV, sender=sender) == 0
    assert len(calls) == 5
    assert all(set(json.loads(c['data'])) == {'title', 'body', 'url'} for c in calls)


def test_expired_warning(tmp_path, monkeypatch):
    summary = tmp_path / 'summary'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    class Expired(Exception):
        response = type('Response', (), {'status_code': 410})()
    def send(**kw):
        raise Expired('private endpoint must not be logged')
    s = queued()
    assert push.send_pending(s, environ=ENV, sender=send) == 0
    assert 'expired' in summary.read_text()
    assert 'private endpoint' not in summary.read_text()
    assert s.conn.execute('SELECT state FROM push_events').fetchone()[0] == 'failed'


def test_backup_live_wal_and_retention(tmp_path):
    db, user = tmp_path / 'msac.sqlite3', tmp_path / 'user.sqlite3'
    s, u = Store(db), UserStore(user)
    u.set_setting('important', 'notes')
    s.put_article(Article(uid='pmid:1', title='MSA'), {})
    s.commit()
    folder = tmp_path / 'backups'
    for i in range(12):
        target = m.backup(db, user, folder)
    assert len(list(folder.glob('backup-*'))) == 10
    c = sqlite3.connect(target / 'user.sqlite3')
    assert json.loads(c.execute("SELECT value FROM settings WHERE key='important'").fetchone()[0]) == 'notes'
    c.close()
    c = sqlite3.connect(target / 'msac.sqlite3')
    assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert c.execute('SELECT count(*) FROM articles').fetchone()[0] == 1
    c.close()
    s.close()
    u.close()


def test_refresh_timestamp_and_failure_atomicity(tmp_path, monkeypatch):
    db, user = tmp_path / 'db.sqlite3', tmp_path / 'user.sqlite3'
    monkeypatch.setattr(m.http, 'get_json', lambda *a: {'dataTimestamp': 'v1'})
    monkeypatch.setattr(m.clinicaltrials, 'search', lambda: iter([replace(trial(), conditions=['Multiple System Atrophy'])]))
    m.refresh(db, user, 'trials')
    monkeypatch.setattr(m.clinicaltrials, 'search', lambda: pytest.fail('unchanged version should not fetch'))
    m.refresh(db, user, 'trials')
    s = Store(db)
    assert s.recent_runs()[0]['status'] == 'unchanged'
    assert s.get_trial('NCT1') is not None
    s.close()
    monkeypatch.setattr(m.http, 'get_json', lambda *a: {'dataTimestamp': 'v2'})
    def broken():
        yield replace(trial(), status='COMPLETED')
        raise RuntimeError('network interrupted')
    monkeypatch.setattr(m.clinicaltrials, 'search', broken)
    with pytest.raises(RuntimeError):
        m.refresh(db, user, 'trials')
    s = Store(db)
    assert s.get_trial('NCT1').status == 'RECRUITING'
    assert m.state(s, 'trials_timestamp') == 'v1'
    assert s.recent_runs()[0]['status'] == 'error'
    s.close()


def test_readonly_store(tmp_path):
    db = tmp_path / 'research.sqlite3'
    Store(db).close()
    s = Store(db, readonly=True)
    assert s.stats()['trials'] == 0
    with pytest.raises(sqlite3.OperationalError):
        s.conn.execute('DELETE FROM trials')
    s.close()


def test_lock_serializes_maintenance_without_blocking_readers(tmp_path):
    db = tmp_path / 'research.sqlite3'
    Store(db).close()
    with m.maintenance_lock(db):
        other = sqlite3.connect(str(db.resolve()) + '.maintenance-lock', timeout=0.01)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute('BEGIN IMMEDIATE')
            reader = Store(db, readonly=True)
            assert reader.stats()['trials'] == 0
            reader.close()
        finally:
            other.close()
    with m.maintenance_lock(db):
        pass  # normal exit releases the lock for the next run


def test_papers_incremental_daily_guard_and_meta(tmp_path, monkeypatch):
    from datetime import date, timedelta
    db, user = tmp_path / 'db.sqlite3', tmp_path / 'user.sqlite3'
    meta = tmp_path / 'meta.json'
    meta.write_text('{"schema_version": 1}')
    monkeypatch.setenv('VAPID_PUBLIC_KEY', 'public-only')
    calls = []
    def search(**kwargs):
        calls.append(kwargs['since'])
        return iter([Article(uid='pmid:1', title='Multiple system atrophy')])
    monkeypatch.setattr(m.pubmed, 'search', search)
    monkeypatch.setattr(m.europepmc, 'search', search)
    m.refresh(db, user, 'papers', backup_dir=tmp_path / 'backups', meta_path=meta, daily_day='day1')
    assert calls == [None, None]
    m.refresh(db, user, 'papers', backup_dir=tmp_path / 'backups', meta_path=meta, daily_day='day1')
    assert len(calls) == 2
    m.refresh(db, user, 'papers', backup_dir=tmp_path / 'backups', meta_path=meta, daily_day='day2')
    assert calls[2:] == [date.today() - timedelta(days=7)] * 2
    data = json.loads(meta.read_text())
    assert data['schema_version'] == 1 and data['push_public_key'] == 'public-only'
    assert set(data['data_as_of']) == {'pubmed', 'europepmc'}
    assert 'PRIVATE' not in meta.read_text()


def test_failed_paper_source_preserves_checkpoint_and_still_backs_up(tmp_path, monkeypatch):
    db, user = tmp_path / 'db.sqlite3', tmp_path / 'user.sqlite3'
    def fail(**kwargs):
        raise RuntimeError('offline')
    monkeypatch.setattr(m.pubmed, 'search', fail)
    monkeypatch.setattr(m.europepmc, 'search', lambda **kw: iter([]))
    with pytest.raises(RuntimeError, match='Literature refresh failed'):
        m.refresh(db, user, 'papers', backup_dir=tmp_path / 'backups')
    s = Store(db)
    assert m.state(s, 'pubmed_since') is None
    assert m.state(s, 'europepmc_since') is not None
    assert s.recent_runs()[0]['source'] == 'backup'
    assert s.recent_runs()[0]['status'] == 'ok'
    s.close()
    assert len(list((tmp_path / 'backups').glob('backup-*'))) == 1


def test_generated_keys_are_compatible_and_exclusive(tmp_path, capsys):
    from py_vapid import Vapid
    from cryptography.hazmat.primitives import serialization
    import base64
    path = tmp_path / 'vapid.private.json'
    push.generate_keys(path)
    keys = json.loads(path.read_text())
    vapid = Vapid.from_string(keys['VAPID_PRIVATE_KEY'])
    public = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    assert base64.urlsafe_b64encode(public).decode().rstrip('=') == keys['VAPID_PUBLIC_KEY']
    assert keys['VAPID_PRIVATE_KEY'] not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        push.generate_keys(path)


def test_refresh_queues_once_across_revisions(tmp_path, monkeypatch):
    db, user = tmp_path / 'db.sqlite3', tmp_path / 'user.sqlite3'
    monkeypatch.delenv('PUSH_SUBSCRIPTION', raising=False)
    revision = ['v1']
    current = [replace(trial(), conditions=['Multiple System Atrophy'])]
    monkeypatch.setattr(m.http, 'get_json', lambda *a: {'dataTimestamp': revision[0]})
    monkeypatch.setattr(m.clinicaltrials, 'search', lambda: iter(current))
    m.refresh(db, user, 'trials')
    current[0] = replace(current[0], status='COMPLETED')
    revision[0] = 'v2'
    m.refresh(db, user, 'trials')
    m.refresh(db, user, 'trials')
    s = Store(db)
    assert s.conn.execute('SELECT count(*) FROM push_events').fetchone()[0] == 1
    assert s.conn.execute('SELECT state FROM push_events').fetchone()[0] == 'pending'
    s.close()


def test_backup_missing_research_does_not_create_empty_db(tmp_path):
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(FileNotFoundError):
        m.backup(missing, tmp_path / 'user.sqlite3', tmp_path / 'backups')
    assert not missing.exists()


def test_workflow_installer_refuses_foreign_content(tmp_path):
    from tools.install_refresh_workflow import main
    target = tmp_path / '.github' / 'workflows' / 'refresh.yml'
    main(['--repo-root', str(tmp_path)])
    assert 'refresh-scheduled' in target.read_text()
    main(['--repo-root', str(tmp_path), '--remove'])
    assert not target.exists()
    target.write_text('foreign workflow')
    with pytest.raises(SystemExit, match='Refusing'):
        main(['--repo-root', str(tmp_path)])
