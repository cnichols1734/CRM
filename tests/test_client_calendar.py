from datetime import datetime
from urllib.parse import urlsplit
import pytest

from models import db, SellerAcceptedContract, SellerContractMilestone, ClientPortalAccess
from services.client_calendar import calendar_token, render_calendar
from tests.test_client_portal_api import _seller_tx, _participant, _grant, _open_session, _auth_headers


def _setup(seed):
    tx = _seller_tx(seed)
    person = _participant(seed, tx, contact_id=seed['contact_a'])
    access = _grant(seed, tx, person)
    contract = SellerAcceptedContract(organization_id=seed['org_a'], transaction_id=tx.id,
        created_by_id=seed['owner_a'], position='primary', status='active', accepted_price=400000)
    db.session.add(contract)
    db.session.flush()
    milestone = SellerContractMilestone(organization_id=seed['org_a'], transaction_id=tx.id,
        accepted_contract_id=contract.id, milestone_key='closing', source='calculated',
        title='Closing', due_at=datetime(2026, 10, 7), status='not_started')
    db.session.add(milestone)
    db.session.commit()
    return access, milestone


def test_subscription_auth_live_updates_and_revocation(app, seed):
    client = app.test_client()
    with app.app_context():
        access, milestone = _setup(seed)
        code, aid, mid = access.invite_code, access.id, milestone.id
    assert client.post('/api/client/v1/calendar-subscription').status_code == 401
    token = _open_session(client, code).get_json()['token']
    response = client.post('/api/client/v1/calendar-subscription', headers=_auth_headers(token))
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    path = urlsplit(response.json['url']).path
    first = client.get(path)
    assert first.status_code == 200
    assert first.mimetype == 'text/calendar'
    assert 'DTSTART;VALUE=DATE:20261007' in first.text
    uid = next(line for line in first.text.splitlines() if line.startswith('UID:'))
    with app.app_context():
        db.session.get(SellerContractMilestone, mid).due_at = datetime(2026, 10, 9)
        db.session.commit()
    second = client.get(path)
    assert uid in second.text and 'DTSTART;VALUE=DATE:20261009' in second.text
    assert '20261007' not in second.text
    assert client.get(path.replace('/calendar/', '/calendar/tampered')).status_code == 404
    # Calendar credentials never authorize the broader client API.
    feed_token = path.split('/calendar/')[1].split('/')[0]
    assert client.get('/api/client/v1/deal', headers=_auth_headers(feed_token)).status_code == 401
    with app.app_context():
        db.session.get(ClientPortalAccess, aid).session_version += 1
        db.session.commit()
    assert client.get(path).status_code == 404


def test_feed_scope_escaping_and_regenerated_identity(app, seed):
    with app.app_context():
        access, milestone = _setup(seed)
        original = render_calendar(access)
        uid = next(line for line in original.splitlines() if line.startswith('UID:'))
        contract_id = milestone.accepted_contract_id
        db.session.delete(milestone)
        db.session.flush()
        db.session.add(SellerContractMilestone(organization_id=access.organization_id,
            transaction_id=access.transaction_id, accepted_contract_id=contract_id,
            milestone_key='closing', source='calculated', title='é' * 100 + ', Title\nBEGIN:VEVENT',
            due_at=datetime(2026, 10, 11), status='not_started', notes='PRIVATE AGENT NOTE'))
        db.session.flush()
        feed = render_calendar(access)
        assert uid in feed and 'PRIVATE AGENT NOTE' not in feed
        assert all(len(line.encode()) <= 75 for line in feed.split('\r\n'))
        assert feed.count('\r\nBEGIN:VEVENT\r\n') == 1
        other, _ = _setup(seed)
        assert uid not in render_calendar(other)
        access.is_active = False
        db.session.commit()
        path = '/api/client/v1/calendar/' + calendar_token(access) + '/dates.ics'
    assert app.test_client().get(path).status_code == 404


