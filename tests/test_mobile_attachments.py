"""Message files stay inside their sender identity and authorized conversation."""
from datetime import datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from models import db, MobileChatAttachment, PortalMessage, ClientBrowseInquiry
from services import supabase_storage, profile_photos
from tests.test_agent_api import _owner_token, _auth, _seller_thread, _agent_session
from tests.test_client_portal_api import _open_session
from tests.test_client_discovery import signup, auth, BASE, enable_discovery


@pytest.fixture
def file_storage(monkeypatch):
    objects = {}
    def upload(bucket, path, data, *args, **kwargs):
        objects[path] = data
    monkeypatch.setattr(supabase_storage, 'upload_file', upload)
    monkeypatch.setattr(supabase_storage, 'get_signed_url', lambda bucket, path, **kw: 'https://files.example/' + path)
    monkeypatch.setattr(supabase_storage, 'delete_file', lambda bucket, path: objects.pop(path, None) is not None or True)
    monkeypatch.setattr(profile_photos, 'storage_configured', lambda: True)
    class Bucket:
        def list(self, prefix, options):
            return [{'name': path.rsplit('/', 1)[-1]} for path in objects if path.startswith(prefix + '/')][:100]
        def remove(self, paths):
            for path in paths:
                objects.pop(path, None)
    monkeypatch.setattr(supabase_storage, 'get_supabase_client', lambda: SimpleNamespace(storage=SimpleNamespace(from_=lambda bucket: Bucket())))
    from services import device_push
    from routes import agent_api, agent_inquiries, portal
    monkeypatch.setattr(device_push, 'enqueue_portal_push', lambda *a, **kw: None)
    monkeypatch.setattr(device_push, 'enqueue_inquiry_push', lambda *a, **kw: None)
    monkeypatch.setattr(agent_api, 'enqueue_portal_push', lambda *a, **kw: None)
    monkeypatch.setattr(agent_inquiries, 'enqueue_inquiry_push', lambda *a, **kw: None)
    monkeypatch.setattr(portal, '_notify_agent_of_message', lambda *a, **kw: None)
    return objects


def png(color='red'):
    output = BytesIO()
    Image.new('RGB', (64, 32), color).save(output, format='PNG')
    return output.getvalue()


def upload(client, route, headers, data=b'Private closing notes', filename='notes.txt', request_id=None):
    return client.post(route, headers=headers, data={'file': (BytesIO(data), filename), 'request_id': request_id or str(uuid4())})


def deal(app, seed, client):
    with app.app_context():
        tx, participant, access = _seller_thread(seed)
        db.session.commit()
        participant_id, code = participant.id, access.invite_code
    return participant_id, _auth(_open_session(client, code).json['token'])


def test_agent_attachment_only_message_delivers_to_client_once(app, seed, client, file_storage):
    participant, client_headers = deal(app, seed, client)
    agent = _auth(_owner_token(client))
    route = f'/api/agent/v1/conversations/{participant}/attachments'
    staged = upload(client, route, agent)
    assert staged.status_code == 201, staged.json
    attachment = staged.json['attachment']
    assert 'storage_path' not in attachment
    assert client.get('/api/client/v1/attachments/' + attachment['id'], headers=client_headers).status_code == 404
    body = dict(body='', request_id=str(uuid4()), attachment_ids=[attachment['id']])
    endpoint = f'/api/agent/v1/conversations/{participant}/messages'
    sent = client.post(endpoint, headers=agent, json=body)
    assert sent.status_code == 201, sent.json
    assert sent.json['message']['attachments'][0]['filename'] == 'notes.txt'
    repeated = client.post(endpoint, headers=agent, json=body)
    assert repeated.status_code == 200
    assert repeated.json['message']['id'] == sent.json['message']['id']
    assert client.post(endpoint, headers=agent, json={**body, 'attachment_ids': [], 'body': 'Changed'}).status_code == 409
    assert client.get('/api/client/v1/attachments/' + attachment['id'], headers=client_headers).status_code == 200
    assert client.delete('/api/agent/v1/attachments/' + attachment['id'], headers=agent).status_code == 403
    received = client.get('/api/client/v1/messages', headers=client_headers).json['messages'][-1]
    assert received['attachments'][0]['id'] == attachment['id']
    with app.app_context():
        assert PortalMessage.query.filter_by(participant_id=participant, request_id=body['request_id']).count() == 1


