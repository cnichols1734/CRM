import uuid
from datetime import datetime, timedelta

import pytest

from models import db, ClientBrowseAccount, ClientBrowseInquiry, ClientInquiryMessage, PortalMessage, TransactionAssignment
from tests.test_agent_api import _seller_thread
from tests.test_client_portal_api import _auth_headers, _open_session


@pytest.fixture
def threads(app, seed):
    with app.app_context():
        tx, participant, access = _seller_thread(seed, '821 Unified Inbox Lane')
        account = ClientBrowseAccount(organization_id=seed['org_a'],
            name='Inbox Buyer', email=f'{uuid.uuid4()}@example.invalid', password_hash='unused',
            agent_id=seed['agent_a'])
        db.session.add(account)
        db.session.flush()
        inquiry = ClientBrowseInquiry(organization_id=seed['org_a'], account_id=account.id,
            agent_id=seed['agent_a'], request_id=str(uuid.uuid4()), kind='showing',
            listing_id='h01', body='Could we tour this home on Saturday?')
        msg = PortalMessage(organization_id=seed['org_a'], transaction_id=tx.id,
            participant_id=participant.id, sender='client', kind='message', body='Inspection question')
        db.session.add_all([inquiry, msg])
        db.session.commit()
        return dict(inquiry=inquiry.id, participant=participant.id, tx=tx.id,
                    message=msg.id, invite=access.invite_code)


def _csrf(client):
    with client.session_transaction() as session:
        return session['client_inquiry_csrf']


def test_combined_inbox_and_filters(owner_a_client, threads):
    page = owner_a_client.get('/messages')
    assert page.status_code == 200
    assert b'Inbox Buyer' in page.data and b'821 Unified Inbox Lane' in page.data
    assert b'Could we tour' in page.data and b'Inspection question' in page.data
    assert b'Inspection question' not in owner_a_client.get('/messages?view=inquiry').data
    assert b'Could we tour' not in owner_a_client.get('/messages?view=deal').data
    assert b'821 Unified Inbox Lane' not in owner_a_client.get('/messages?q=Inbox+Buyer').data
    assert owner_a_client.get('/org/client-inquiries').location.endswith('/messages')


def test_inbox_tenant_and_assignment_isolation(owner_b_client, agent_a_client, threads):
    for key in (f"inquiry-{threads['inquiry']}", f"deal-{threads['participant']}"):
        assert owner_b_client.get('/messages?thread=' + key).status_code == 404
        assert owner_b_client.post('/messages?thread=' + key, data={'body': 'Forbidden'}).status_code == 404
    assert agent_a_client.get(f"/messages?thread=inquiry-{threads['inquiry']}").status_code == 200
    assert agent_a_client.get(f"/messages?thread=deal-{threads['participant']}").status_code == 404


def test_inquiry_reply_csrf_validation_and_attention(app, owner_a_client, threads):
    path = f"/messages?thread=inquiry-{threads['inquiry']}"
    assert owner_a_client.post(path, data={'body': 'Forged'}).status_code == 400
    owner_a_client.get(path)
    csrf = _csrf(owner_a_client)
    assert owner_a_client.post(path, data={'body': ' ', 'csrf_token': csrf}).status_code == 400
    assert owner_a_client.post(path, data={'body': 'x' * 4001, 'csrf_token': csrf}).status_code == 400
    sent = owner_a_client.post(path, data={'body': 'Saturday works.', 'csrf_token': csrf})
    assert sent.status_code == 302
    with app.app_context():
        assert ClientInquiryMessage.query.filter_by(inquiry_id=threads['inquiry']).one().body == 'Saturday works.'
    assert f"thread=inquiry-{threads['inquiry']}&".encode() not in owner_a_client.get('/messages?view=attention').data


