"""Web Push events and a durable delivery ledger. Never log subscription endpoints or keys."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

FITS = {'includes_msa_c', 'any_msa'}


def vapid_subject(value):
    """Accept an absolute HTTPS site URL or mailto contact without requiring email."""
    if isinstance(value,str):
        value = value.strip()
        try:
            parsed = urlsplit(value)
            if not re.search(r'\s',value):
                if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password:
                    parsed.port  # reject malformed port numbers
                    return value
                if parsed.scheme=='mailto' and not parsed.netloc and not parsed.query and not parsed.fragment and re.fullmatch(r'[^@\s]+@[^@\s]+',parsed.path):
                    return value
        except ValueError:
            pass
    raise ValueError('VAPID_SUBJECT must be an https: site URL (recommended) or a mailto: address')


def _vapid_signer(private_key,subject):
    if urlsplit(subject).scheme!='https':
        return private_key
    from py_vapid import Vapid
    # py-vapid's legacy strict regex rejects valid HTTPS paths such as /repo/.
    # Our URI validator above replaces that check on this signer instance only.
    subject = vapid_subject(subject)
    signer = Vapid.from_string(private_key)
    signer.conf['no-strict'] = True
    return signer


def detect_events(before, after, revision):
    events = []
    for nct_id, trial in sorted(after.items()):
        if trial.subtype_fit not in FITS:
            continue
        old = before.get(nct_id)
        if old is None:
            if not (trial.is_open and trial.subtype_fit in FITS):
                continue
            title, body = 'New MSA trial', trial.title
            change = 'new'
        elif old.status != trial.status:
            title = 'Trial status changed'
            body = f'{trial.title}: {old.status_label} → {trial.status_label}'
            change = f'{old.status}:{trial.status}'
        else:
            continue
        event_id = hashlib.sha256(f'{nct_id}|{revision}|{change}'.encode()).hexdigest()
        events.append({'id': event_id, 'payload': {'title': title, 'body': body[:500], 'url': f'#/trials/{nct_id}',
                                                 'tag': f'{nct_id}:{trial.status}'}})
    return events


def _report(message):
    print(message)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as out:
            out.write(message + '\n\n')


def send_pending(store, *, environ=None, sender=None):
    env = os.environ if environ is None else environ
    # Prefer the documented single-phone secret; accept the legacy list silently.
    subscriptions = None
    if not env.get('PUSH_SUBSCRIPTION') and env.get('PUSH_SUBSCRIPTIONS'):
        try:
            subscriptions = json.loads(env['PUSH_SUBSCRIPTIONS'])
            if not isinstance(subscriptions,list) or not subscriptions or not all(
                isinstance(s,dict) and s.get('endpoint') and s.get('keys') for s in subscriptions):
                raise ValueError('invalid list')
            subscriptions = list({s['endpoint']:s for s in subscriptions}.values())
            env = dict(env,PUSH_SUBSCRIPTION=json.dumps(subscriptions[0]))
        except (ValueError,TypeError):
            _report('Push skipped: PUSH_SUBSCRIPTIONS must be a nonempty JSON list of subscriptions.')
            return 0
    required = ('PUSH_SUBSCRIPTION', 'VAPID_PRIVATE_KEY', 'VAPID_PUBLIC_KEY')
    if not all(env.get(key) for key in required):
        _report('Push skipped: PUSH_SUBSCRIPTION or VAPID keys are missing.')
        return 0
    try:
        contact = vapid_subject(env.get('VAPID_SUBJECT'))
    except ValueError:
        _report('Push skipped: configure VAPID_SUBJECT as an https: site URL or mailto: address.')
        return 0
    try:
        subscription = json.loads(env['PUSH_SUBSCRIPTION'])
        if not subscription.get('endpoint') or not subscription.get('keys'):
            raise ValueError('invalid subscription')
    except (ValueError, TypeError, AttributeError):
        _report('Push skipped: PUSH_SUBSCRIPTION is not a valid subscription JSON object.')
        return 0
    if sender is None:
        from pywebpush import webpush
        sender = webpush
    if subscriptions:
        direct_sender = sender
        def fanout(**kwargs):
            for target in subscriptions:
                try:
                    direct_sender(**dict(kwargs,subscription_info=target,vapid_claims=dict(kwargs['vapid_claims'])))
                except Exception as exc:
                    response = getattr(exc,'response',None)
                    if getattr(response,'status_code',None) in (404,410):
                        _report('WARNING: Push subscription expired (HTTP 404/410). Subscribe again and replace PUSH_SUBSCRIPTION.')
                    else:
                        _report('WARNING: Push delivery failed for one phone; event attempt retained to prevent duplicates.')
        sender = fanout
    rows = store.conn.execute("SELECT id,payload FROM push_events WHERE state='pending' ORDER BY rowid").fetchall()
    # Maximum five requests, including the summary: four individual + one summary if overflowing.
    batches = [([r['id']], json.loads(r['payload'])) for r in rows[:5 if len(rows) <= 5 else 4]]
    if len(rows) > 5:
        batches.append(([r['id'] for r in rows[4:]], {'title': 'More MSA trial updates',
                        'body': f'{len(rows) - 4} additional trial updates. Open Trials to review them.',
                        'url': '#/trials'}))
    sent = 0
    for ids, payload in batches:
        # Reserve durably before the network request: at-most-once attempts after process crash.
        with store.conn:
            store.conn.executemany("UPDATE push_events SET state='sending' WHERE id=?", [(key,) for key in ids])
        try:
            sender(subscription_info=subscription, data=json.dumps(payload),
                   vapid_private_key=_vapid_signer(env['VAPID_PRIVATE_KEY'],contact), vapid_claims={'sub': contact}, timeout=30)
        except Exception as exc:
            response = getattr(exc, 'response', None)
            status = getattr(response, 'status_code', None)
            if status in (404, 410):
                _report('WARNING: Push subscription expired (HTTP 404/410). Subscribe again and replace PUSH_SUBSCRIPTION.')
            else:
                _report('WARNING: Push delivery failed. Event attempts retained to prevent duplicate notifications.')
            with store.conn:
                store.conn.executemany("UPDATE push_events SET state='failed' WHERE id=?", [(key,) for key in ids])
            break
        with store.conn:
            store.conn.executemany("UPDATE push_events SET state='sent' WHERE id=?", [(key,) for key in ids])
        sent += 1
    _report(f'Push notifications sent: {sent}.')
    return sent


def vapid_key_pair():
    """Generate paste-ready keys in memory; never persist private material implicitly."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    private = base64.urlsafe_b64encode(key.private_bytes(serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode().rstrip('=')
    public = base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')
    return {'VAPID_PRIVATE_KEY': private, 'VAPID_PUBLIC_KEY': public}


