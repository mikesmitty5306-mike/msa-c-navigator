"""Focused, offline deployment safety checks. No browser or live Web Push calls."""
from datetime import datetime,timezone
import json
from pathlib import Path
import re

import pytest

from msac import maintenance as m,push,static_build
from msac.cli import main as cli_main
from msac.export import export_static
from msac.http import FetchError
from msac.models import Article,Trial
from msac.storage import Store
from tools.make_vapid_keys import main as print_keys
from tools.prepare_deployment import prepare

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026,11,14,6,17,tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolate_actions_files(monkeypatch):
    # Mock delivery must not claim a real notification in the workflow summary.
    monkeypatch.delenv('GITHUB_STEP_SUMMARY',raising=False)
    monkeypatch.delenv('GITHUB_OUTPUT',raising=False)


@pytest.fixture
def state(tmp_path,monkeypatch):
    cities = tmp_path/'cities.txt'
    cities.write_text('Boston|MA|United States|42|-71\n')
    monkeypatch.setattr('msac.config.CITIES_PATH',cities)
    db,user,out = tmp_path/'research.sqlite3',tmp_path/'user.sqlite3',tmp_path/'public-data'
    s = Store(db)
    m.initialize(s)
    s.put_article(Article(uid='pmid:1',title='MSA research',year=2026,sources=['pubmed']),{})
    s.put_trial(Trial(nct_id='NCT00000001',title='MSA trial',conditions=['Multiple System Atrophy'],status='RECRUITING'),{})
    for key,value in [('trials_timestamp','v1'),('pubmed_since','2026-11-13'),
                      ('europepmc_since','2026-11-13'),('daily_day','2026-11-13')]:
        m.set_state(s,key,value)
    s.commit()
    s.close()
    export_static(db,out,build_time=NOW.isoformat())
    return db,user,out


@pytest.mark.parametrize('source',['version','clinicaltrials','pubmed','europepmc'])
@pytest.mark.parametrize('failure',[FetchError,TimeoutError])
def test_failed_refresh_keeps_last_export_and_blocks_release(state,monkeypatch,tmp_path,source,failure):
    db,user,out = state
    before = {p.relative_to(out):p.read_bytes() for p in out.rglob('*.json')}
    outputs = tmp_path/'outputs'
    monkeypatch.setenv('GITHUB_OUTPUT',str(outputs))
    def fail(*args,**kwargs):
        raise failure('simulated source failure')
    monkeypatch.setattr(m.http,'get_json',fail if source=='version' else lambda *a:{'dataTimestamp':'v2' if source=='clinicaltrials' else 'v1'})
    monkeypatch.setattr(m.clinicaltrials,'search',fail if source=='clinicaltrials' else lambda:iter([]))
    monkeypatch.setattr(m.pubmed,'search',fail if source=='pubmed' else lambda **kw:iter([]))
    monkeypatch.setattr(m.europepmc,'search',fail if source=='europepmc' else lambda **kw:iter([]))
    monkeypatch.setattr(static_build,'send_pending',lambda *a:pytest.fail('No push during a failed refresh'))
    monkeypatch.setattr(static_build,'export_static',lambda *a,**kw:pytest.fail('No publication during a failed refresh'))
    with pytest.raises((FetchError,TimeoutError,RuntimeError)):
        static_build.scheduled(db,user,out=out,backup_dir=tmp_path/'backups',now=NOW)
    assert before=={p.relative_to(out):p.read_bytes() for p in out.rglob('*.json')}
    assert outputs.read_text().splitlines()==['deploy=false']


def test_empty_rebuild_cannot_replace_nonempty_export(state,monkeypatch,tmp_path):
    db,user,out = state
    before = (out/'meta.json').read_bytes()
    monkeypatch.setattr(m.http,'get_json',lambda *a:{'dataTimestamp':'v2'})
    def empty(*args,**kwargs):
        s = Store(db)
        s.conn.execute('DELETE FROM trials')
        s.commit()
        s.close()
    monkeypatch.setattr(m,'refresh',empty)
    with pytest.raises(RuntimeError,match='no trials'):
        static_build.scheduled(db,user,out=out,now=NOW)
    assert (out/'meta.json').read_bytes()==before


def test_first_push_prepares_committed_export_without_apis(state,monkeypatch):
    db,user,out = state
    monkeypatch.setattr(m.http,'get_json',lambda *a:pytest.fail('Initial deployment must not contact research APIs'))
    pair = push.vapid_key_pair()
    original = json.loads((out/'meta.json').read_text())
    prepare(out,pair['VAPID_PUBLIC_KEY'])
    updated = json.loads((out/'meta.json').read_text())
    assert updated['push_public_key']==pair['VAPID_PUBLIC_KEY']
    assert updated['files']==original['files']
    assert pair['VAPID_PRIVATE_KEY'] not in (out/'meta.json').read_text()
    assert not list(out.glob('*.tmp'))


def test_prepare_rejects_private_key_and_invalid_export(state):
    db,user,out = state
    before = (out/'meta.json').read_bytes()
    with pytest.raises(ValueError,match='never a private key'):
        prepare(out,push.vapid_key_pair()['VAPID_PRIVATE_KEY'])
    assert (out/'meta.json').read_bytes()==before
    (out/'trials.json').write_text('{}')
    with pytest.raises(ValueError):
        prepare(out)


def test_prepare_rejects_schema_valid_but_empty_export(state,tmp_path):
    empty_db = tmp_path/'empty.sqlite3'
    Store(empty_db).close()
    folder = tmp_path/'empty-export'
    export_static(empty_db,folder,build_time=NOW.isoformat())
    with pytest.raises(ValueError,match='empty research export'):
        prepare(folder)