def test_deal_read_and_reply_reaches_client(app, client, owner_a_client, threads, monkeypatch):
    pushed = []
    monkeypatch.setattr('routes.client_messages.enqueue_portal_push', lambda msg: pushed.append(msg.id))
    owner_a_client.get('/messages')
    with app.app_context():
        assert db.session.get(PortalMessage, threads['message']).read_by_agent_at is None
        for number in range(55):
            db.session.add(PortalMessage(organization_id=db.session.get(PortalMessage, threads['message']).organization_id,
                transaction_id=threads['tx'], participant_id=threads['participant'], sender='agent',
                kind='message', body=f'History {number}', created_at=datetime.utcnow() - timedelta(days=2)))
        db.session.commit()
    path = f"/messages?thread=deal-{threads['participant']}"
    assert owner_a_client.get(path).status_code == 200
    with app.app_context():
        assert db.session.get(PortalMessage, threads['message']).read_by_agent_at is not None
    sent = owner_a_client.post(path, data={'body': 'Your inspection is confirmed.', 'csrf_token': _csrf(owner_a_client)})
    assert sent.status_code == 302 and len(pushed) == 1
    token = _open_session(client, threads['invite']).json['token']
    messages = client.get('/api/client/v1/messages', headers=_auth_headers(token)).json['messages']
    assert len(messages) == 50
    assert messages[-1]['body'] == 'Your inspection is confirmed.'


def test_read_only_collaborator_cannot_reply(app, seed, agent_a_client, threads):
    with app.app_context():
        db.session.add(TransactionAssignment(organization_id=seed['org_a'],
            transaction_id=threads['tx'], user_id=seed['agent_a'], role='collaborator'))
        db.session.commit()
    path = f"/messages?thread=deal-{threads['participant']}"
    assert agent_a_client.get(path).status_code == 200
    assert agent_a_client.post(path, data={'body': 'Blocked', 'csrf_token': _csrf(agent_a_client)}).status_code == 403


def test_backfilled_message_does_not_replace_current_preview(app, seed, owner_a_client, threads):
    with app.app_context():
        db.session.add(PortalMessage(organization_id=seed['org_a'],
            transaction_id=threads['tx'], participant_id=threads['participant'],
            sender='agent', kind='message', body='Old backfilled message',
            created_at=datetime.utcnow() - timedelta(days=7)))
        db.session.commit()
    page = owner_a_client.get('/messages')
    assert page.status_code == 200
    assert b'Old backfilled message' not in page.data


JSON_HEADERS = {'Accept': 'application/json'}


def test_json_snapshot_preserves_html_fallback_and_is_private(owner_a_client, threads):
    path = f"/messages?thread=inquiry-{threads['inquiry']}"
    initial = owner_a_client.get(path + '&format=json')
    assert initial.status_code == 200
    data = initial.json
    assert data['ok'] is True and data['selected_key'] == f"inquiry-{threads['inquiry']}"
    assert 'Inbox Buyer' in data['threads_html']
    assert 'Could we tour this home on Saturday?' in data['conversation_html']
    assert data['can_reply'] is True and data['csrf_token'] == _csrf(owner_a_client)
    assert initial.headers['Cache-Control'] == 'private, no-store'
    assert 'Accept' in initial.headers['Vary']
    again = owner_a_client.get(path, headers=JSON_HEADERS)
    assert again.json['conversation_version'] == data['conversation_version']
    assert again.json['request_id'] != data['request_id']
    fallback = owner_a_client.get(path)
    assert fallback.status_code == 200 and fallback.mimetype == 'text/html'


def test_json_inquiry_reply_is_idempotent_and_rejects_changed_retry(app, owner_a_client, threads):
    path = f"/messages?thread=inquiry-{threads['inquiry']}&view=mine"
    owner_a_client.get(path)
    request_id = str(uuid.uuid4())
    data = {'body': 'Saturday at ten works.', 'csrf_token': _csrf(owner_a_client), 'request_id': request_id}
    first = owner_a_client.post(path, data=data, headers=JSON_HEADERS)
    assert first.status_code == 200 and first.json['ok'] is True
    assert 'Saturday at ten works.' in first.json['history_html']
    assert first.json['view'] == 'mine'
    second = owner_a_client.post(path, data=data, headers=JSON_HEADERS)
    assert second.status_code == 200
    with app.app_context():
        assert ClientInquiryMessage.query.filter_by(inquiry_id=threads['inquiry'], request_id=request_id).count() == 1
    changed = owner_a_client.post(path, data={**data, 'body': 'Different message'}, headers=JSON_HEADERS)
    assert changed.status_code == 409 and changed.json['ok'] is False


