"""Contract exports, status history, scheduling and assembly without network access."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import pytest

from msac import maintenance as m, pipeline, static_build
from msac.cli import main
from msac.export import export_static, library_shard
from msac.export.validate import validate_export
from msac.models import Article, Trial
from msac.storage import Store
from tools.assemble_site import assemble

STAMP = '2026-11-14T06:17:00Z'


def read(folder,name):
    return json.loads((folder/name).read_text(encoding='utf-8'))


@pytest.fixture
def research(tmp_path,monkeypatch):
    db = tmp_path/'research.sqlite3'
    cities = tmp_path/'cities.txt'
    cities.write_text('Boston|MA|United States|42|-71\nSão Paulo|SP|Brazil|-23|-46\nSydney|NSW|Australia|-33|151\n東京||Japan|35|139\n',encoding='utf-8')
    monkeypatch.setattr('msac.config.CITIES_PATH',cities)
    store = Store(db)
    m.initialize(store)
    for i,year in enumerate([2026,2026,2014,1990,1989,None]):
        a = Article(uid=f'pmid:{i}',pmid=str(i),title='Multiple system atrophy study',year=year,
            abstract='Some abstract',evidence='clinical',outcome='safety',sources=['pubmed'],keywords=['MSA'])
        store.put_article(a,{'asyn_aggregation':['MSA']})
    t = Trial(nct_id='NCT00000001',title='MSA trial',status='RECRUITING',subtype_fit='any_msa',
        conditions=['Multiple System Atrophy'],study_type='INTERVENTIONAL',min_age='6 Months',
        completion_date='2027-05',locations=[dict(city='Boston',country='United States',lat=42,lon=-71)])
    store.put_trial(t,{'asyn_aggregation':['MSA']})
    m.set_state(store,'trials_timestamp','v1')
    store.commit()
    store.close()
    return db,tmp_path/'public-data',tmp_path/'user.sqlite3'


def test_deterministic_complete_export_and_semantics(research,monkeypatch):
    db,out,user = research
    monkeypatch.setenv('VAPID_PUBLIC_KEY','public-key')
    export_static(db,out,build_time=STAMP)
    before = {p.relative_to(out).as_posix():p.read_bytes() for p in out.rglob('*.json')}
    export_static(db,out,build_time=STAMP)
    assert before == {p.relative_to(out).as_posix():p.read_bytes() for p in out.rglob('*.json')}
    assert validate_export(out) == len(before)
    assert len(list((out/'abstracts').glob('*.json'))) == 256
    assert len(list((out/'cities').glob('*.json'))) == 27
    index = read(out,'library/index.json')
    assert [s['name'] for s in index['shards']] == ['2026','2010-2014','1990-1994','before-1990']
    assert all(row[6]=='unclear' for row in read(out,'library/2026.json')['rows'])
    assert read(out,'trials.json')['trials'][0]['min_age'] == 0.5
    assert read(out,'trials/NCT00000001.json')['completion_date'] is None
    assert read(out,'meta.json')['push_public_key'] == 'public-key'
    assert read(out,'meta.json')['sources']['pubmed']['records'] == 6
    assert read(out,'cities/s.json')['rows'][0][0] == 'São Paulo'
    assert read(out,'cities/other.json')['rows'][0][0] == '東京'
    assert not user.exists()
    # Manifest, output bytes, and the SHA-1 UID shard selection agree.
    key = hashlib.sha1(b'pmid:0').hexdigest()[:2]
    assert read(out,f'abstracts/{key}.json')['abstracts']['pmid:0']=='Some abstract'


def test_status_change_survives_exports_and_cache_rebuild(research,monkeypatch):
    db,out,user = research
    export_static(db,out,build_time=STAMP)
    monkeypatch.setattr(m.http,'get_json',lambda *a:{'dataTimestamp':'v2'})
    monkeypatch.setattr(m.clinicaltrials,'search',lambda:iter([Trial(nct_id='NCT00000001',title='MSA trial',
        status='COMPLETED',conditions=['Multiple System Atrophy'])]))
    m.refresh(db,user,'trials',public_dir=out,notify=False)
    export_static(db,out,build_time='2026-11-15T06:17:00Z')
    changed = read(out,'trials.json')['trials'][0]['status_changed']
    assert changed == {'from':'RECRUITING','to':'COMPLETED','date':datetime.now(timezone.utc).date().isoformat()}
    export_static(db,out,build_time='2026-11-16T06:17:00Z')
    assert read(out,'trials.json')['trials'][0]['status_changed']==changed
    # Fresh working DB still compares with the committed public export.
    db2 = db.with_name('rebuilt.sqlite3')
    monkeypatch.setattr(m.http,'get_json',lambda *a:{'dataTimestamp':'v3'})
    monkeypatch.setattr(m.clinicaltrials,'search',lambda:iter([Trial(nct_id='NCT00000001',title='MSA trial',
        status='TERMINATED',conditions=['Multiple System Atrophy'])]))
    m.refresh(db2,user,'trials',public_dir=out,notify=False)
    s = Store(db2)
    assert s.conn.execute('SELECT previous_status FROM trial_status_history').fetchone()[0]=='COMPLETED'
    assert s.conn.execute('SELECT count(*) FROM push_events').fetchone()[0]==1
    s.close()


def test_check_trials_exit_codes(research,monkeypatch):
    db,out,user = research
    export_static(db,out,build_time=STAMP)
    monkeypatch.setattr(static_build.http,'get_json',lambda *a:{'dataTimestamp':'v1'})
    with pytest.raises(SystemExit) as unchanged:
        main(['check-trials','--out',str(out)])
    assert unchanged.value.code == 3
    monkeypatch.setattr(static_build.http,'get_json',lambda *a:{'dataTimestamp':'v2'})
    with pytest.raises(SystemExit) as changed:
        main(['check-trials','--out',str(out)])
    assert changed.value.code == 0
    monkeypatch.setattr(static_build.http,'get_json',lambda *a:{})
    with pytest.raises(ValueError,match='dataTimestamp'):
        static_build.check_trials(out)


def test_invalid_export_does_not_replace_public_data(research):
    db,out,user = research
    export_static(db,out,build_time=STAMP)
    original = (out/'meta.json').read_bytes()
    s = Store(db)
    s.put_article(Article(uid='bad',evidence='invalid-enum'),{})
    s.commit()
    s.close()
    with pytest.raises(ValueError,match='Invalid export'):
        export_static(db,out,build_time=STAMP)
    assert (out/'meta.json').read_bytes()==original
    assert not list(out.parent.glob('.export-*'))


def test_assembly_contains_only_public_sources(research,tmp_path):
    db,out,user = research
    export_static(db,out,build_time=STAMP)
    web = tmp_path/'web'
    web.mkdir()
    (web/'index.html').write_text('<h1>Static site</h1>')
    (web/'.env').write_text('SECRET')
    (out/'.env').write_text('SECRET')
    site = assemble(web,out,tmp_path/'site')
    assert (site/'index.html').exists() and (site/'data/meta.json').exists()
    assert not list(site.rglob('*.sqlite3')) and not list(site.rglob('.env'))
    assert (site/'.nojekyll').exists()
    assemble(web,out,site)
    with pytest.raises(ValueError):
        assemble(web,out,web)


def test_scheduled_bootstrap_noop_and_daily_catchup(research,monkeypatch):
    db,out,user = research
    clock = [STAMP]
    monkeypatch.setattr('msac.storage._now',lambda:clock[0])
    monkeypatch.setattr(m.http,'get_json',lambda *a:{'dataTimestamp':'v1'})
    monkeypatch.setattr(m.clinicaltrials,'search',lambda:pytest.fail('trial timestamp unchanged'))
    calls = []
    def papers(**kw):
        calls.append(kw['since'])
        return iter([])
    monkeypatch.setattr(m.pubmed,'search',papers)
    monkeypatch.setattr(m.europepmc,'search',papers)
    first = datetime(2026,11,14,6,17,tzinfo=timezone.utc)
    assert static_build.scheduled(db,user,out=out,backup_dir=out.parent/'backups',now=first)
    assert calls==[None,None]
    previous = (out/'meta.json').read_bytes()
    assert not static_build.scheduled(db,user,out=out,now=datetime(2026,11,14,7,17,tzinfo=timezone.utc))
    assert (out/'meta.json').read_bytes()==previous
    clock[0] = '2026-11-15T09:17:00Z'
    assert static_build.scheduled(db,user,out=out,backup_dir=out.parent/'backups',now=datetime(2026,11,15,9,17,tzinfo=timezone.utc))
    assert len(calls)==4


def test_monthly_heartbeat_changes_meta_only(research):
    db,out,user = research
    export_static(db,out,build_time=STAMP,heartbeat=True)
    before = read(out,'meta.json')
    export_static(db,out,build_time='2026-12-01T06:17:00Z',heartbeat=True)
    after = read(out,'meta.json')
    assert before['files']==after['files']
    assert after['generated_at'].startswith('2026-12')
    assert before['build_id']!=after['build_id']


def test_workflow_is_pinned_and_publish_scope_is_public_only():
    root = Path(__file__).resolve().parents[1]
    workflow = (root/'.github/workflows/refresh.yml').read_text()
    uses = re.findall(r'uses:\s*(\S+)',workflow)
    assert uses and all(re.search(r'@[a-f0-9]{40}$',item) for item in uses)
    assert "python-version: '3.12.10'" in workflow
    assert "cron: '17 * * * *'" in workflow
    assert 'cancel-in-progress: false' in workflow
    assert 'git add -- public-data/' in workflow
    assert 'git add .' not in workflow
    assert 'pages: write' in workflow and 'id-token: write' in workflow
    assert 'pull_request:' not in workflow


def test_cache_checkpoint_preserves_committed_research(research):
    from tools.checkpoint_research import checkpoint
    db,out,user = research
    s = Store(db)
    s.put_article(Article(uid='wal-only',title='Committed WAL'),{})
    s.commit()
    checkpoint(db)
    copy = db.with_name('cache-copy.sqlite3')
    copy.write_bytes(db.read_bytes())
    reader = Store(copy,readonly=True)
    assert reader.get_article('wal-only').title=='Committed WAL'
    reader.close()
    s.close()


def test_internal_run_errors_never_enter_public_export_or_site(research,tmp_path):
    db,out,user = research
    private = tmp_path/'data/maintenance-internal.json'
    s = Store(db)
    run = s.start_run('pubmed')
    sentinel = 'PRIVATE_RUN_ERROR_DO_NOT_DEPLOY'
    s.finish_run(run,0,'error',sentinel)
    m.write_meta(s,private)
    assert sentinel in private.read_text()
    for blocked in (out/'meta.json',tmp_path/'_site/internal.json'):
        with pytest.raises(ValueError,match='Internal maintenance metadata'):
            m.write_meta(s,blocked)
        assert not blocked.exists()
    s.close()
    export_static(db,out,build_time=STAMP)
    web = tmp_path/'web'
    web.mkdir()
    (web/'index.html').write_text('<h1>Site</h1>')
    (web/'diagnostics.json').write_bytes(private.read_bytes())
    (web/'data').mkdir()
    (web/'data/maintenance-internal.json').write_bytes(private.read_bytes())
    site = assemble(web,out,tmp_path/'_site')
    assert not (site/'diagnostics.json').exists()
    assert not (site/'data/maintenance-internal.json').exists()
    for folder in (out,site):
        for path in folder.rglob('*.json'):
            assert sentinel not in path.read_text(encoding='utf-8')
    assert 'refresh_runs' not in read(out,'meta.json')


def test_successful_refresh_preserves_export_timestamp_and_skips_redownload(research,monkeypatch):
    db,out,user = research
    calls = []
    stamp = '2026-11-15T05:00:09'
    monkeypatch.setattr(m.http,'get_json',lambda *a:{'dataTimestamp':stamp})
    def fetch():
        calls.append(1)
        return iter([Trial(nct_id='NCT00000001',title='MSA study',status='RECRUITING',conditions=['Multiple System Atrophy'])])
    monkeypatch.setattr(m.clinicaltrials,'search',fetch)
    internal = out.parent/'data/maintenance-internal.json'
    m.refresh(db,user,'trials',meta_path=internal,notify=False)
    export_static(db,out,build_time=STAMP)
    assert read(out,'meta.json')['sources']['clinicaltrials']['data_timestamp']==stamp
    assert not static_build.check_trials(out)
    m.refresh(db,user,'trials',meta_path=internal,notify=False)
    export_static(db,out,build_time=STAMP)
    assert read(out,'meta.json')['sources']['clinicaltrials']['data_timestamp']==stamp
    assert len(calls)==1
