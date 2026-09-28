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
