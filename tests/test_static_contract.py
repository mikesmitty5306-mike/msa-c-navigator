from pathlib import Path
import json
import ast

from msac.export.validate import validate_export

ROOT = Path(__file__).resolve().parents[1]


def test_contract_sample():
    sample = ROOT/'contract/sample'
    assert validate_export(sample) > 256
    meta = json.loads((sample/'meta.json').read_text())
    assert meta['counts']['articles'] == 50
    assert meta['counts']['trials'] == 5
    assert len(list((sample/'mechanisms').glob('*.json'))) == 3
    assert len(list((sample/'cities').glob('*.json'))) == 2


def test_real_export_when_present():
    data = ROOT/'public-data'
    if not data.exists():
        import pytest
        pytest.skip('Run export-static to validate the complete research dataset')
    assert validate_export(data) > 256


def test_sample_covers_frontend_smoke_routes():
    # Read the frontend's route list without importing or modifying its test harness.
    tree = ast.parse((ROOT/'tests/frontend/test_static_app.py').read_text(encoding='utf-8'))
    screens = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                   and any(isinstance(t,ast.Name) and t.id=='SCREENS' for t in n.targets))
    sample = ROOT/'contract/sample'
    load = lambda name:json.loads((sample/name).read_text(encoding='utf-8'))
    mids = {m['id'] for m in load('grades.json')['mechanisms']}
    tids = {t['id'] for t in load('trials.json')['trials']}
    for route,expected in screens:
        parts = route.split('?')[0].removeprefix('#/').split('/')
        if parts[0]=='grades' and len(parts)>1 and parts[1]!='help':
            assert parts[1] in mids
            detail = load(f'mechanisms/{parts[1]}.json')
            if len(parts)>2 and parts[2]=='human':
                assert detail['studies']['human']['negative']
            if len(parts)>2 and parts[2]=='trials':
                assert any(t['counts_as_failure'] for t in detail['trials']['stopped'])
                assert all(t['id'] in tids for t in detail['trials']['stopped'])
        if parts[0] in ('trial','trials') and len(parts)>1:
            assert parts[1] in tids
            assert load(f'trials/{parts[1]}.json')['inquiry']['body']
