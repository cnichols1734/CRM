import uuid
import pytest
from models import (db, Organization, ClientBrowseAccount, ClientBrowseInquiry,
                    ClientBrowseRateLimit, Contact, Interaction, UserTodo)

BASE = '/api/client/v1/discovery'


@pytest.fixture(autouse=True)
def enable_discovery(app, seed):
    with app.app_context():
        for oid in (seed['org_a'], seed['org_b']):
            db.session.get(Organization, oid).client_app_settings = {'enabled': True}
        ClientBrowseRateLimit.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        for oid in (seed['org_a'], seed['org_b']):
            db.session.get(Organization, oid).client_app_settings = None
        db.session.commit()


def signup(client, slug='test-realty-a', email=None, **extra):
    return client.post(BASE + '/accounts', json=dict(brokerage=slug,
        name='Test Browser', email=email or f'{uuid.uuid4()}@example.com', password='test-password-123', **extra))


def auth(response):
    return {'Authorization': 'Bearer ' + response.get_json()['token']}


def test_public_brand_and_agent_are_tenant_scoped(app, seed, client):
    response = client.get(BASE + '/brokerages/test-realty-a')
    assert response.status_code == 200
    assert response.json['branding']['slug'] == 'test-realty-a'
    assert response.json['agent']['id'] == seed['owner_a']
    assert 'email' not in response.json['agent']
    assert client.get(BASE + f"/brokerages/test-realty-a?agent={seed['owner_b']}").status_code == 404
    with app.app_context():
        db.session.get(Organization, seed['org_b']).client_app_settings = {'enabled': False}
        db.session.commit()
    assert client.get(BASE + '/brokerages/test-realty-b').status_code == 404


def test_account_is_not_a_deal_grant(client):
    response = signup(client)
    assert response.status_code == 201
    headers = auth(response)
    assert client.get('/api/client/v1/deal', headers=headers).status_code == 401
    assert client.get('/api/agent/v1/me', headers=headers).status_code == 401
    assert client.get(BASE + '/account', headers=headers).json['account']['saved_ids'] == []
    assert client.get(BASE + '/account').status_code == 401


def test_saved_homes_delta_merge_removal_and_reload(client):
    headers = auth(signup(client))
    first = client.put(BASE + '/saved', headers=headers, json={'changes': {'h01': True}})
    assert first.json['account']['saved_ids'] == ['h01']
    client.put(BASE + '/saved', headers=headers, json={'changes': {'h02': True}})
    assert set(client.get(BASE + '/account', headers=headers).json['account']['saved_ids']) == {'h01', 'h02'}
    removed = client.put(BASE + '/saved', headers=headers, json={'changes': {'h01': False}})
    assert removed.json['account']['saved_ids'] == ['h02']
    assert client.put(BASE + '/saved', headers=headers, json={'changes': {'unknown': True}}).status_code == 400
    assert client.put(BASE + '/saved', headers=headers, json={'changes': {'h01': 'yes'}}).status_code == 400


def test_same_email_never_shares_accounts_across_brokerages(client):
    email = f'{uuid.uuid4()}@example.com'
    a, b = signup(client, email=email), signup(client, slug='test-realty-b', email=email)
    assert a.status_code == b.status_code == 201
    client.put(BASE + '/saved', headers=auth(a), json={'changes': {'h01': True}})
    assert client.get(BASE + '/account', headers=auth(b)).json['account']['saved_ids'] == []
    assert client.post(BASE + '/session', json={'brokerage': 'test-realty-a', 'email': email, 'password': 'wrong-password'}).status_code == 401
    login = client.post(BASE + '/session', json={'brokerage': 'test-realty-a', 'email': email, 'password': 'test-password-123'})
    assert login.status_code == 200
    assert login.json['account']['saved_ids'] == ['h01']


def inquiry_body(**extra):
    return dict(kind='showing', listing_id='h01', body='Test showing inquiry', consent=True, request_id=str(uuid.uuid4()), **extra)


def test_inquiry_creates_correct_crm_contact_followup_and_is_idempotent(app, seed, client):
    registered = signup(client, agent_id=seed['agent_a'])
    headers = auth(registered)
    body = inquiry_body(phone='5551234567')
    first = client.post(BASE + '/inquiries', headers=headers, json=body)
    again = client.post(BASE + '/inquiries', headers=headers, json=body)
    assert first.status_code == 201
    assert again.status_code == 200 and first.json == again.json
    with app.app_context():
        row = db.session.get(ClientBrowseInquiry, first.json['id'])
        assert row.organization_id == seed['org_a'] and row.agent_id == seed['agent_a']
        account = db.session.get(ClientBrowseAccount, registered.json['account']['id'])
        contact = db.session.get(Contact, account.contact_id)
        assert contact.user_id == seed['agent_a'] and contact.organization_id == seed['org_a']
        assert contact.phone == '5551234567'
        assert Interaction.query.filter_by(contact_id=contact.id).count() == 1
        assert UserTodo.query.filter_by(user_id=seed['agent_a']).filter(UserTodo.text.contains(f'Contact #{contact.id}')).count() == 1
    other = signup(client)
    assert client.get(BASE + '/inquiries', headers=auth(other)).json['inquiries'] == []


