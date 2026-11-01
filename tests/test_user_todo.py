"""Todo ordering, completion, replacement, and per-user isolation."""
import pytest
from models import UserTodo


@pytest.fixture
def restore_todos(app, db, seed):
    users = [seed['owner_a'], seed['agent_a'], seed['owner_b']]
    with app.app_context():
        saved = [{column.name: getattr(todo, column.name) for column in UserTodo.__table__.columns}
                 for todo in UserTodo.query.filter(UserTodo.user_id.in_(users))]
    yield
    with app.app_context():
        UserTodo.query.filter(UserTodo.user_id.in_(users)).delete(synchronize_session=False)
        db.session.add_all(UserTodo(**row) for row in saved)
        db.session.commit()


def test_todos_round_trip_replace_and_clear(owner_a_client, restore_todos):
    url = '/api/user_todos/save'
    original = {'active': ['Call client', 'Book inspection'], 'completed': ['Send offer']}
    response = owner_a_client.post(url, json=original)
    assert response.status_code == 200
    assert owner_a_client.get('/api/user_todos/get').json == original
    replacement = {'active': ['Book inspection'], 'completed': ['Call client', 'Send offer']}
    assert owner_a_client.post(url, json=replacement).status_code == 200
    assert owner_a_client.get('/api/user_todos/get').json == replacement
    assert owner_a_client.post(url, json={'active': [], 'completed': []}).status_code == 200
    assert owner_a_client.get('/api/user_todos/get').json == {'active': [], 'completed': []}


def test_saving_one_users_todos_preserves_other_users(owner_a_client, agent_a_client, owner_b_client, restore_todos):
    clients = [owner_a_client, agent_a_client, owner_b_client]
    values = [{'active': [text], 'completed': []} for text in ['Owner call', 'Agent call', 'Other org call']]
    for client, value in zip(clients, values):
        assert client.post('/api/user_todos/save', json=value).status_code == 200
    assert owner_a_client.post('/api/user_todos/save', json={'active': [], 'completed': []}).status_code == 200
    values[0] = {'active': [], 'completed': []}
    for client, value in zip(clients, values):
        response = client.get('/api/user_todos/get')
        assert response.status_code == 200
        assert response.json == value


def test_agent_todos_share_web_order_and_reject_stale_writes(client, owner_a_client, restore_todos):
    token = client.post('/api/agent/v1/session', json={'email': 'owner_a@test.com', 'password': 'password123'}).json['token']
    headers = {'Authorization': f'Bearer {token}'}
    url = '/api/agent/v1/todos'
    owner_a_client.post('/api/user_todos/save', json={'active': ['Call', 'Plan'], 'completed': ['Done']})
    initial = client.get(url, headers=headers).json
    items = initial['items']
    items[0]['completed'] = True
    changed = {'revision': initial['revision'], 'items': [items[1], items[0], items[2]]}
    saved = client.put(url, headers=headers, json=changed)
    assert saved.status_code == 200
    assert owner_a_client.get('/api/user_todos/get').json == {'active': ['Plan'], 'completed': ['Call', 'Done']}
    assert client.put(url, headers=headers, json=changed).status_code == 409
    assert client.get(url, headers=headers).json == saved.json
    # A stale browser cannot overwrite the native completion.
    stale_web = owner_a_client.post('/api/user_todos/save', json={
        'active': ['Call', 'Plan'], 'completed': ['Done'], 'revision': initial['revision']})
    assert stale_web.status_code == 409
    # Restore, add, and reorder survive an independent readback.
    restored = saved.json
    restored['items'][1]['completed'] = False
    restored['items'].insert(0, {'id': '01a197c4-706d-79a0-99c1-cf64958c0859', 'text': 'New', 'completed': False})
    response = client.put(url, headers=headers, json=restored)
    assert response.status_code == 200
    assert owner_a_client.get('/api/user_todos/get').json == {'active': ['New', 'Plan', 'Call'], 'completed': ['Done']}
    ids = {item['text']: item['id'] for item in response.json['items']}
    owner_a_client.post('/api/user_todos/save', json={'active': ['Call', 'Plan', 'New'], 'completed': ['Done']})
    assert {item['text']: item['id'] for item in client.get(url, headers=headers).json['items']} == ids


def test_agent_todos_require_bearer_and_preserve_other_users(client, owner_a_client, agent_a_client, owner_b_client, restore_todos):
    url = '/api/agent/v1/todos'
    assert owner_a_client.get(url).status_code == 401
    for web, text in [(owner_a_client, 'Mine'), (agent_a_client, 'Same org'), (owner_b_client, 'Other org')]:
        web.post('/api/user_todos/save', json={'active': [text], 'completed': []})
    token = client.post('/api/agent/v1/session', json={'email': 'owner_a@test.com', 'password': 'password123'}).json['token']
    headers = {'Authorization': f'Bearer {token}'}
    initial = client.get(url, headers=headers).json
    assert [item['text'] for item in initial['items']] == ['Mine']
    invalid = {'revision': initial['revision'], 'items': [{'id': '99999999', 'text': 'Foreign', 'completed': False}]}
    assert client.put(url, headers=headers, json=invalid).status_code == 409
    invalid['items'][0]['text'] = 'x' * 501
    assert client.put(url, headers=headers, json=invalid).status_code == 400
    assert client.get(url, headers=headers).json == initial
    assert client.put(url, headers=headers, json={'revision': initial['revision'], 'items': []}).status_code == 200
    assert agent_a_client.get('/api/user_todos/get').json['active'] == ['Same org']
    assert owner_b_client.get('/api/user_todos/get').json['active'] == ['Other org']
