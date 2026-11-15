import json
import base64
import re
import time
from dataclasses import replace
import pytest

from msac import maintenance,push
from msac.storage import Store
from msac.models import Trial


@pytest.fixture(autouse=True)
def isolate_actions_summary(monkeypatch):
    monkeypatch.delenv('GITHUB_STEP_SUMMARY',raising=False)


def test_multiple_subscriptions_share_event_dedupe(capsys):
    s = Store(':memory:')
    maintenance.initialize(s)
    event = push.detect_events({}, {'NCT1':Trial(nct_id='NCT1',title='MSA',status='RECRUITING')},'v1')[0]
    assert event['payload']['url']=='#/trials/NCT1'
    assert event['payload']['tag']=='NCT1:RECRUITING'
    s.conn.execute('INSERT INTO push_events(id,payload) VALUES (?,?)',(event['id'],json.dumps(event['payload'])))
    s.commit()
    subscriptions = [{'endpoint':f'https://example.test/{i}','keys':{'auth':'a','p256dh':'p'}} for i in range(2)]
    env = dict(PUSH_SUBSCRIPTIONS=json.dumps(subscriptions),VAPID_PRIVATE_KEY='secret',
               VAPID_PUBLIC_KEY='public',VAPID_SUBJECT='mailto:example@example.org')
    calls = []
    assert push.send_pending(s,environ=env,sender=lambda **kw:calls.append(kw))==1
    assert len(calls)==2
    assert push.send_pending(s,environ=env,sender=lambda **kw:calls.append(kw))==0
    assert len(calls)==2
    assert 'PUSH_SUBSCRIPTIONS' not in capsys.readouterr().out
    s.close()


def test_notification_status_text_uses_plain_labels():
    old = Trial(nct_id='NCT1',title='MSA trial',status='NOT_YET_RECRUITING')
    new = replace(old,status='RECRUITING')
    payload = push.detect_events({'NCT1':old},{'NCT1':new},'v2')[0]['payload']
    assert payload['body']=='MSA trial: Not Yet Recruiting → Recruiting'
    assert 'RECRUITING' not in payload['body']
    assert payload['url']=='#/trials/NCT1'


@pytest.mark.parametrize('fit', ['includes_msa_c','any_msa','msa_p_focused','excludes_msa_c'])
@pytest.mark.parametrize('previous', [None,'RECRUITING','COMPLETED'])
def test_notifications_only_for_msa_c_fits(fit,previous):
    new = Trial(nct_id='NCT1',title='MSA trial',status='RECRUITING' if previous is None else 'TERMINATED',subtype_fit=fit)
    # Previously relevant/open trials must still be excluded if their current fit is unsuitable.
    before = {} if previous is None else {'NCT1':replace(new,status=previous,subtype_fit='any_msa')}
    events = push.detect_events(before,{'NCT1':new},'v2')
    assert bool(events)==(fit in push.FITS)


def test_single_subscription_precedes_legacy_fallback(capsys):
    s = Store(':memory:')
    maintenance.initialize(s)
    s.conn.execute('INSERT INTO push_events(id,payload) VALUES (?,?)',('event',json.dumps({'title':'MSA','body':'Update','url':'#/trials/NCT1'})))
    s.commit()
    single = {'endpoint':'https://example.test/single','keys':{'auth':'a','p256dh':'p'}}
    env = dict(PUSH_SUBSCRIPTION=json.dumps(single),PUSH_SUBSCRIPTIONS='invalid ignored legacy value',
               VAPID_PRIVATE_KEY='secret',VAPID_PUBLIC_KEY='public',VAPID_SUBJECT='mailto:test@example.org')
    calls = []
    assert push.send_pending(s,environ=env,sender=lambda **kw:calls.append(kw))==1
    assert [c['subscription_info'] for c in calls]==[single]
    assert 'PUSH_SUBSCRIPTIONS' not in capsys.readouterr().out
    s.close()


@pytest.mark.parametrize('subject',[
    'https://researcher.github.io/msac-navigator',
    'https://researcher.github.io/msac-navigator/',
    'mailto:helper@example.org',
])
def test_https_and_mailto_subjects_work_for_events_and_test_push(subject):
    pair = push.vapid_key_pair()
    env = dict(pair,VAPID_SUBJECT=subject,PUSH_SUBSCRIPTION=json.dumps({
        'endpoint':'https://push.example.test/device','keys':{'auth':'a','p256dh':'p'}}))
    s = Store(':memory:')
    maintenance.initialize(s)
    payload = {'title':'MSA','body':'Recruiting','url':'#/trials/NCT1'}
    s.conn.execute('INSERT INTO push_events(id,payload) VALUES (?,?)',('event',json.dumps(payload)))
    s.commit()
    calls = []
    sender = lambda **kwargs:calls.append(kwargs)
    assert push.send_pending(s,environ=env,sender=sender)==1
    assert push.send_test_push(environ=env,sender=sender)==1
    assert len(calls)==2
    from py_vapid import Vapid
    for call in calls:
        assert call['vapid_claims']['sub']==subject
        signer = call['vapid_private_key']
        if isinstance(signer,str):
            signer = Vapid.from_string(signer)
        # Exercise the real VAPID signer, including full HTTPS repository paths,
        # and inspect the signed JWT without making a network request.
        headers = signer.sign({'sub':subject,'aud':'https://push.example.test','exp':int(time.time())+3600})
        token = re.search(r'\bt=([^, ]+)',headers['Authorization']).group(1)
        body = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        assert claims['sub']==subject
    s.close()


@pytest.mark.parametrize('subject',['','helper@example.org','http://example.org/contact','https:///missing-host','https://user:password@example.org/repo','https://example.org:bad/repo'])
def test_invalid_vapid_subjects_are_rejected(subject):
    with pytest.raises(ValueError,match='https: site URL'):
        push.vapid_subject(subject)