def test_json_deal_reply_retry_has_one_message_and_one_push(app, owner_a_client, threads, monkeypatch):
    from models import AuditEvent
    pushed = []
    monkeypatch.setattr('routes.client_messages.enqueue_portal_push', lambda message: pushed.append(message.id))
    path = f"/messages?thread=deal-{threads['participant']}"
    owner_a_client.get(path)
    request_id = str(uuid.uuid4())
    data = {'body': 'Inspection is booked.', 'csrf_token': _csrf(owner_a_client), 'request_id': request_id}
    first = owner_a_client.post(path, data=data, headers=JSON_HEADERS)
    second = owner_a_client.post(path, data=data, headers=JSON_HEADERS)
    assert first.status_code == second.status_code == 200
    assert len(pushed) == 1
    assert 'Inspection is booked.' in first.json['history_html']
    with app.app_context():
        assert PortalMessage.query.filter_by(participant_id=threads['participant'], body=data['body']).count() == 1
        assert AuditEvent.query.filter_by(transaction_id=threads['tx'], event_type='client_message_sent').count() == 1
    changed = owner_a_client.post(path, data={**data, 'body': 'Replacement text'}, headers=JSON_HEADERS)
    assert changed.status_code == 409


def test_json_errors_and_tenant_permissions(client, owner_a_client, owner_b_client, agent_a_client, threads):
    path = f"/messages?thread=inquiry-{threads['inquiry']}"
    expired = client.get('/messages?format=json')
    assert expired.status_code == 401 and expired.json['ok'] is False
    assert expired.headers['Cache-Control'] == 'private, no-store'
    denied = owner_b_client.get(path, headers=JSON_HEADERS)
    assert denied.status_code == 404 and denied.json['ok'] is False
    forged = owner_a_client.post(path, data={'body': 'Forged'}, headers=JSON_HEADERS)
    assert forged.status_code == 400 and forged.json['ok'] is False
    owner_a_client.get(path)
    for body in (' ', 'x' * 4001):
        invalid = owner_a_client.post(path, data={'body': body, 'csrf_token': _csrf(owner_a_client)}, headers=JSON_HEADERS)
        assert invalid.status_code == 400 and invalid.json['error'] == 'Write a message of up to 4,000 characters.'
    invalid_id = owner_a_client.post(path, data={'body': 'Valid body', 'csrf_token': _csrf(owner_a_client),
                                                'request_id': 'bad-id'}, headers=JSON_HEADERS)
    assert invalid_id.status_code == 400 and invalid_id.is_json
    deal_denied = agent_a_client.get(f"/messages?thread=deal-{threads['participant']}", headers=JSON_HEADERS)
    assert deal_denied.status_code == 404


def test_live_snapshot_sees_client_api_reply_and_only_reads_open_thread(app, client, owner_a_client, threads, seed, monkeypatch):
    monkeypatch.setattr('services.device_push.enqueue_portal_push', lambda message: None)
    monkeypatch.setattr('routes.portal._notify_agent_of_message', lambda access, body: None)
    with app.app_context():
        tx2, participant2, _ = _seller_thread(seed, '824 Unopened Conversation Lane')
        unread = PortalMessage(organization_id=seed['org_a'], transaction_id=tx2.id,
            participant_id=participant2.id, sender='client', kind='message', body='Leave this unread')
        db.session.add(unread)
        db.session.commit()
        unopened_id = unread.id
    path = f"/messages?thread=deal-{threads['participant']}&format=json"
    first = owner_a_client.get(path + '&mark_read=0')
    with app.app_context():
        assert db.session.get(PortalMessage, threads['message']).read_by_agent_at is None
    token = _open_session(client, threads['invite']).json['token']
    delivered = client.post('/api/client/v1/messages', headers=_auth_headers(token), json={'body': 'A new question from my phone'})
    assert delivered.status_code in (200, 201)
    refreshed = owner_a_client.get(path)
    assert refreshed.status_code == 200
    assert 'A new question from my phone' in refreshed.json['history_html']
    assert first.json['conversation_version'] != refreshed.json['conversation_version']
    with app.app_context():
        assert db.session.get(PortalMessage, threads['message']).read_by_agent_at is not None
        assert db.session.get(PortalMessage, unopened_id).read_by_agent_at is None
        assert PortalMessage.query.filter_by(participant_id=threads['participant'], body='A new question from my phone').one().read_by_agent_at is not None


