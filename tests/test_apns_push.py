"""APNs sender: audience topic, sandbox host, missing-env no-op."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from models import DeviceToken, PortalMessage, Transaction, TransactionParticipant, db


TEST_P8 = (
    '-----BEGIN PRIVATE KEY-----\n'
    'MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgloWaYwhRgAbK7ghR\n'
    'dgJFwDTqKCe5s71OH3LG+C7GBBehRANCAARGF0uxHGoJa+Et1Eng8ZDj5y9Zskhi\n'
    'rZCagWSeebZxKXxs41X/Mn2dECxth+TZ0UwfVIPkPqZtWLK0JJQXTK7a\n'
    '-----END PRIVATE KEY-----'
)


@pytest.fixture(autouse=True)
def _isolate_device_tokens(app):
    yield
    with app.app_context():
        DeviceToken.query.delete()
        db.session.commit()


def _clear_apns_env(monkeypatch):
    for key in (
        'APNS_KEY_ID',
        'APNS_TEAM_ID',
        'APNS_KEY',
        'APNS_BUNDLE_ID',
        'APNS_BUNDLE_ID_AGENT',
        'APNS_BUNDLE_ID_CLIENT',
        'APNS_ENVIRONMENT',
    ):
        monkeypatch.delenv(key, raising=False)


def _set_apns_keys(monkeypatch, **overrides):
    _clear_apns_env(monkeypatch)
    values = {
        'APNS_KEY_ID': 'KEYID1234',
        'APNS_TEAM_ID': 'TEAMID1234',
        'APNS_KEY': TEST_P8,
    }
    values.update(overrides)
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


class _OkResponse:
    status_code = 200


def _capture_httpx(monkeypatch):
    import httpx

    calls = []

    def post(url, headers=None, content=None, timeout=None):
        calls.append({
            'url': url,
            'headers': dict(headers or {}),
            'content': content,
        })
        return _OkResponse()

    class HTTP2Client:
        def __init__(self, *, http2, http1, timeout):
            assert http2 is True and http1 is False and timeout == 10.0
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def post(self, *args, **kwargs):
            return post(*args, **kwargs)
    monkeypatch.setattr(httpx, 'Client', HTTP2Client)
    return calls


def _thread(seed, address):
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=seed['tx_type_a'],
        street_address=address,
        city='Austin',
        state='TX',
        status='active',
    )
    db.session.add(tx)
    db.session.flush()
    seller = TransactionParticipant(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        contact_id=seed['contact_a'],
        role='seller',
        is_primary=True,
    )
    db.session.add(seller)
    db.session.flush()
    return tx, seller


def _portal_message(seed, tx, seller, sender, body):
    msg = PortalMessage(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        participant_id=seller.id,
        sender=sender,
        kind='message',
        body=body,
        author_user_id=seed['owner_a'] if sender == 'agent' else None,
    )
    db.session.add(msg)
    db.session.flush()
    return msg


def _device(seed, audience, token, participant_id=None):
    row = DeviceToken(
        organization_id=seed['org_a'],
        audience=audience,
        token=token,
        platform='ios',
        user_id=seed['owner_a'] if audience == DeviceToken.AUDIENCE_AGENT else None,
        participant_id=(
            participant_id if audience == DeviceToken.AUDIENCE_CLIENT else None
        ),
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_missing_env_still_noops(app, seed, monkeypatch):
    from jobs.apns_push import apns_configured, send_portal_push
    from services.device_push import enqueue_portal_push

    _clear_apns_env(monkeypatch)
    calls = _capture_httpx(monkeypatch)

    assert apns_configured() is False
    assert send_portal_push(message_id=1, org_id=seed['org_a']) == {
        'ok': False,
        'reason': 'apns_unconfigured',
    }
    assert calls == []

    with app.app_context():
        tx, seller = _thread(seed, '500 Missing Env Ln')
        msg = _portal_message(seed, tx, seller, 'agent', 'Still no push.')
        db.session.commit()
        skipped = enqueue_portal_push(msg)
    assert skipped['reason'] == 'apns_unconfigured'


def test_configured_without_legacy_bundle_id(monkeypatch):
    from jobs.apns_push import apns_configured

    _set_apns_keys(monkeypatch)
    assert apns_configured() is True


def test_send_uses_audience_topics_and_sandbox_host(app, seed, monkeypatch):
    from jobs.apns_push import send_portal_push

    _set_apns_keys(monkeypatch, APNS_ENVIRONMENT='sandbox')
    calls = _capture_httpx(monkeypatch)

    with app.app_context():
        tx, seller = _thread(seed, '501 Topic Ln')
        client_msg = _portal_message(seed, tx, seller, 'client', 'From the seller.')
        agent_msg = _portal_message(seed, tx, seller, 'agent', 'From the agent.')
        _device(seed, DeviceToken.AUDIENCE_AGENT, 'agent-device-token')
        _device(
            seed, DeviceToken.AUDIENCE_CLIENT, 'client-device-token',
            participant_id=seller.id,
        )
        db.session.commit()
        client_msg_id = client_msg.id
        agent_msg_id = agent_msg.id
        org_id = seed['org_a']

    to_agent = send_portal_push(message_id=client_msg_id, org_id=org_id)
    to_client = send_portal_push(message_id=agent_msg_id, org_id=org_id)

    assert to_agent == {'ok': True, 'sent': 1}
    assert to_client == {'ok': True, 'sent': 1}
    assert len(calls) == 2

    agent_call = next(c for c in calls if c['url'].endswith('/agent-device-token'))
    client_call = next(c for c in calls if c['url'].endswith('/client-device-token'))

    assert agent_call['url'] == (
        'https://api.sandbox.push.apple.com/3/device/agent-device-token'
    )
    assert client_call['url'] == (
        'https://api.sandbox.push.apple.com/3/device/client-device-token'
    )
    assert agent_call['headers']['apns-topic'] == 'com.agentflow.agent'
    assert client_call['headers']['apns-topic'] == 'com.agentflow.client'


def test_audience_env_overrides_default_topics(app, seed, monkeypatch):
    from jobs.apns_push import send_portal_push

    _set_apns_keys(
        monkeypatch,
        APNS_ENVIRONMENT='sandbox',
        APNS_BUNDLE_ID='com.legacy.shared',
        APNS_BUNDLE_ID_AGENT='com.custom.agent',
        APNS_BUNDLE_ID_CLIENT='com.custom.client',
    )
    calls = _capture_httpx(monkeypatch)

    with app.app_context():
        tx, seller = _thread(seed, '502 Override Ln')
        client_msg = _portal_message(seed, tx, seller, 'client', 'Override agent.')
        agent_msg = _portal_message(seed, tx, seller, 'agent', 'Override client.')
        _device(seed, DeviceToken.AUDIENCE_AGENT, 'override-agent-token')
        _device(
            seed, DeviceToken.AUDIENCE_CLIENT, 'override-client-token',
            participant_id=seller.id,
        )
        db.session.commit()
        client_msg_id = client_msg.id
        agent_msg_id = agent_msg.id
        org_id = seed['org_a']

    send_portal_push(message_id=client_msg_id, org_id=org_id)
    send_portal_push(message_id=agent_msg_id, org_id=org_id)

    topics = {c['url'].rsplit('/', 1)[-1]: c['headers']['apns-topic'] for c in calls}
    assert topics['override-agent-token'] == 'com.custom.agent'
    assert topics['override-client-token'] == 'com.custom.client'


def test_legacy_bundle_id_does_not_replace_audience_defaults(app, seed, monkeypatch):
    from jobs.apns_push import send_portal_push

    _set_apns_keys(
        monkeypatch,
        APNS_ENVIRONMENT='sandbox',
        APNS_BUNDLE_ID='com.legacy.shared',
    )
    calls = _capture_httpx(monkeypatch)

    with app.app_context():
        tx, seller = _thread(seed, '503 Legacy Ln')
        client_msg = _portal_message(seed, tx, seller, 'client', 'Legacy agent.')
        _device(seed, DeviceToken.AUDIENCE_AGENT, 'legacy-agent-token')
        db.session.commit()
        result = send_portal_push(message_id=client_msg.id, org_id=seed['org_a'])

    assert result == {'ok': True, 'sent': 1}
    assert calls[0]['headers']['apns-topic'] == 'com.agentflow.agent'


def test_production_host_when_environment_is_production(monkeypatch):
    from services.apns_client import send_alert

    _set_apns_keys(monkeypatch, APNS_ENVIRONMENT='production')
    calls = _capture_httpx(monkeypatch)
    msg = SimpleNamespace(
        id=9,
        body='Production host.',
        sender='client',
        transaction_id=1,
        participant_id=2,
    )

    assert send_alert('prod-device-token', msg, audience='agent') is True
    assert calls[0]['url'] == (
        'https://api.push.apple.com/3/device/prod-device-token'
    )
    assert calls[0]['headers']['apns-topic'] == 'com.agentflow.agent'


def test_production_host_when_environment_unset(monkeypatch):
    from services.apns_client import send_alert

    _set_apns_keys(monkeypatch)
    calls = _capture_httpx(monkeypatch)
    msg = SimpleNamespace(
        id=10,
        body='Default host.',
        sender='agent',
        transaction_id=1,
        participant_id=2,
    )

    assert send_alert('default-host-token', msg, audience='client') is True
    assert calls[0]['url'] == (
        'https://api.push.apple.com/3/device/default-host-token'
    )
    assert calls[0]['headers']['apns-topic'] == 'com.agentflow.client'


def test_date_alert_has_expiry_and_collapse_id(monkeypatch):
    from services.apns_client import send_payload
    import json
    _set_apns_keys(monkeypatch)
    calls = _capture_httpx(monkeypatch)
    payload = {'kind': 'milestones', 'transaction_id': 77,
               'aps': {'alert': {'title': 'Transaction dates updated', 'body': 'Open Next steps'}}}
    assert send_payload('test-token', payload, audience='client')
    assert int(calls[0]['headers']['apns-expiration']) > 0
    assert calls[0]['headers']['apns-collapse-id'] == 'dates-77'
    assert json.loads(calls[0]['content'])['kind'] == 'milestones'


def test_inquiry_push_recipients_assignment_and_device_environments(app, seed, monkeypatch):
    import json
    import uuid
    from datetime import datetime, timedelta
    from models import ClientBrowseAccount, ClientBrowseInquiry, ClientInquiryMessage
    from jobs.apns_push import send_inquiry_push

    _set_apns_keys(monkeypatch, APNS_ENVIRONMENT='sandbox')
    calls = _capture_httpx(monkeypatch)
    with app.app_context():
        account = ClientBrowseAccount(organization_id=seed['org_a'], name='Push Client',
            email=f'{uuid.uuid4()}@example.com', password_hash='not-used')
        db.session.add(account)
        db.session.flush()
        inquiry = ClientBrowseInquiry(organization_id=seed['org_a'], account_id=account.id,
            kind='message', body='Can someone help?', request_id=str(uuid.uuid4()))
        db.session.add(inquiry)
        db.session.flush()
        for user, token, org in [(seed['owner_a'], 'assigned', seed['org_a']),
                (seed['agent_a'], 'unassigned', seed['org_a']), (seed['owner_b'], 'foreign', seed['org_b'])]:
            db.session.add(DeviceToken(organization_id=org, user_id=user, token=token,
                audience='agent', platform='ios', environment='production'))
        db.session.add(DeviceToken(organization_id=seed['org_a'], browse_account_id=account.id,
            audience='browse', platform='ios', token='browser', environment='sandbox',
            expires_at=datetime.utcnow() + timedelta(days=1)))
        db.session.add(DeviceToken(organization_id=seed['org_a'], browse_account_id=account.id,
            audience='browse', platform='ios', token='expired-browser',
            expires_at=datetime.utcnow() - timedelta(seconds=1)))
        db.session.commit()
        inquiry_id, account_id = inquiry.id, account.id

    assert send_inquiry_push(inquiry_id=inquiry_id, org_id=seed['org_a'])['sent'] == 2
    assert {c['url'].rsplit('/', 1)[-1] for c in calls} == {'assigned', 'unassigned'}
    assert all(c['url'].startswith('https://api.push.apple.com/') for c in calls)
    calls.clear()
    with app.app_context():
        account = db.session.get(ClientBrowseAccount, account_id)
        inquiry = db.session.get(ClientBrowseInquiry, inquiry_id)
        account.agent_id = inquiry.agent_id = seed['owner_a']
        reply = ClientInquiryMessage(organization_id=seed['org_a'], inquiry_id=inquiry_id,
            sender='agent', author_user_id=seed['owner_a'], body='I can help.', request_id=str(uuid.uuid4()))
        notice = ClientInquiryMessage(organization_id=seed['org_a'], inquiry_id=inquiry_id,
            sender='agent', author_user_id=seed['owner_a'], body='Agent Test Owner has reviewed your message and will respond shortly.', request_id=str(uuid.uuid4()))
        db.session.add_all([reply, notice])
        db.session.commit()
        reply_id, notice_id = reply.id, notice.id
    assert send_inquiry_push(inquiry_id=inquiry_id, org_id=seed['org_a'])['sent'] == 1
    assert calls[0]['url'].endswith('/assigned')
    calls.clear()
    for message_id, assignment in [(reply_id, False), (notice_id, True)]:
        assert send_inquiry_push(inquiry_id=inquiry_id, org_id=seed['org_a'], message_id=message_id, assignment=assignment)['sent'] == 1
    assert all(c['url'] == 'https://api.sandbox.push.apple.com/3/device/browser' for c in calls)
    assert all(c['headers']['apns-topic'] == 'com.agentflow.client' for c in calls)
    payload = json.loads(calls[-1]['content'])
    assert payload['kind'] == 'inquiry_assignment'
    assert payload['inquiry_id'] == inquiry_id and payload['account_id'] == account_id
    assert payload['aps']['alert']['body'] == 'Agent Test Owner has reviewed your message and will respond shortly.'


def test_portal_push_excludes_agents_without_transaction_access(app, seed, monkeypatch):
    from jobs.apns_push import send_portal_push
    _set_apns_keys(monkeypatch)
    calls = _capture_httpx(monkeypatch)
    with app.app_context():
        tx, seller = _thread(seed, '503 Private Transaction Ln')
        msg = _portal_message(seed, tx, seller, 'client', 'Private client message')
        _device(seed, 'agent', 'transaction-owner')
        db.session.add(DeviceToken(organization_id=seed['org_a'], user_id=seed['agent_a'],
            audience='agent', platform='ios', token='unrelated-agent'))
        db.session.commit()
        message_id = msg.id
    assert send_portal_push(message_id=message_id, org_id=seed['org_a'])['sent'] == 1
    assert len(calls) == 1 and calls[0]['url'].endswith('/transaction-owner')


def test_portal_retry_only_resends_failed_devices(app, seed, monkeypatch):
    import rq
    from jobs.apns_push import send_portal_push
    _set_apns_keys(monkeypatch)
    job = SimpleNamespace(meta={}, save_meta=lambda: None)
    monkeypatch.setattr(rq, 'get_current_job', lambda: job)
    attempts = []
    def deliver(token, *_args):
        attempts.append(token)
        return token != 'retry-second' or attempts.count(token) > 1
    monkeypatch.setattr('jobs.apns_push._send_one', deliver)
    with app.app_context():
        tx, seller = _thread(seed, '504 Retry Ln')
        msg = _portal_message(seed, tx, seller, 'client', 'One notification per device')
        _device(seed, 'agent', 'retry-first')
        _device(seed, 'agent', 'retry-second')
        db.session.commit()
        message_id = msg.id
    with pytest.raises(RuntimeError, match='1 devices'):
        send_portal_push(message_id=message_id, org_id=seed['org_a'])
    assert send_portal_push(message_id=message_id, org_id=seed['org_a']) == {'ok': True, 'sent': 1}
    assert attempts.count('retry-first') == 1
    assert attempts.count('retry-second') == 2