def test_upload_receipts_and_owner_thread_tenant_boundaries(app, seed, client, file_storage):
    participant, client_headers = deal(app, seed, client)
    other_participant, other_headers = deal(app, seed, client)
    agent = _auth(_owner_token(client))
    foreign = _auth(_agent_session(client, email='owner_b@test.com').json['token'])
    request_id = str(uuid4())
    staged = upload(client, '/api/client/v1/attachments', client_headers, png(), 'photo.png', request_id)
    assert staged.status_code == 201
    identifier = staged.json['attachment']['id']
    retry = upload(client, '/api/client/v1/attachments', client_headers, png(), 'photo.png', request_id)
    assert retry.status_code == 200 and retry.json['attachment']['id'] == identifier
    assert upload(client, '/api/client/v1/attachments', client_headers, png('blue'), 'photo.png', request_id).status_code == 409
    assert client.get('/api/client/v1/attachments/' + identifier, headers=other_headers).status_code == 404
    assert client.get('/api/agent/v1/attachments/' + identifier, headers=foreign).status_code == 404
    payload = dict(body='', request_id=str(uuid4()), attachment_ids=[identifier])
    assert client.post('/api/client/v1/messages', headers=other_headers, json=payload).status_code == 403
    assert client.post(f'/api/agent/v1/conversations/{participant}/messages', headers=agent, json=payload).status_code == 403
    sent = client.post('/api/client/v1/messages', headers=client_headers, json=payload)
    assert sent.status_code == 201, sent.json
    assert sent.json['message']['attachments'][0]['content_type'] == 'image/jpeg'
    assert client.get('/api/agent/v1/attachments/' + identifier, headers=agent).status_code == 200
    assert client.post('/api/client/v1/messages', headers=client_headers, json={**payload, 'request_id': str(uuid4())}).status_code == 409


def test_initial_property_inquiry_and_replies_bind_private_files(app, seed, client, enable_discovery, file_storage):
    browser = signup(client, agent_id=seed['owner_a'])
    headers = auth(browser)
    stranger = auth(signup(client))
    staged = upload(client, BASE + '/attachments', headers)
    assert staged.status_code == 201
    identifier = staged.json['attachment']['id']
    data = dict(kind='question', listing_id='h01', body='', consent=True, request_id=str(uuid4()), attachment_ids=[identifier])
    assert client.post(BASE + '/inquiries', headers=stranger, json=data).status_code == 403
    sent = client.post(BASE + '/inquiries', headers=headers, json=data)
    assert sent.status_code == 201, sent.json
    inquiry_id = sent.json['id']
    assert client.post(BASE + '/inquiries', headers=headers, json=data).status_code == 200
    assert client.post(BASE + '/inquiries', headers=headers, json={**data, 'attachment_ids': []}).status_code == 409
    agent = _auth(_owner_token(client))
    detail = client.get(f'/api/agent/v1/inquiries/{inquiry_id}', headers=agent).json['inquiry']
    assert detail['messages'][0]['attachments'][0]['id'] == identifier
    assert client.get('/api/agent/v1/attachments/' + identifier, headers=agent).status_code == 200
    assert client.get(BASE + '/attachments/' + identifier, headers=stranger).status_code == 404
    reply_file = upload(client, f'/api/agent/v1/inquiries/{inquiry_id}/attachments', agent, png(), 'house.png').json['attachment']
    reply = client.post(f'/api/agent/v1/inquiries/{inquiry_id}/messages', headers=agent,
        json=dict(body='', request_id=str(uuid4()), attachment_ids=[reply_file['id']]))
    assert reply.status_code == 201, reply.json
    assert client.get(BASE + '/attachments/' + reply_file['id'], headers=headers).status_code == 200
    followup_file = upload(client, BASE + f'/inquiries/{inquiry_id}/attachments', headers).json['attachment']
    followup = client.post(BASE + f'/inquiries/{inquiry_id}/messages', headers=headers,
        json=dict(body='', request_id=str(uuid4()), attachment_ids=[followup_file['id']]))
    assert followup.status_code == 200, followup.json
    assert followup.json['message']['attachments'][0]['id'] == followup_file['id']
    removed = client.delete(BASE + '/account', headers=headers, json={'password': 'test-password-123'})
    assert removed.status_code == 200, removed.json
    assert file_storage == {}
    with app.app_context():
        assert MobileChatAttachment.query.filter(MobileChatAttachment.id.in_([identifier, reply_file['id'], followup_file['id']])).count() == 0