def test_json_claim_assignment_conflict_and_access_loss(app, seed, owner_a_client, agent_a_client, threads):
    with app.app_context():
        inquiry = db.session.get(ClientBrowseInquiry, threads['inquiry'])
        account = db.session.get(ClientBrowseAccount, inquiry.account_id)
        account.agent_id = inquiry.agent_id = None
        db.session.commit()
    path = f"/messages?thread=inquiry-{threads['inquiry']}&view=general"
    agent_a_client.get(path)
    claimed = agent_a_client.post(path, data={'action': 'claim', 'csrf_token': _csrf(agent_a_client)}, headers=JSON_HEADERS)
    assert claimed.status_code == 200 and claimed.json['can_reply'] is True and claimed.json['view'] == 'mine'
    owner_a_client.get(path)
    stale = owner_a_client.post(path, data={'action': 'assign', 'expected_agent': '', 'agent_id': seed['owner_a'],
                                           'csrf_token': _csrf(owner_a_client)}, headers=JSON_HEADERS)
    assert stale.status_code == 409 and stale.json['ok'] is False
    assigned = owner_a_client.post(path, data={'action': 'assign', 'expected_agent': seed['agent_a'],
                                              'agent_id': seed['owner_a'], 'csrf_token': _csrf(owner_a_client)}, headers=JSON_HEADERS)
    assert assigned.status_code == 200
    lost = agent_a_client.get(path, headers=JSON_HEADERS)
    assert lost.status_code == 404 and lost.json['ok'] is False
    late_reply = agent_a_client.post(path, data={'body': 'No longer allowed', 'csrf_token': _csrf(agent_a_client)}, headers=JSON_HEADERS)
    assert late_reply.status_code == 404
    still_visible = owner_a_client.get(path, headers=JSON_HEADERS)
    assert still_visible.status_code == 200


def test_json_read_only_collaborator_stays_read_only(app, seed, agent_a_client, threads):
    with app.app_context():
        db.session.add(TransactionAssignment(organization_id=seed['org_a'],
            transaction_id=threads['tx'], user_id=seed['agent_a'], role='collaborator'))
        db.session.commit()
    path = f"/messages?thread=deal-{threads['participant']}"
    assert agent_a_client.get(path, headers=JSON_HEADERS).json['can_reply'] is False
    reply = agent_a_client.post(path, data={'body': 'Blocked', 'csrf_token': _csrf(agent_a_client)}, headers=JSON_HEADERS)
    assert reply.status_code == 403 and reply.json['ok'] is False


def test_inquiry_previews_do_not_load_every_history(app, seed, owner_a_client, threads):
    from sqlalchemy import event
    statements = []
    with app.app_context():
        account_id = db.session.get(ClientBrowseInquiry, threads['inquiry']).account_id
        for number in range(12):
            inquiry = ClientBrowseInquiry(organization_id=seed['org_a'], account_id=account_id,
                agent_id=seed['agent_a'], request_id=str(uuid.uuid4()), kind='question', body=f'Older question {number}')
            db.session.add(inquiry)
            db.session.flush()
            for reply_number in range(3):
                db.session.add(ClientInquiryMessage(organization_id=seed['org_a'], inquiry_id=inquiry.id,
                    sender='agent', body=f'Reply {number}-{reply_number}', author_user_id=seed['agent_a'], request_id=str(uuid.uuid4())))
        db.session.commit()
        engine = db.engine
    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        snapshot = owner_a_client.get('/messages?format=json')
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert snapshot.status_code == 200
    assert 'Reply 11-2' in snapshot.json['threads_html']
    assert 'Reply 11-0' not in snapshot.json['threads_html']
    message_queries = [sql for sql in statements if 'client_inquiry_messages' in sql]
    assert len(message_queries) == 1
    assert 'row_number() OVER' in message_queries[0]