def test_dates_notify_only_after_commit_and_ignore_noop(app, seed, monkeypatch):
    calls = []
    monkeypatch.setattr('services.device_push.enqueue_date_push', lambda **kw: calls.append(kw))
    with app.app_context():
        access, milestone = _setup(seed)
        assert len(calls) == 1 and calls[0]['added'] == 1
        calls.clear()
        milestone.due_at = datetime(2026, 10, 12)
        db.session.flush()
        assert calls == []
        db.session.rollback()
        assert calls == []
        milestone = db.session.get(SellerContractMilestone, milestone.id)
        milestone.notes = 'Internal note'
        db.session.commit()
        assert calls == []
        milestone.due_at = datetime(2026, 10, 13)
        db.session.flush()
        milestone.due_at = datetime(2026, 10, 14)
        db.session.commit()
        assert len(calls) == 1 and calls[0]['changed'] == 1
        assert calls[0]['org_id'] == access.organization_id


def test_date_push_scopes_recipients(app, seed, monkeypatch):
    from models import DeviceToken
    from jobs.apns_push import send_date_push
    import app as app_module
    monkeypatch.setattr(app_module, 'app', app)
    monkeypatch.setattr('jobs.apns_push.apns_configured', lambda: True)
    monkeypatch.setattr('services.device_push.enqueue_date_push', lambda **kw: None)
    sent = []
    monkeypatch.setattr('services.apns_client.send_payload', lambda token, payload, audience: sent.append((token, payload, audience)) or True)
    with app.app_context():
        access, _ = _setup(seed)
        other, _ = _setup(seed)
        org, tx = access.organization_id, access.transaction_id
        for grant, token in [(access, 'right-device'), (other, 'wrong-deal')]:
            db.session.add(DeviceToken(organization_id=org, audience='client', token=token,
                                      platform='ios', participant_id=grant.participant_id))
        db.session.commit()
    result = send_date_push(org_id=org, transaction_id=tx, added=1, changed=0)
    assert result['sent'] == 1
    assert sent[0][0] == 'right-device' and sent[0][1]['kind'] == 'milestones'
    assert sent[0][2] == 'client'


def test_no_alert_before_outer_commit_or_after_nested_rollback(app, seed, monkeypatch):
    calls = []
    monkeypatch.setattr('services.device_push.enqueue_date_push', lambda **kw: calls.append(kw))
    with app.app_context():
        access, milestone = _setup(seed)
        calls.clear()
        with db.session.begin_nested():
            milestone.due_at = datetime(2026, 11, 1)
        assert calls == []
        db.session.rollback()
        assert calls == []
        milestone.due_at = datetime(2026, 11, 2)
        db.session.flush()
        nested = db.session.begin_nested()
        milestone.due_at = datetime(2026, 11, 3)
        db.session.flush()
        nested.rollback()
        assert calls == []
        db.session.commit()
        assert len(calls) == 1 and calls[0]['changed'] == 1


def test_recalculation_keeps_uid_and_does_not_notify_unchanged_dates(app, seed, monkeypatch):
    calls = []
    monkeypatch.setattr('services.device_push.enqueue_date_push', lambda **kw: calls.append(kw))
    with app.app_context():
        access, milestone = _setup(seed)
        from services.client_calendar import event_key
        original = render_calendar(access)
        uid = next(line for line in original.splitlines() if line.startswith('UID:'))
        milestone.source = 'manual'
        db.session.commit()
        assert uid in render_calendar(access)
        calls.clear()
        contract = milestone.accepted_contract_id
        db.session.delete(milestone)
        db.session.flush()
        db.session.add(SellerContractMilestone(organization_id=access.organization_id,
            transaction_id=access.transaction_id, accepted_contract_id=contract,
            milestone_key='closing', source='calculated', title='Closing',
            due_at=datetime(2026, 10, 7), status='not_started'))
        db.session.commit()
        assert calls == [] and uid in render_calendar(access)