def generate_keys(path=None):
    """Print keys by default; explicit file output is allowed only outside repositories."""
    keys = vapid_key_pair()
    if path is None:
        print(json.dumps(keys,indent=2))
        return
    path = Path(path)
    from . import config
    resolved = path.resolve()
    if resolved.is_relative_to(config.APP_DIR) or any((p/'.git').exists() for p in resolved.parents):
        raise ValueError('Private VAPID keys must not be written inside a repository; print them or choose an external path')
    # Exclusive creation, restrictive permissions on Linux; never print private material.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(keys, out, indent=2)
    print(f"Key pair saved to {path}. Keep this file private. Public key: {keys['VAPID_PUBLIC_KEY']}")


def send_test_push(*, environ=None, sender=None):
    """One explicitly requested notification to the standard single subscription."""
    env = os.environ if environ is None else environ
    required = ('PUSH_SUBSCRIPTION','VAPID_PRIVATE_KEY','VAPID_PUBLIC_KEY','VAPID_SUBJECT')
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise ValueError('Test push requires these GitHub secrets: '+', '.join(missing))
    subject = vapid_subject(env['VAPID_SUBJECT'])
    try:
        subscription = json.loads(env['PUSH_SUBSCRIPTION'])
        if not isinstance(subscription,dict) or not subscription.get('endpoint') or not subscription.get('keys'):
            raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('PUSH_SUBSCRIPTION must be one subscription JSON object') from None
    if sender is None:
        from pywebpush import webpush
        sender = webpush
    try:
        sender(subscription_info=subscription,
               data=json.dumps({'title':'MSA-C Navigator test','body':'Test notification: your phone is connected.',
                                'url':'#/trials','tag':'msac-test'}),
               vapid_private_key=_vapid_signer(env['VAPID_PRIVATE_KEY'],subject),vapid_claims={'sub':subject},timeout=30)
    except Exception as exc:
        code = getattr(getattr(exc,'response',None),'status_code',None)
        if code in (404,410):
            raise RuntimeError('Test push subscription expired (404/410). Subscribe again and replace PUSH_SUBSCRIPTION.') from None
        raise RuntimeError('Test push delivery failed. Check the VAPID keys and PUSH_SUBSCRIPTION; private details were omitted.') from None
    _report('One test notification sent to PUSH_SUBSCRIPTION.')
    return 1