def test_cannot_choose_agent_from_other_tenant(seed, client):
    assert signup(client, agent_id=seed['owner_b']).status_code == 400


def test_owner_reply_visible_only_to_requesting_account(client, owner_a_client, owner_b_client):
    registered = signup(client)
    row = client.post(BASE + '/inquiries', headers=auth(registered), json=inquiry_body()).json
    assert owner_b_client.post('/org/client-inquiries', data={'inquiry_id': row['id'], 'reply': 'Wrong tenant'}).status_code == 404
    assert owner_a_client.post('/org/client-inquiries', data={'inquiry_id': row['id'], 'reply': 'Forged'}).status_code == 400
    owner_a_client.get('/org/client-inquiries')
    with owner_a_client.session_transaction() as session:
        csrf_token = session['client_inquiry_csrf']
    assert owner_a_client.post('/org/client-inquiries', data={'inquiry_id': row['id'], 'reply': 'We can help.', 'csrf_token': csrf_token}).status_code == 302
    assert client.get(BASE + '/inquiries', headers=auth(registered)).json['inquiries'][0]['reply'] == 'We can help.'
    assert owner_a_client.get('/org/client-inquiries').status_code == 200


def test_sign_out_revokes_token_and_delete_requires_password(client):
    registered = signup(client)
    headers = auth(registered)
    assert client.delete(BASE + '/account', headers=headers, json={'password': 'wrong'}).status_code == 403
    assert client.delete(BASE + '/session', headers=headers).status_code == 200
    assert client.get(BASE + '/account', headers=headers).status_code == 401
    other = signup(client)
    assert client.delete(BASE + '/account', headers=auth(other), json={'password': 'test-password-123'}).status_code == 200
    assert client.get(BASE + '/account', headers=auth(other)).status_code == 401


def test_malformed_bodies_and_missing_consent(client):
    assert client.post(BASE + '/accounts', json=[]).status_code == 404
    registered = signup(client)
    body = inquiry_body()
    body['consent'] = False
    assert client.post(BASE + '/inquiries', headers=auth(registered), json=body).status_code == 400
    body['consent'] = True
    body['request_id'] = 'bad'
    assert client.post(BASE + '/inquiries', headers=auth(registered), json=body).status_code == 400


def test_rate_limit_applies_to_login_attempts(client):
    for _ in range(30):
        assert client.post(BASE + '/session', json={}).status_code == 404
    assert client.post(BASE + '/session', json={}).status_code == 429


def test_deal_connection_requires_explicit_same_tenant_grant(app, seed, client):
    from test_client_portal_api import _seller_tx, _participant, _grant, _open_session
    with app.app_context():
        tx = _seller_tx(seed)
        tx.created_by_id = seed['agent_a']
        participant = _participant(seed, tx, contact_id=seed['contact_a'])
        grant = _grant(seed, tx, participant)
        db.session.commit()
        code = grant.invite_code
    token = _open_session(client, code).json['token']
    a = signup(client)
    b = signup(client, slug='test-realty-b')
    assert client.post(BASE + '/connection', headers=auth(b), json={'deal_token': token}).status_code == 403
    assert client.post(BASE + '/connection', headers=auth(a), json={'deal_token': a.json['token']}).status_code == 403
    connected = client.post(BASE + '/connection', headers=auth(a), json={'deal_token': token})
    assert connected.status_code == 200
    assert connected.json['agent']['id'] == seed['agent_a']
    assert client.get('/api/client/v1/deal', headers=auth(a)).status_code == 401


def verified_account(app, client, monkeypatch, email):
    codes = []
    monkeypatch.setattr('routes.client_discovery.send_verification_email', lambda account, code, org: codes.append(code) or True)
    response = signup(client, email=email)
    headers = auth(response)
    assert client.post(BASE + '/email/verification', headers=headers).status_code == 200
    assert client.post(BASE + '/email/verify', headers=headers, json={'code': codes[-1]}).status_code == 200
    return response, headers


