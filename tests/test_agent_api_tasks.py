"""Agent iPhone Tasks API."""
from __future__ import annotations

from datetime import timedelta

from models import Transaction, User
from services.bob_tools.context import BobContext

import routes.agent_task_desk  # noqa: F401  — self-registers before Agent A's hook


TASK_KEYS = {
    'id', 'subject', 'description', 'status', 'priority',
    'due_date', 'scheduled_time', 'type_id', 'subtype_id',
    'type', 'subtype', 'contact_id', 'contact_name',
    'transaction_id', 'property_address', 'outcome', 'completed_at',
}


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _owner_token(client):
    resp = client.post(
        '/api/agent/v1/session',
        json={'email': 'owner_a@test.com', 'password': 'password123'},
        content_type='application/json',
    )
    assert resp.status_code == 200
    return resp.get_json()['token']


def _agent_token(client):
    resp = client.post(
        '/api/agent/v1/session',
        json={'email': 'agent_a@test.com', 'password': 'password123'},
        content_type='application/json',
    )
    assert resp.status_code == 200
    return resp.get_json()['token']


def _chicago_dates(app, user_id):
    with app.app_context():
        ctx = BobContext.from_user(User.query.get(user_id))
        return (
            (ctx.today() - timedelta(days=1)).isoformat(),
            ctx.today().isoformat(),
        )


def test_task_types_include_call_and_follow_up(app, seed, client):
    headers = _auth(_owner_token(client))
    resp = client.get('/api/agent/v1/task-types', headers=headers)
    assert resp.status_code == 200
    types = resp.get_json()['task_types']
    names = {row['name']: row for row in types}
    assert 'Call' in names
    assert names['Call']['id'] == seed['task_type_a']
    subtype_names = {row['name'] for row in names['Call']['subtypes']}
    assert 'Follow Up' in subtype_names
    follow = next(
        row for row in names['Call']['subtypes'] if row['name'] == 'Follow Up'
    )
    assert follow['id'] == seed['subtype_a']


