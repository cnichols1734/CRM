"""Native BOB tenant boundaries, durable history and replay protection."""
import json
from uuid import uuid4
import pytest
from models import ChatConversation, ChatMessage, BobAction, db
from routes import agent_bob
from tests.test_agent_api import _owner_token, _auth


class ReceiptStore:
    def __init__(self):
        self.values = {}
    def get(self, key):
        return self.values.get(key)
    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True
    def eval(self, script, count, key, expected):
        if self.values.get(key) == expected:
            self.values.pop(key, None)


@pytest.fixture
def receipts(monkeypatch):
    store = ReceiptStore()
    monkeypatch.setattr(agent_bob, 'receipt_store', lambda: store)
    return store


def test_bob_requires_bearer(client, seed):
    response = client.get('/api/agent/v1/bob/conversations')
    assert response.status_code == 401
    assert response.is_json


def test_native_history_and_idempotent_turn(app, client, seed, monkeypatch, receipts):
    headers = _auth(_owner_token(client))
    created = client.post('/api/agent/v1/bob/conversations', json={}, headers=headers)
    assert created.status_code == 201
    cid = created.json['id']
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        yield 'text', 'Your next task is ready.'
    monkeypatch.setattr(agent_bob, 'run_tool_conversation', run)
    body = {'message': 'What is next?', 'request_id': str(uuid4())}
    route = f'/api/agent/v1/bob/conversations/{cid}/messages'
    result = client.post(route, json=body, headers=headers)
    assert result.status_code == 200, result.json
    assert [m['role'] for m in result.json['messages']] == ['user', 'assistant']
    assert client.post(route, json=body, headers=headers).status_code == 200
    assert len(calls) == 1
    assert client.post(route, json={**body, 'message': 'Different'}, headers=headers).status_code == 409
    followup = client.post(route, json={'message': 'And then?', 'request_id': str(uuid4())}, headers=headers)
    assert followup.status_code == 200
    assert calls[-1]['messages'][0]['content'] == 'What is next?'
    assert calls[-1]['messages'][1]['content'] == 'Your next task is ready.'
    assert client.get(f'/api/agent/v1/bob/conversations/{cid}', headers=headers).json['messages'][-1]['content'] == 'Your next task is ready.'


def test_foreign_conversation_and_action_are_hidden(app, client, seed):
    with app.app_context():
        foreign = ChatConversation(user_id=seed['owner_b'], organization_id=seed['org_b'])
        db.session.add(foreign)
        db.session.flush()
        action = BobAction(user_id=seed['owner_b'], organization_id=seed['org_b'], conversation_id=foreign.id,
                           tool_name='delete_contact', status='pending', arguments={})
        db.session.add(action)
        db.session.commit()
        cid, aid = foreign.id, action.id
    headers = _auth(_owner_token(client))
    assert client.get(f'/api/agent/v1/bob/conversations/{cid}', headers=headers).status_code == 404
    assert client.post(f'/api/agent/v1/bob/conversations/{cid}/messages', json={'message': 'hi', 'request_id': str(uuid4())}, headers=headers).status_code == 404
    assert client.post(f'/api/agent/v1/bob/actions/{aid}', json={'decision': 'approve'}, headers=headers).status_code == 404
    assert cid not in [c['id'] for c in client.get('/api/agent/v1/bob/conversations', headers=headers).json['conversations']]


def test_receipts_fail_closed(app, client, seed, monkeypatch):
    headers = _auth(_owner_token(client))
    cid = client.post('/api/agent/v1/bob/conversations', json={}, headers=headers).json['id']
    def unavailable():
        raise ConnectionError('offline')
    monkeypatch.setattr(agent_bob, 'receipt_store', unavailable)
    monkeypatch.setattr(agent_bob, 'run_tool_conversation', lambda **kw: pytest.fail('must not call model'))
    response = client.post(f'/api/agent/v1/bob/conversations/{cid}/messages', json={'message': 'hi', 'request_id': str(uuid4())}, headers=headers)
    assert response.status_code == 503
    assert client.get(f'/api/agent/v1/bob/conversations/{cid}', headers=headers).json['messages'] == []


def test_pending_action_survives_refresh_and_rejection(app, client, seed, monkeypatch, receipts):
    headers = _auth(_owner_token(client))
    cid = client.post('/api/agent/v1/bob/conversations', json={}, headers=headers).json['id']
    with app.app_context():
        row = BobAction(user_id=seed['owner_a'], organization_id=seed['org_a'], conversation_id=cid,
                        tool_name='delete_contact', status='pending', arguments={}, summary='Delete contact', preview={'contact_name': 'Example'})
        db.session.add(row)
        db.session.commit()
        aid = row.id
    result = client.get(f'/api/agent/v1/bob/conversations/{cid}', headers=headers)
    assert result.json['actions'][0]['preview_text'] == 'Contact name: Example'
    result = client.post(f'/api/agent/v1/bob/actions/{aid}', json={'decision': 'reject'}, headers=headers)
    assert result.status_code == 200, result.json
    assert client.get(f'/api/agent/v1/bob/conversations/{cid}', headers=headers).json['actions'][0]['status'] == 'rejected'


def test_native_and_web_have_same_tool_catalog(app, seed):
    from models import User
    from services.bob_tools import BobContext, select_tools
    with app.app_context():
        user = db.session.get(User, seed['owner_a'])
        native = {t.name for t in select_tools(BobContext.from_user(user, surface='bob_ios'))}
        web = {t.name for t in select_tools(BobContext.from_user(user, surface='bob_chat'))}
        assert native == web
        assert 'search_transactions' in native


def test_message_validates_json_before_running_tools(client, seed):
    headers = _auth(_owner_token(client))
    cid = client.post('/api/agent/v1/bob/conversations', json={}, headers=headers).json['id']
    route = f'/api/agent/v1/bob/conversations/{cid}/messages'
    assert client.post(route, json=['invalid'], headers=headers).status_code == 400
    assert client.post(route, json={'message': 'hi', 'request_id': 'invalid'}, headers=headers).status_code == 400