def test_verified_email_matches_only_client_participants_and_revokes(app, seed, client, monkeypatch):
    from test_client_portal_api import _seller_tx, _participant
    from models import ClientPortalAccess
    email = f'match-{uuid.uuid4()}@example.com'
    with app.app_context():
        tx = _seller_tx(seed)
        p = _participant(seed, tx, email=email.upper(), name='Verified Client')
        other = _participant(seed, tx, role='listing_agent', email=email)
        db.session.commit()
        pid = p.id
    registered = signup(client, email=email)
    headers = auth(registered)
    assert client.get(BASE + '/deals', headers=headers).json['deals'] == []
    assert client.post(BASE + f'/deals/{pid}/session', headers=headers).status_code == 404
    codes = []
    monkeypatch.setattr('routes.client_discovery.send_verification_email', lambda a, code, o: codes.append(code) or True)
    client.post(BASE + '/email/verification', headers=headers)
    verified = client.post(BASE + '/email/verify', headers=headers, json={'code': codes[-1]})
    assert verified.json['account']['email_verified'] is True
    assert [d['id'] for d in client.get(BASE + '/deals', headers=headers).json['deals']] == [pid]
    opened = client.post(BASE + f'/deals/{pid}/session', headers=headers)
    token = opened.json['deal_session']['token']
    assert client.get('/api/client/v1/deal', headers={'Authorization': 'Bearer ' + token}).status_code == 200
    b = signup(client, slug='test-realty-b', email=email)
    assert client.post(BASE + f'/deals/{pid}/session', headers=auth(b)).status_code == 404
    with app.app_context():
        grant = ClientPortalAccess.query.filter_by(participant_id=pid).one()
        grant.revoke()
        db.session.commit()
    assert client.get(BASE + '/deals', headers=headers).json['deals'] == []
    assert client.post(BASE + f'/deals/{pid}/session', headers=headers).status_code == 404
    assert client.get('/api/client/v1/deal', headers={'Authorization': 'Bearer ' + token}).status_code == 401


def test_verification_expiration_attempt_limit_and_mail_failure(app, client, monkeypatch):
    import time
    headers = auth(signup(client))
    monkeypatch.setattr('routes.client_discovery.send_verification_email', lambda *a: False)
    assert client.post(BASE + '/email/verification', headers=headers).status_code == 503
    assert client.post(BASE + '/email/verify', headers=headers, json={'code': '123456'}).status_code == 422
    codes = []
    monkeypatch.setattr('routes.client_discovery.send_verification_email', lambda a, code, o: codes.append(code) or True)
    client.post(BASE + '/email/verification', headers=headers)
    wrong = '000000' if codes[-1] != '000000' else '111111'
    for _ in range(5):
        assert client.post(BASE + '/email/verify', headers=headers, json={'code': wrong}).status_code == 422
    assert client.post(BASE + '/email/verify', headers=headers, json={'code': codes[-1]}).status_code == 422
    client.post(BASE + '/email/verification', headers=headers)
    with app.app_context():
        a = ClientBrowseAccount.query.order_by(ClientBrowseAccount.id.desc()).first()
        a.verification_expires_at = int(time.time()) - 1
        db.session.commit()
    assert client.post(BASE + '/email/verify', headers=headers, json={'code': codes[-1]}).status_code == 422


def test_email_match_uses_crm_contact_and_signout_invalidates_deal(app, seed, client, monkeypatch):
    from test_client_portal_api import _seller_tx, _participant
    from models import Contact
    email = f'contact-{uuid.uuid4()}@example.com'
    with app.app_context():
        contact = Contact(organization_id=seed['org_a'], user_id=seed['agent_a'], created_by_id=seed['agent_a'], first_name='Contact', last_name='Test', email=email)
        db.session.add(contact); db.session.flush()
        p = _participant(seed, _seller_tx(seed), contact_id=contact.id, email='different@example.com')
        db.session.commit()
        cid, pid = contact.id, p.id
    registered, headers = verified_account(app, client, monkeypatch, email)
    assert client.get(BASE + '/account', headers=headers).json['account']['agent_id'] == seed['agent_a']
    token = client.post(BASE + f'/deals/{pid}/session', headers=headers).json['deal_session']['token']
    with app.app_context():
        assert db.session.get(ClientBrowseAccount, registered.json['account']['id']).contact_id == cid
    client.delete(BASE + '/session', headers=headers)
    assert client.get('/api/client/v1/deal', headers={'Authorization': 'Bearer ' + token}).status_code == 401


def test_code_fallback_persists_different_email_connection(app, seed, client):
    from test_client_portal_api import _seller_tx, _participant, _grant, _open_session
    with app.app_context():
        tx = _seller_tx(seed)
        p = _participant(seed, tx, email='another@example.com')
        g = _grant(seed, tx, p)
        db.session.commit()
        pid, code = p.id, g.invite_code
    response = signup(client)
    headers = auth(response)
    token = _open_session(client, code).json['token']
    assert client.post(BASE + '/connection', headers=headers, json={'deal_token': token}).status_code == 200
    assert [d['id'] for d in client.get(BASE + '/deals', headers=headers).json['deals']] == [pid]
    token = client.post(BASE + f'/deals/{pid}/session', headers=headers).json['deal_session']['token']
    assert client.get('/api/client/v1/deal', headers={'Authorization': 'Bearer ' + token}).status_code == 200
    assert client.delete(BASE + '/account', headers=headers, json={'password': 'test-password-123'}).status_code == 200
    assert client.get('/api/client/v1/deal', headers={'Authorization': 'Bearer ' + token}).status_code == 401