def test_owner_lists_own_tasks_only(app, seed, client):
    headers = _auth(_owner_token(client))
    resp = client.get('/api/agent/v1/tasks', headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {'tasks', 'page', 'per_page', 'total'}
    subjects = {row['subject'] for row in body['tasks']}
    assert 'Call Jane' in subjects
    assert 'Follow up John' not in subjects
    assert 'Task B Only' not in subjects
    jane = next(row for row in body['tasks'] if row['subject'] == 'Call Jane')
    assert set(jane) == TASK_KEYS
    assert jane['id'] == seed['task_a']
    assert jane['contact_id'] == seed['contact_a']
    assert jane['contact_name'] == 'Jane Doe'
    assert jane['status'] == 'pending'


def test_task_buckets_overdue_and_today(app, seed, client):
    headers = _auth(_owner_token(client))
    yesterday, today = _chicago_dates(app, seed['owner_a'])
    overdue = client.post(
        '/api/agent/v1/tasks',
        json={
            'subject': 'Overdue bucket probe',
            'due_date': yesterday,
            'type_id': seed['task_type_a'],
            'subtype_id': seed['subtype_a'],
            'contact_id': seed['contact_a'],
        },
        headers=headers,
    )
    assert overdue.status_code == 201
    overdue_id = overdue.get_json()['task']['id']

    due_today = client.post(
        '/api/agent/v1/tasks',
        json={
            'subject': 'Today bucket probe',
            'due_date': today,
            'type_id': seed['task_type_a'],
            'subtype_id': seed['subtype_a'],
            'contact_id': seed['contact_a'],
        },
        headers=headers,
    )
    assert due_today.status_code == 201
    today_id = due_today.get_json()['task']['id']

    listed_overdue = client.get(
        '/api/agent/v1/tasks?bucket=overdue', headers=headers,
    )
    assert listed_overdue.status_code == 200
    overdue_ids = {row['id'] for row in listed_overdue.get_json()['tasks']}
    assert overdue_id in overdue_ids
    assert today_id not in overdue_ids

    listed_today = client.get(
        '/api/agent/v1/tasks?bucket=today', headers=headers,
    )
    assert listed_today.status_code == 200
    today_ids = {row['id'] for row in listed_today.get_json()['tasks']}
    assert today_id in today_ids
    assert overdue_id not in today_ids

    client.delete(f'/api/agent/v1/tasks/{overdue_id}', headers=headers)
    client.delete(f'/api/agent/v1/tasks/{today_id}', headers=headers)


def test_create_task_with_contact(app, seed, client):
    headers = _auth(_owner_token(client))
    _, today = _chicago_dates(app, seed['owner_a'])
    resp = client.post(
        '/api/agent/v1/tasks',
        json={
            'subject': 'Call Jane from iPhone',
            'due_date': today,
            'type_id': seed['task_type_a'],
            'subtype_id': seed['subtype_a'],
            'contact_id': seed['contact_a'],
            'priority': 'high',
            'description': 'Ask about the listing',
        },
        headers=headers,
    )
    assert resp.status_code == 201
    task = resp.get_json()['task']
    assert set(task) == TASK_KEYS
    assert task['subject'] == 'Call Jane from iPhone'
    assert task['contact_id'] == seed['contact_a']
    assert task['contact_name'] == 'Jane Doe'
    assert task['priority'] == 'high'
    assert task['due_date'] == today
    assert task['type'] == 'Call'
    assert task['subtype'] == 'Follow Up'
    assert task['status'] == 'pending'
    client.delete(f'/api/agent/v1/tasks/{task["id"]}', headers=headers)


def test_create_task_transaction_only(app, seed, client):
    headers = _auth(_owner_token(client))
    _, today = _chicago_dates(app, seed['owner_a'])
    with app.app_context():
        tx = Transaction.query.filter_by(
            organization_id=seed['org_a'],
            street_address='100 Main St',
        ).first()
        assert tx is not None
        tx_id = tx.id
        assert tx_id == seed['tx_a']

    resp = client.post(
        '/api/agent/v1/tasks',
        json={
            'subject': 'Order HOA docs',
            'due_date': today,
            'type_id': seed['task_type_a'],
            'subtype_id': seed['subtype_a'],
            'transaction_id': tx_id,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    task = resp.get_json()['task']
    assert task['transaction_id'] == tx_id
    assert task['contact_id'] is None
    assert task['property_address'] == '100 Main St'
    client.delete(f'/api/agent/v1/tasks/{task["id"]}', headers=headers)


def test_complete_task(app, seed, client):
    headers = _auth(_owner_token(client))
    _, today = _chicago_dates(app, seed['owner_a'])
    created = client.post(
        '/api/agent/v1/tasks',
        json={
            'subject': 'Complete me',
            'due_date': today,
            'type_id': seed['task_type_a'],
            'subtype_id': seed['subtype_a'],
            'contact_id': seed['contact_a'],
        },
        headers=headers,
    )
    task_id = created.get_json()['task']['id']
    resp = client.post(
        f'/api/agent/v1/tasks/{task_id}/complete',
        json={'outcome': 'Left a voicemail'},
        headers=headers,
    )
    assert resp.status_code == 200
    task = resp.get_json()['task']
    assert task['status'] == 'completed'
    assert task['outcome'] == 'Left a voicemail'
    assert task['completed_at']

    again = client.post(
        f'/api/agent/v1/tasks/{task_id}/complete',
        json={},
        headers=headers,
    )
    assert again.status_code == 200
    assert again.get_json()['task']['status'] == 'completed'
    client.delete(f'/api/agent/v1/tasks/{task_id}', headers=headers)


def test_patch_due_date(app, seed, client):
    headers = _auth(_owner_token(client))
    yesterday, today = _chicago_dates(app, seed['owner_a'])
    created = client.post(
        '/api/agent/v1/tasks',
        json={
            'subject': 'Reschedule me',
            'due_date': yesterday,
            'type_id': seed['task_type_a'],
            'subtype_id': seed['subtype_a'],
            'contact_id': seed['contact_a'],
        },
        headers=headers,
    )
    task_id = created.get_json()['task']['id']
    resp = client.patch(
        f'/api/agent/v1/tasks/{task_id}',
        json={'due_date': today},
        headers=headers,
    )
    assert resp.status_code == 200
    task = resp.get_json()['task']
    assert task['due_date'] == today
    client.delete(f'/api/agent/v1/tasks/{task_id}', headers=headers)


def test_delete_task(app, seed, client):
    headers = _auth(_owner_token(client))
    _, today = _chicago_dates(app, seed['owner_a'])
    created = client.post(
        '/api/agent/v1/tasks',
        json={
            'subject': 'Delete me',
            'due_date': today,
            'type_id': seed['task_type_a'],
            'subtype_id': seed['subtype_a'],
            'contact_id': seed['contact_a'],
        },
        headers=headers,
    )
    task_id = created.get_json()['task']['id']
    resp = client.delete(f'/api/agent/v1/tasks/{task_id}', headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    missing = client.get(f'/api/agent/v1/tasks/{task_id}', headers=headers)
    assert missing.status_code == 404


def test_agent_cannot_complete_someone_elses_task(app, seed, client):
    headers = _auth(_agent_token(client))
    resp = client.post(
        f'/api/agent/v1/tasks/{seed["task_a"]}/complete',
        json={},
        headers=headers,
    )
    assert resp.status_code in (403, 404)


def test_tasks_require_jwt(app, seed, client):
    resp = client.get('/api/agent/v1/tasks')
    assert resp.status_code == 401
    assert resp.content_type.startswith('application/json')


def test_cookie_is_not_enough_for_tasks(app, seed, owner_a_client):
    resp = owner_a_client.get('/api/agent/v1/tasks')
    assert resp.status_code == 401
    assert resp.content_type.startswith('application/json')