def test_key_helper_and_cli_print_without_writing_files(tmp_path,monkeypatch,capsys):
    monkeypatch.chdir(tmp_path)
    print_keys()
    first = json.loads(capsys.readouterr().out)
    assert set(first)=={'VAPID_PRIVATE_KEY','VAPID_PUBLIC_KEY'}
    cli_main(['generate-vapid'])
    second = json.loads(capsys.readouterr().out)
    assert second['VAPID_PUBLIC_KEY']!=first['VAPID_PUBLIC_KEY']
    assert not list(tmp_path.iterdir())


def test_private_key_file_output_is_forbidden_inside_repositories(tmp_path):
    repo = tmp_path/'repo'
    (repo/'.git').mkdir(parents=True)
    target = repo/'keys.private.json'
    with pytest.raises(ValueError,match='must not be written inside a repository'):
        push.generate_keys(target)
    assert not target.exists()


def test_explicit_test_push_sends_exactly_one_and_does_not_touch_ledger():
    env = dict(PUSH_SUBSCRIPTION=json.dumps({'endpoint':'https://example.test/phone','keys':{'p256dh':'public','auth':'auth'}}),
               VAPID_PRIVATE_KEY='private',VAPID_PUBLIC_KEY='public',VAPID_SUBJECT='mailto:helper@example.org',
               PUSH_SUBSCRIPTIONS='ignored legacy list')
    calls = []
    assert push.send_test_push(environ=env,sender=lambda **kw:calls.append(kw))==1
    assert len(calls)==1
    assert json.loads(calls[0]['data'])['url']=='#/trials'
    assert calls[0]['subscription_info']['endpoint']=='https://example.test/phone'


def test_explicit_test_push_requires_standard_secret_and_redacts_failures():
    with pytest.raises(ValueError,match='PUSH_SUBSCRIPTION'):
        push.send_test_push(environ={})
    env = dict(PUSH_SUBSCRIPTION=json.dumps({'endpoint':'https://example.test/private-endpoint','keys':{'auth':'private-auth'}}),
               VAPID_PRIVATE_KEY='private-key',VAPID_PUBLIC_KEY='public',VAPID_SUBJECT='mailto:test@example.org')
    def fail(**kw):
        raise RuntimeError('private-endpoint private-auth private-key')
    with pytest.raises(RuntimeError) as error:
        push.send_test_push(environ=env,sender=fail)
    assert all(secret not in str(error.value) for secret in ['private-endpoint','private-auth','private-key'])


def test_test_push_cli_requires_explicit_command(monkeypatch):
    calls = []
    monkeypatch.setattr(push,'send_test_push',lambda:calls.append('test'))
    cli_main(['test-push'])
    assert calls==['test']


def test_workflow_has_safe_first_push_release_and_no_browsers():
    workflow = (ROOT/'.github/workflows/refresh.yml').read_text()
    assert 'push:\n    branches: [main]' in workflow
    assert 'send_test_push:' in workflow and 'default: false' in workflow
    assert "github.event_name == 'schedule' || inputs.refresh_data == true" in workflow
    assert "needs.build.result == 'success' && needs.build.outputs.deploy == 'true'" in workflow
    assert 'cancel-in-progress: false' in workflow
    assert 'pages: write' in workflow and 'id-token: write' in workflow
    assert 'actions/upload-pages-artifact@' in workflow and 'actions/deploy-pages@' in workflow
    assert workflow.index('python tools/prepare_deployment.py')<workflow.index('python tools/assemble_site.py')<workflow.index('actions/upload-pages-artifact@')
    commands = re.findall(r'run: (python -m pytest[^\n]*)',workflow)
    assert commands==['python -m pytest -q tests/test_deployment.py tests/test_push_static.py']
    assert 'inputs.send_test_push == true' in workflow
    assert "needs.deploy.result == 'success'" in workflow
    assert 'python -m msac test-push' in workflow
    assert all(re.search(r'@[a-f0-9]{40}$',action) for action in re.findall(r'uses:\s*(\S+)',workflow))


def test_workflow_uses_least_privilege_per_job():
    workflow = (ROOT/'.github/workflows/refresh.yml').read_text()
    assert re.search(r'^permissions: \{\}$',workflow,re.M)
    blocks = re.split(r'^  ([a-z-]+):\n',workflow.split('jobs:\n',1)[1],flags=re.M)
    jobs = dict(zip(blocks[1::2],blocks[2::2]))
    expected = {'build':{'contents':'write'},'deploy':{'pages':'write','id-token':'write'},'test-push':{'contents':'read'}}
    assert set(jobs)==set(expected)
    for name,permissions in expected.items():
        block = re.search(r'^    permissions:\n((?:      .*\n)+)',jobs[name],re.M).group(1)
        actual = dict(re.findall(r'^      ([a-z-]+): (read|write|none)$',block,re.M))
        assert actual==permissions
    assert workflow.count('contents: write')==1
    assert 'git commit --only' in jobs['build']


def test_setup_keeps_restricted_default_and_recommends_https_subject():
    setup = (ROOT/'SETUP.md').read_text()
    assert 'Read repository contents and packages' in setup
    assert 'Read and write permissions' not in setup
    assert 'https://<user>.github.io/<repo>' in setup
    assert 'no personal email is needed for push' in setup
    assert 'does\nnot trigger another `push` workflow run' in setup
