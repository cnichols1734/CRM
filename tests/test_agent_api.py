"""Agent iPhone API: password JWT, CRM resources, portal messages."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from models import (
    ClientPortalAccess,
    DeviceToken,
    PortalMessage,
    SellerAcceptedContract,
    Task,
    Transaction,
    TransactionParticipant,
    db,
)


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _agent_session(client, *, email=None, username=None, password='password123'):
    body = {'password': password}
    if email is not None:
        body['email'] = email
    if username is not None:
        body['username'] = username
    return client.post(
        '/api/agent/v1/session',
        json=body,
        content_type='application/json',
    )


def _owner_token(client):
    resp = _agent_session(client, email='owner_a@test.com')
    assert resp.status_code == 200
    return resp.get_json()['token']


def _seller_thread(seed, address='400 Agent Api Ln'):
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
    access = ClientPortalAccess(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        participant_id=seller.id,
        token=ClientPortalAccess.generate_token(),
        invite_code=ClientPortalAccess.generate_invite_code(),
        session_version=1,
        is_active=True,
    )
    db.session.add(access)
    db.session.flush()
    return tx, seller, access


def test_session_email_and_username(app, seed, client):
    by_email = _agent_session(client, email='owner_a@test.com')
    assert by_email.status_code == 200
    body = by_email.get_json()
    assert body['token']
    assert body['token_type'] == 'Bearer'
    assert body['expires_in'] == 30 * 24 * 60 * 60
    assert body['user']['email'] == 'owner_a@test.com'

    by_username = _agent_session(client, username='owner_a')
    assert by_username.status_code == 200
    assert by_username.get_json()['token']


def test_session_bad_creds_401(app, seed, client):
    resp = _agent_session(client, email='owner_a@test.com', password='nope')
    assert resp.status_code == 401
    assert 'token' not in (resp.get_json() or {})


def test_cookie_is_not_enough(app, seed, owner_a_client):
    resp = owner_a_client.get('/api/agent/v1/me')
    assert resp.status_code == 401
    assert resp.content_type.startswith('application/json')


def test_rejects_client_portal_jwt(app, seed, client):
    from services.client_portal_auth import issue_client_jwt

    with app.app_context():
        tx, seller, access = _seller_thread(seed, '401 Client Jwt Ln')
        db.session.commit()
        client_token = issue_client_jwt(access)

    resp = client.get('/api/agent/v1/me', headers=_auth(client_token))
    assert resp.status_code == 401


def test_me_and_dashboard_shape(app, seed, client):
    token = _owner_token(client)
    headers = _auth(token)

    me = client.get('/api/agent/v1/me', headers=headers)
    assert me.status_code == 200
    payload = me.get_json()
    assert set(payload.keys()) == {'user', 'org', 'features'}
    assert payload['user']['username'] == 'owner_a'
    assert payload['org']['name'] == 'Test Realty A'
    assert 'TRANSACTIONS' in payload['features']

    dash = client.get('/api/agent/v1/dashboard', headers=headers)
    assert dash.status_code == 200
    body = dash.get_json()
    assert set(body.keys()) == {
        'user', 'kpis', 'today_tasks', 'pipeline_by_status',
        'group_stats', 'features',
    }
    assert 'total_contacts' in body['kpis']
    assert isinstance(body['today_tasks'], list)
    assert isinstance(body['pipeline_by_status'], list)
    assert isinstance(body['group_stats'], list)


def test_session_revoke_bumps_sv(app, seed, client):
    token = _owner_token(client)
    headers = _auth(token)
    assert client.get('/api/agent/v1/me', headers=headers).status_code == 200
    left = client.delete('/api/agent/v1/session', headers=headers)
    assert left.status_code == 200
    assert client.get('/api/agent/v1/me', headers=headers).status_code == 401
    again = _owner_token(client)
    assert client.get('/api/agent/v1/me', headers=_auth(again)).status_code == 200


def test_contacts_crud_and_force_delete(app, seed, client):
    token = _owner_token(client)
    headers = _auth(token)

    missing = client.post(
        '/api/agent/v1/contacts',
        headers=headers,
        json={'first_name': 'Only'},
    )
    assert missing.status_code == 400

    created = client.post(
        '/api/agent/v1/contacts',
        headers=headers,
        json={
            'first_name': 'Pat',
            'last_name': 'Nguyen',
            'email': 'pat-agent-api@test.com',
            'group_ids': [seed['group_a1']],
        },
    )
    assert created.status_code == 201
    contact_id = created.get_json()['contact']['id']

    listed = client.get(
        '/api/agent/v1/contacts?q=Nguyen',
        headers=headers,
    )
    assert listed.status_code == 200
    names = [c['last_name'] for c in listed.get_json()['contacts']]
    assert 'Nguyen' in names

    patched = client.patch(
        f'/api/agent/v1/contacts/{contact_id}',
        headers=headers,
        json={'phone': '5550001111'},
    )
    assert patched.status_code == 200
    assert patched.get_json()['contact']['phone'] == '5550001111'

    with app.app_context():
        db.session.add(Task(
            organization_id=seed['org_a'],
            contact_id=contact_id,
            assigned_to_id=seed['owner_a'],
            created_by_id=seed['owner_a'],
            type_id=seed['task_type_a'],
            subtype_id=seed['subtype_a'],
            subject='Keep this contact',
            priority='low',
            status='pending',
            due_date=datetime.utcnow() + timedelta(days=2),
        ))
        db.session.commit()

    blocked = client.delete(
        f'/api/agent/v1/contacts/{contact_id}',
        headers=headers,
    )
    assert blocked.status_code == 400
    assert blocked.get_json()['has_associated_data'] is True

    forced = client.delete(
        f'/api/agent/v1/contacts/{contact_id}?force=true',
        headers=headers,
    )
    assert forced.status_code == 200
    gone = client.get(f'/api/agent/v1/contacts/{contact_id}', headers=headers)
    assert gone.status_code == 404


def test_transactions_flag_403_json(app, seed, client):
    token = _agent_session(client, email='owner_b@test.com').get_json()['token']
    resp = client.get('/api/agent/v1/transactions', headers=_auth(token))
    assert resp.status_code == 403
    assert resp.content_type.startswith('application/json')
    assert resp.get_json()['code'] == 'transactions_required'
    assert resp.get_json().get('error')


def test_dates_allowlist(app, seed, client):
    token = _owner_token(client)
    headers = _auth(token)

    with app.app_context():
        tx, seller, _access = _seller_thread(seed, '402 Dates Ln')
        contract = SellerAcceptedContract(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            position='primary',
            status='active',
            accepted_price=350000,
            effective_date=date(2026, 4, 1),
            closing_date=date(2026, 5, 15),
        )
        db.session.add(contract)
        db.session.commit()
        tx_id = tx.id

    rejected = client.patch(
        f'/api/agent/v1/transactions/{tx_id}/dates',
        headers=headers,
        json={'option_period_days': 10, 'expected_close_date': '2026-06-01'},
    )
    assert rejected.status_code == 400

    updated = client.patch(
        f'/api/agent/v1/transactions/{tx_id}/dates',
        headers=headers,
        json={
            'expected_close_date': '2026-06-01',
            'actual_close_date': '2026-06-02',
            'go_live_date': '2026-03-15',
            'effective_date': '2026-04-02',
            'closing_date': '2026-05-20',
        },
    )
    assert updated.status_code == 200
    dates = updated.get_json()['dates']
    assert dates['expected_close_date'] == '2026-06-01'
    assert dates['go_live_date'] == '2026-03-15'
    assert dates['effective_date'] == '2026-04-02'
    assert dates['closing_date'] == '2026-05-20'
    assert dates['actual_close_date'] == '2026-06-02'


def test_conversations_include_empty_threads(app, seed, client):
    token = _owner_token(client)
    with app.app_context():
        tx, seller, _access = _seller_thread(seed, '403 Empty Thread Ln')
        db.session.commit()
        participant_id = seller.id
        assert PortalMessage.query.filter_by(participant_id=participant_id).count() == 0

    listed = client.get('/api/agent/v1/conversations', headers=_auth(token))
    assert listed.status_code == 200
    rows = listed.get_json()['conversations']
    ids = [row['participant_id'] for row in rows]
    assert participant_id in ids
    empty = next(row for row in rows if row['participant_id'] == participant_id)
    assert empty['last_preview'] is None
    assert empty['unread'] == 0
    assert empty['address'] == '403 Empty Thread Ln'


def test_agent_message_visible_on_client_list(app, seed, client):
    from tests.test_client_portal_api import _auth_headers, _open_session

    with app.app_context():
        tx, seller, access = _seller_thread(seed, '404 Cross Talk Ln')
        db.session.commit()
        participant_id = seller.id
        invite = access.invite_code

    agent_token = _owner_token(client)
    posted = client.post(
        f'/api/agent/v1/conversations/{participant_id}/messages',
        headers=_auth(agent_token),
        json={'body': 'Inspection is booked for Thursday.'},
    )
    assert posted.status_code == 201
    assert posted.get_json()['message']['sender'] == 'agent'
    assert posted.get_json()['message']['kind'] == 'message'

    client_token = _open_session(client, invite).get_json()['token']
    listed = client.get(
        '/api/client/v1/messages',
        headers=_auth_headers(client_token),
    )
    assert listed.status_code == 200
    bodies = [m['body'] for m in listed.get_json()['messages']]
    assert 'Inspection is booked for Thursday.' in bodies
    kinds = [m['kind'] for m in listed.get_json()['messages']]
    assert 'message' in kinds


def test_devices_register(app, seed, client):
    token = _owner_token(client)
    resp = client.post(
        '/api/agent/v1/devices',
        headers=_auth(token),
        json={'token': 'apns-agent-token-1', 'platform': 'ios'},
    )
    assert resp.status_code == 201
    assert resp.get_json()['ok'] is True
    with app.app_context():
        row = DeviceToken.query.filter_by(token='apns-agent-token-1').first()
        assert row is not None
        assert row.audience == DeviceToken.AUDIENCE_AGENT
        assert row.user_id == seed['owner_a']


def test_apns_noop_when_env_missing(app, seed, client, monkeypatch):
    from jobs.apns_push import apns_configured, send_portal_push
    from services.device_push import enqueue_portal_push

    for key in ('APNS_KEY_ID', 'APNS_TEAM_ID', 'APNS_KEY', 'APNS_BUNDLE_ID'):
        monkeypatch.delenv(key, raising=False)

    assert apns_configured() is False
    result = send_portal_push(message_id=1, org_id=seed['org_a'])
    assert result['reason'] == 'apns_unconfigured'

    token = _owner_token(client)
    with app.app_context():
        tx, seller, _access = _seller_thread(seed, '405 Push Ln')
        db.session.commit()
        participant_id = seller.id

    posted = client.post(
        f'/api/agent/v1/conversations/{participant_id}/messages',
        headers=_auth(token),
        json={'body': 'See you at the house.'},
    )
    assert posted.status_code == 201
    with app.app_context():
        msg = PortalMessage.query.filter_by(
            participant_id=participant_id, sender='agent',
        ).order_by(PortalMessage.id.desc()).first()
        skipped = enqueue_portal_push(msg)
        assert skipped['reason'] == 'apns_unconfigured'


def test_jwt_user_not_cookie_user(app, seed, owner_a_client):
    agent_token = _agent_session(owner_a_client, email='agent_a@test.com').get_json()['token']
    listed = owner_a_client.get(
        '/api/agent/v1/contacts',
        headers=_auth(agent_token),
    )
    assert listed.status_code == 200
    emails = [c['email'] for c in listed.get_json()['contacts']]
    assert 'john@test.com' in emails
    assert 'jane@test.com' not in emails