def test_suspended_organization_cannot_read_feed_or_receive_date_push(app, seed, monkeypatch):
    from models import Organization
    from jobs.apns_push import send_date_push
    import app as app_module

    monkeypatch.setattr(app_module, 'app', app)
    monkeypatch.setattr('jobs.apns_push.apns_configured', lambda: True)
    monkeypatch.setattr('services.device_push.enqueue_date_push', lambda **kw: None)
    with app.app_context():
        access, _ = _setup(seed)
        path = '/api/client/v1/calendar/' + calendar_token(access) + '/dates.ics'
        org_id, transaction_id = access.organization_id, access.transaction_id
        organization = db.session.get(Organization, org_id)
        organization.status = 'suspended'
        db.session.commit()
    try:
        assert app.test_client().get(path).status_code == 404
        assert send_date_push(org_id=org_id, transaction_id=transaction_id, added=1, changed=0) == {
            'ok': False, 'reason': 'organization_inactive',
        }
    finally:
        with app.app_context():
            db.session.get(Organization, org_id).status = 'active'
            db.session.commit()


def test_calendar_credentials_are_redacted_from_app_and_access_log_formats():
    import logging
    from app import _PrivateCalendarFormatter

    token = 'private-calendar-credential'
    path = f'/api/client/v1/calendar/{token}/dates.ics'
    formatter = _PrivateCalendarFormatter(logging.Formatter('%(levelname)s:%(message)s'))
    app_record = logging.LogRecord('app', logging.WARNING, __file__, 1,
        'request_summary path=%s status=%s', (path, 500), None)
    access_record = logging.LogRecord('gunicorn.access', logging.INFO, __file__, 1,
        '%(r)s %(s)s', ({'r': f'GET {path} HTTP/1.1', 's': 200},), None)
    for record in (app_record, access_record):
        line = formatter.format(record)
        assert token not in line
        assert '/api/client/v1/calendar/[redacted]/dates.ics' in line


def test_calendar_credentials_are_redacted_from_exception_text():
    import logging
    import sys
    from app import _PrivateCalendarFormatter

    token = 'private-calendar-credential'
    try:
        raise RuntimeError(f'Unable to load /api/client/v1/calendar/{token}/dates.ics')
    except RuntimeError:
        record = logging.LogRecord('app', logging.ERROR, __file__, 1,
            'Calendar request failed', (), sys.exc_info())
    line = _PrivateCalendarFormatter(logging.Formatter('%(message)s')).format(record)
    assert token not in line
    assert '/api/client/v1/calendar/[redacted]/dates.ics' in line


def test_calendar_controls_revoke_links_without_ending_session_or_alerts(app, seed, monkeypatch):
    calls = []
    monkeypatch.setattr('services.device_push.enqueue_date_push', lambda **kw: calls.append(kw))
    client = app.test_client()
    with app.app_context():
        access, milestone = _setup(seed)
        code, aid, mid, session_version = access.invite_code, access.id, milestone.id, access.session_version
        other, _ = _setup(seed)
        other_path = '/api/client/v1/calendar/' + calendar_token(other) + '/dates.ics'
    calls.clear()
    headers = _auth_headers(_open_session(client, code).json['token'])
    endpoint = '/api/client/v1/calendar-subscription'
    initial = client.get(endpoint, headers=headers)
    assert initial.json == {'status': 'not_started', 'version': 1, 'url': None}
    assert initial.headers['Cache-Control'] == 'no-store'
    enabled = client.post(endpoint, headers=headers)
    assert enabled.json['status'] == 'link_enabled' and enabled.json['version'] == 1
    path = urlsplit(enabled.json['url']).path
    assert client.get(path).status_code == 200
    assert client.post(endpoint, headers=headers).json == enabled.json
    assert client.get(endpoint, headers=headers).json == {
        'status': 'link_enabled', 'version': 1, 'url': None,
    }

    disabled = client.delete(endpoint, headers=headers)
    assert disabled.json == {'status': 'disabled', 'version': 2, 'url': None}
    assert client.delete(endpoint, headers=headers).json == disabled.json
    assert client.get(endpoint, headers=headers).json == disabled.json
    assert client.get(path).status_code == 404
    assert client.get(other_path).status_code == 200
    assert client.get('/api/client/v1/deal', headers=headers).status_code == 200
    assert calls == []
    with app.app_context():
        access = db.session.get(ClientPortalAccess, aid)
        assert access.session_version == session_version
        db.session.get(SellerContractMilestone, mid).due_at = datetime(2026, 12, 12)
        db.session.commit()
    assert len(calls) == 1 and calls[0]['changed'] == 1

    reenabled = client.post(endpoint, headers=headers)
    assert reenabled.json['status'] == 'link_enabled' and reenabled.json['version'] == 2
    new_path = urlsplit(reenabled.json['url']).path
    assert new_path != path
    assert client.get(new_path).status_code == 200
    assert client.get(path).status_code == 404
    assert client.delete(endpoint, headers=headers).json['version'] == 3
    assert client.get(new_path).status_code == 404