def test_invalid_files_expired_drafts_and_discard(app, seed, client, file_storage):
    participant, headers = deal(app, seed, client)
    assert upload(client, '/api/client/v1/attachments', headers, b'not PDF', 'fake.pdf').status_code == 400
    assert upload(client, '/api/client/v1/attachments', headers, b'\x00binary', 'text.txt').status_code == 400
    assert upload(client, '/api/client/v1/attachments', headers, b'x' * (10 * 1024 * 1024 + 1), 'huge.txt').status_code == 413
    request_id = str(uuid4())
    first = upload(client, '/api/client/v1/attachments', headers, request_id=request_id).json['attachment']
    with app.app_context():
        db.session.get(MobileChatAttachment, first['id']).created_at = datetime.utcnow() - timedelta(days=2)
        db.session.commit()
    assert client.get('/api/client/v1/attachments/' + first['id'], headers=headers).status_code == 410
    second = upload(client, '/api/client/v1/attachments', headers, request_id=request_id)
    assert second.status_code == 201
    assert second.json['attachment']['id'] != first['id']
    assert len(file_storage) == 1
    identifier = second.json['attachment']['id']
    assert client.delete('/api/client/v1/attachments/' + identifier, headers=headers).status_code == 200
    assert file_storage == {}
    assert client.post('/api/client/v1/messages', headers=headers,
        json=dict(body='', request_id=str(uuid4()), attachment_ids=[identifier])).status_code == 410


def test_discarding_uploads_does_not_reset_daily_allowance(app, seed, client, file_storage, monkeypatch):
    from services import mobile_attachments
    monkeypatch.setattr(mobile_attachments, 'DAILY_UPLOAD_LIMIT', 2)
    participant, headers = deal(app, seed, client)
    for _ in range(2):
        response = upload(client, '/api/client/v1/attachments', headers)
        assert response.status_code == 201
        assert client.delete('/api/client/v1/attachments/' + response.json['attachment']['id'], headers=headers).status_code == 200
    assert upload(client, '/api/client/v1/attachments', headers).status_code == 429
    assert file_storage == {}


def test_daily_cleanup_removes_only_expired_unsent_files(app, seed, client, file_storage):
    from jobs.mobile_attachment_cleanup import cleanup_staged_attachments
    participant, headers = deal(app, seed, client)
    expired = upload(client, '/api/client/v1/attachments', headers).json['attachment']['id']
    sent = upload(client, '/api/client/v1/attachments', headers).json['attachment']['id']
    fresh = upload(client, '/api/client/v1/attachments', headers).json['attachment']['id']
    response = client.post('/api/client/v1/messages', headers=headers,
        json=dict(body='', request_id=str(uuid4()), attachment_ids=[sent]))
    assert response.status_code == 201
    with app.app_context():
        for identifier in (expired, sent):
            db.session.get(MobileChatAttachment, identifier).created_at = datetime.utcnow() - timedelta(days=2)
        db.session.commit()
        assert cleanup_staged_attachments() == 1
        assert db.session.get(MobileChatAttachment, expired) is None
        assert db.session.get(MobileChatAttachment, sent) is not None
        assert db.session.get(MobileChatAttachment, fresh) is not None
    assert len(file_storage) == 2


@pytest.mark.parametrize('remove_participant', [False, True])
def test_deal_deletion_purges_sent_and_staged_files(app, seed, client, file_storage, remove_participant):
    from models import TransactionParticipant
    from tests.conftest import login
    participant, client_headers = deal(app, seed, client)
    agent = _auth(_owner_token(client))
    sent_file = upload(client, '/api/client/v1/attachments', client_headers).json['attachment']['id']
    assert client.post('/api/client/v1/messages', headers=client_headers, json=dict(
        body='', request_id=str(uuid4()), attachment_ids=[sent_file])).status_code == 201
    upload(client, f'/api/agent/v1/conversations/{participant}/attachments', agent)
    with app.app_context():
        transaction_id = db.session.get(TransactionParticipant, participant).transaction_id
    if remove_participant:
        login(client, 'owner_a')
        deleted = client.delete(f'/transactions/{transaction_id}/participants/{participant}')
    else:
        deleted = client.delete(f'/api/agent/v1/transactions/{transaction_id}', headers=agent)
    assert deleted.status_code == 200, deleted.json
    assert not file_storage
    with app.app_context():
        assert MobileChatAttachment.query.filter_by(target_kind='deal', target_id=participant).count() == 0
    assert client.get('/api/client/v1/attachments/' + sent_file, headers=client_headers).status_code in (401, 404)


def test_message_projection_does_not_sign_orphaned_thread_binding(app, seed, client, file_storage):
    from services.mobile_attachments import message_attachments
    participant, client_headers = deal(app, seed, client)
    other, _ = deal(app, seed, client)
    identifier = upload(client, '/api/client/v1/attachments', client_headers).json['attachment']['id']
    response = client.post('/api/client/v1/messages', headers=client_headers, json=dict(
        body='', request_id=str(uuid4()), attachment_ids=[identifier]))
    message_id = response.json['message']['id']
    with app.test_request_context():
        row = db.session.get(MobileChatAttachment, identifier)
        row.target_id = other
        db.session.commit()
        assert message_attachments(seed['org_a'], 'portal', [message_id])[message_id] == []
