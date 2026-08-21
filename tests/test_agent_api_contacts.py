"""Agent iPhone API: contact-desk tasks, files, timeline, email, suggestions."""
from __future__ import annotations

from models import ContactFile, Interaction, db

import routes.agent_contact_desk  # noqa: F401 — attach routes before create_app


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _assert_json_not_redirect(resp):
    assert resp.status_code not in (301, 302, 303, 307, 308)
    assert 'Location' not in resp.headers
    assert resp.content_type.startswith('application/json')


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


def test_owner_lists_contact_a_tasks(app, seed, client):
    token = _owner_token(client)
    resp = client.get(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/tasks',
        headers=_auth(token),
    )
    assert resp.status_code == 200
    _assert_json_not_redirect(resp)
    tasks = resp.get_json()['tasks']
    assert any(row['subject'] == 'Call Jane' for row in tasks)
    jane = next(row for row in tasks if row['subject'] == 'Call Jane')
    assert jane['status'] == 'pending'
    assert set(jane) >= {
        'id', 'subject', 'status', 'priority', 'due_date',
        'type', 'subtype', 'transaction_id', 'description',
    }


def test_agent_cannot_see_owner_contact_tasks(app, seed, client):
    token = _agent_session(client, email='agent_a@test.com').get_json()['token']
    resp = client.get(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/tasks',
        headers=_auth(token),
    )
    assert resp.status_code == 404
    _assert_json_not_redirect(resp)
    assert resp.get_json()['error'] == 'Contact not found.'


def test_cookie_is_not_enough_for_contact_desk(app, seed, owner_a_client):
    resp = owner_a_client.get(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/tasks',
    )
    assert resp.status_code == 401
    _assert_json_not_redirect(resp)


def test_log_activity_creates_interaction(app, seed, client):
    token = _owner_token(client)
    resp = client.post(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/activity',
        headers=_auth(token),
        json={'type': 'call', 'notes': 'Left a voicemail', 'date': '2026-08-20'},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['activity']['type'] == 'call'
    assert body['activity']['notes'] == 'Left a voicemail'
    assert body['activity']['id']
    with app.app_context():
        row = db.session.get(Interaction, body['activity']['id'])
        assert row is not None
        assert row.contact_id == seed['contact_a']
        assert row.type == 'call'
        assert row.notes == 'Left a voicemail'


def test_timeline_returns_count_keys(app, seed, client):
    token = _owner_token(client)
    resp = client.get(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/timeline',
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body['counts']) == {
        'all', 'interaction', 'email', 'task', 'file', 'voice_memo',
    }
    assert 'activities' in body
    assert body['page'] == 1
    assert body['per_page'] == 20
    assert isinstance(body['total'], int)
    assert body['counts']['task'] >= 1


def test_files_list_empty_and_upload_requires_file(app, seed, client):
    token = _owner_token(client)
    headers = _auth(token)
    listed = client.get(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/files',
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.get_json()['files'] == []

    missing = client.post(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/files',
        headers=headers,
    )
    assert missing.status_code == 400
    _assert_json_not_redirect(missing)
    assert missing.get_json()['error']


def test_file_get_returns_json_url(app, seed, client, monkeypatch):
    monkeypatch.setattr(
        'services.supabase_storage.get_signed_url',
        lambda bucket, path, expires_in=3600: f'https://signed.example/{path}',
    )
    token = _owner_token(client)
    headers = _auth(token)

    with app.app_context():
        row = ContactFile(
            organization_id=seed['org_a'],
            contact_id=seed['contact_a'],
            user_id=seed['owner_a'],
            filename='abc.pdf',
            original_filename='listing.pdf',
            file_type='application/pdf',
            file_size=128,
            storage_path='contacts/1/listing.pdf',
        )
        db.session.add(row)
        db.session.commit()
        file_id = row.id

    cookie_only = client.get(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/files/{file_id}/file',
    )
    assert cookie_only.status_code == 401
    _assert_json_not_redirect(cookie_only)

    resp = client.get(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/files/{file_id}/file',
        headers={**headers, 'Accept': 'application/pdf'},
    )
    assert resp.status_code == 200
    _assert_json_not_redirect(resp)
    body = resp.get_json()
    assert set(body.keys()) == {'url'}
    assert body['url'] == 'https://signed.example/contacts/1/listing.pdf'


def test_task_suggestions_require_jwt_and_feature(app, seed, client):
    unauth = client.post(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/task-suggestions',
    )
    assert unauth.status_code == 401
    _assert_json_not_redirect(unauth)

    token = _agent_session(client, email='owner_b@test.com').get_json()['token']
    resp = client.post(
        f'/api/agent/v1/contacts/{seed["contact_b"]}/task-suggestions',
        headers=_auth(token),
    )
    assert resp.status_code == 403
    _assert_json_not_redirect(resp)
    body = resp.get_json()
    assert body['code'] == 'feature_required'
    assert body.get('error')


def test_emails_without_gmail_are_empty_and_disconnected(app, seed, client):
    token = _owner_token(client)
    resp = client.get(
        f'/api/agent/v1/contacts/{seed["contact_a"]}/emails',
        headers=_auth(token),
    )
    assert resp.status_code == 200
    _assert_json_not_redirect(resp)
    body = resp.get_json()
    assert body['connected'] is False
    assert body['emails'] == []