def test_long_names_fit_contact_columns_and_deal_moves_app_contact(app, seed, client):
    from test_client_portal_api import _seller_tx, _participant, _grant, _open_session
    response = client.post(BASE + '/accounts', json={'brokerage': 'test-realty-a', 'email': f'{uuid.uuid4()}@example.com', 'password': 'test-password-123', 'name': 'N' * 160})
    headers = auth(response)
    assert client.post(BASE + '/inquiries', headers=headers, json=inquiry_body()).status_code == 201
    with app.app_context():
        a = db.session.get(ClientBrowseAccount, response.json['account']['id'])
        cid = a.contact_id
        c = db.session.get(Contact, cid)
        assert len(c.first_name) == 80 and len(c.last_name) <= 80
        tx = _seller_tx(seed)
        tx.created_by_id = seed['agent_a']
        g = _grant(seed, tx, _participant(seed, tx, email='different@example.com'))
        db.session.commit()
        code = g.invite_code
    token = _open_session(client, code).json['token']
    assert client.post(BASE + '/connection', headers=headers, json={'deal_token': token}).status_code == 200
    with app.app_context():
        assert db.session.get(Contact, cid).user_id == seed['agent_a']


def test_discovery_foreign_key_lifecycle_enforced():
    from sqlalchemy import create_engine, text, MetaData, Table, Column, Integer
    from models import ClientBrowseAccount, ClientBrowseInquiry
    engine = create_engine('sqlite://')
    metadata = MetaData()
    for name in ('organizations', 'user', 'contact'):
        Table(name, metadata, Column('id', Integer, primary_key=True))
    ClientBrowseAccount.__table__.to_metadata(metadata)
    ClientBrowseInquiry.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text('PRAGMA foreign_keys=ON'))
        for name in ('organizations', 'user', 'contact'):
            conn.execute(text(f'INSERT INTO "{name}" (id) VALUES (1)'))
        conn.execute(text("INSERT INTO client_browse_accounts (id,organization_id,email,name,password_hash,session_version,saved_ids,linked_access_ids,owns_contact,verification_attempts,agent_id,contact_id,created_at) VALUES (1,1,'a@b.com','Test','hash',1,'[]','[]',0,0,1,1,CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO client_browse_inquiries (id,organization_id,account_id,agent_id,request_id,kind,body,created_at) VALUES (1,1,1,1,'uuid','question','test',CURRENT_TIMESTAMP)"))
        conn.execute(text('DELETE FROM contact WHERE id=1'))
        conn.execute(text('DELETE FROM "user" WHERE id=1'))
        assert conn.execute(text('SELECT contact_id,agent_id FROM client_browse_accounts')).one() == (None, None)
        assert conn.execute(text('SELECT agent_id FROM client_browse_inquiries')).scalar() is None
        conn.execute(text('DELETE FROM organizations WHERE id=1'))
        assert conn.execute(text('SELECT count(*) FROM client_browse_accounts')).scalar() == 0
        assert conn.execute(text('SELECT count(*) FROM client_browse_inquiries')).scalar() == 0


def test_auth_rate_limits_use_railway_client_ip_not_shared_proxy(client, monkeypatch):
    monkeypatch.setenv('RAILWAY_ENVIRONMENT_ID', 'test-production')
    for _ in range(30):
        assert client.post(BASE + '/session', json={}, headers={'X-Real-IP': '192.0.2.1'}).status_code == 404
    assert client.post(BASE + '/session', json={}, headers={'X-Real-IP': '192.0.2.1'}).status_code == 429
    assert client.post(BASE + '/session', json={}, headers={'X-Real-IP': '192.0.2.2'}).status_code == 404


def test_forwarded_ip_is_ignored_off_railway(app, monkeypatch):
    from routes.client_discovery import client_ip
    monkeypatch.delenv('RAILWAY_ENVIRONMENT_ID', raising=False)
    with app.test_request_context(headers={'X-Real-IP': '192.0.2.1'}, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        assert client_ip() == '127.0.0.1'
    monkeypatch.setenv('RAILWAY_ENVIRONMENT_ID', 'test-production')
    with app.test_request_context(headers={'X-Real-IP': 'invalid'}, environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        assert client_ip() == '127.0.0.1'