def test_legacy_calendar_links_survive_migration_until_disabled(app, seed):
    from services.client_calendar import _signer
    client = app.test_client()
    with app.app_context():
        access, _ = _setup(seed)
        code, aid = access.invite_code, access.id
        token = _signer().dumps({'aid': access.id, 'oid': access.organization_id,
            'pid': access.participant_id, 'tid': access.transaction_id,
            'sv': access.session_version or 1})
    path = '/api/client/v1/calendar/' + token + '/dates.ics'
    assert client.get(path).status_code == 200
    with app.app_context():
        assert db.session.get(ClientPortalAccess, aid).calendar_issued_at is None
    headers = _auth_headers(_open_session(client, code).json['token'])
    endpoint = '/api/client/v1/calendar-subscription'
    assert client.post(endpoint, headers=headers).json['version'] == 1
    assert client.get(path).status_code == 200
    client.delete(endpoint, headers=headers)
    assert client.get(path).status_code == 404
    client.post(endpoint, headers=headers)
    assert client.get(path).status_code == 404


@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
def test_calendar_controls_require_client_auth(app, seed, method):
    client = app.test_client()
    endpoint = '/api/client/v1/calendar-subscription'
    assert client.open(endpoint, method=method).status_code == 401
    assert client.open(endpoint, method=method, headers=_auth_headers('invalid')).status_code == 401
    with app.app_context():
        access, _ = _setup(seed)
        feed_token = calendar_token(access)
    assert client.open(endpoint, method=method, headers=_auth_headers(feed_token)).status_code == 401


def test_calendar_claim_rejects_invalid_version(app, seed):
    from services.client_calendar import _signer, calendar_claims
    with app.app_context():
        access, _ = _setup(seed)
        claims = calendar_claims(calendar_token(access))
        for version in (None, True, '1', 0, -1):
            assert calendar_claims(_signer().dumps(dict(claims, cv=version))) is None


def test_disabling_before_first_setup_is_persistent_and_idempotent(app, seed):
    client = app.test_client()
    with app.app_context():
        access, _ = _setup(seed)
        code = access.invite_code
    headers = _auth_headers(_open_session(client, code).json['token'])
    endpoint = '/api/client/v1/calendar-subscription'
    assert client.delete(endpoint, headers=headers).json == {
        'status': 'disabled', 'version': 2, 'url': None,
    }
    assert client.delete(endpoint, headers=headers).json['version'] == 2
    assert client.post(endpoint, headers=headers).json['status'] == 'link_enabled'


@pytest.mark.parametrize('invalidate', ['bump_session', 'rotate_invite', 'revoke'])
def test_grant_session_changes_invalidate_calendar_confirmation(app, seed, invalidate):
    from services.client_calendar import calendar_subscription_status
    with app.app_context():
        access, _ = _setup(seed)
        access.calendar_issued_at = datetime.utcnow()
        version = access.calendar_version
        getattr(access, invalidate)()
        assert access.calendar_version == version + 1
        assert calendar_subscription_status(access) == 'not_started'
        access.calendar_disabled_at = datetime.utcnow()
        access.calendar_issued_at = datetime.utcnow()
        getattr(access, invalidate)()
        assert calendar_subscription_status(access) == 'disabled'


@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
def test_calendar_controls_reject_revoked_session(app, seed, method):
    client = app.test_client()
    with app.app_context():
        access, _ = _setup(seed)
        code, aid = access.invite_code, access.id
    headers = _auth_headers(_open_session(client, code).json['token'])
    with app.app_context():
        db.session.get(ClientPortalAccess, aid).revoke()
        db.session.commit()
    assert client.open('/api/client/v1/calendar-subscription', method=method, headers=headers).status_code == 401
