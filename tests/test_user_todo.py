"""Todo ordering, completion, replacement, and per-user isolation."""
import pytest
from models import UserTodo


@pytest.fixture
def restore_todos(app, db, seed):
    users = [seed['owner_a'], seed['agent_a'], seed['owner_b']]
    with app.app_context():
        saved = [{column.name: getattr(todo, column.name) for column in UserTodo.__table__.columns}
                 for todo in UserTodo.query.filter(UserTodo.user_id.in_(users))]
        UserTodo.query.filter(UserTodo.user_id.in_(users)).delete(synchronize_session=False)
        db.session.commit()
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


def test_agent_todos_share_web_order_and_reject_stale_writes(app, client, owner_a_client, restore_todos):
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
    with app.app_context():
        assert all(row.details is None for row in UserTodo.query.filter_by(text='Call'))
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


def test_details_survive_old_app_and_web_edits(client, owner_a_client, restore_todos):
    import json
    token = client.post('/api/agent/v1/session', json={'email': 'owner_a@test.com', 'password': 'password123'}).json['token']
    headers = {'Authorization': f'Bearer {token}'}
    url = '/api/agent/v1/todos'
    original = client.get(url, headers=headers).json
    original['items'] = [{'id': '12e74756-d158-4fde-b77c-a51cfdc98f50', 'text': 'Plan open house', 'completed': False, 'details': {
        'description': 'Bring the signs', 'due_date': '2026-11-08', 'priority': 2,
        'subtodos': [{'id': 'd1472301-45b1-48cb-807f-65905fa660d8', 'text': 'Print flyers', 'completed': False}],
        'comments': [{'id': '7cfaebae-88dd-4ea4-851a-09bcb826b107', 'text': 'Key is ready', 'created_at': 'forged'}]
    }}]
    saved = client.put(url, headers=headers, json=original)
    assert saved.status_code == 200
    item = saved.json['items'][0]
    assert item['id'] == original['items'][0]['id'], 'New client IDs must survive save for queued edits'
    details = item['details']
    assert details['comments'][0]['created_at'] != 'forged'
    assert details['subtodos'][0]['text'] == 'Print flyers'
    legacy = {'revision': saved.json['revision'], 'items': [{key: item[key] for key in ('id', 'text', 'completed')}]}
    legacy['items'][0]['completed'] = True
    assert client.put(url, headers=headers, json=legacy).status_code == 200
    assert client.get(url, headers=headers).json['items'][0]['details'] == details
    web = owner_a_client.get('/api/user_todos/get?include_ids=1')
    todo_id = web.json['identities']['completed'][0]
    response = owner_a_client.post('/api/user_todos/save', json={
        'active': [], 'completed': ['Open house plan'], 'revision': web.headers['X-Todo-Revision'],
        'identities': {'active': [], 'completed': [todo_id]}})
    assert response.status_code == 200
    updated = client.get(url, headers=headers).json
    assert updated['items'][0]['text'] == 'Open house plan'
    assert updated['items'][0]['details'] == details
    invalid = dict(updated)
    invalid['items'][0]['details']['subtodos'] *= 2
    assert client.put(url, headers=headers, json=invalid).status_code == 400
    assert client.get(url, headers=headers).json['items'][0]['details'] == details


def test_web_duplicate_titles_keep_their_own_details(client, owner_a_client, restore_todos):
    token = client.post('/api/agent/v1/session', json={'email': 'owner_a@test.com', 'password': 'password123'}).json['token']
    headers = {'Authorization': f'Bearer {token}'}
    url = '/api/agent/v1/todos'
    snapshot = client.get(url, headers=headers).json
    ids = ['888b87f3-4fd2-43c8-943b-e8e7903d6b4c', '56f13525-e36b-4e92-92d4-e4e62d8a7381']
    snapshot['items'] = [
        {'id': identity, 'text': 'Call', 'completed': False, 'details': {'description': note}}
        for identity, note in zip(ids, ['First person', 'Second person'])
    ]
    assert client.put(url, headers=headers, json=snapshot).status_code == 200
    web = owner_a_client.get('/api/user_todos/get?include_ids=1')
    payload = {'active': ['Call', 'Call'], 'completed': [], 'identities': {'active': ids[::-1], 'completed': []}, 'revision': web.headers['X-Todo-Revision']}
    assert owner_a_client.post('/api/user_todos/save', json=payload).status_code == 200
    items = client.get(url, headers=headers).json['items']
    assert [item['id'] for item in items] == ids[::-1]
    assert [item['details']['description'] for item in items] == ['Second person', 'First person']
    web = owner_a_client.get('/api/user_todos/get?include_ids=1')
    payload['revision'] = web.headers['X-Todo-Revision']
    payload['identities']['active'] = [ids[0], '99999999']
    assert owner_a_client.post('/api/user_todos/save', json=payload).status_code == 409
    assert client.get(url, headers=headers).json['items'] == items


def test_legacy_duplicate_rename_and_overlong_ids_are_rejected(client, owner_a_client, restore_todos):
    token = client.post('/api/agent/v1/session', json={'email': 'owner_a@test.com', 'password': 'password123'}).json['token']
    headers = {'Authorization': f'Bearer {token}'}
    url = '/api/agent/v1/todos'
    snapshot = client.get(url, headers=headers).json
    ids = ['70653d7c-40ed-4071-9dbe-926b78750cd2', '05b7304f-d252-438f-bb15-af3e3fa38acd']
    snapshot['items'] = [
        {'id': ids[0], 'text': 'Same title', 'completed': False},
        {'id': ids[1], 'text': 'Same title', 'completed': False, 'details': {'description': 'Private note'}}
    ]
    assert client.put(url, headers=headers, json=snapshot).status_code == 200
    saved = client.get(url, headers=headers).json
    assert owner_a_client.post('/api/user_todos/save', json={
        'active': ['Same title', 'Renamed detailed item'], 'completed': [], 'revision': saved['revision']}).status_code == 409
    assert client.get(url, headers=headers).json == saved
    for active, completed in [(['Same title'], []), (['Same title'], ['Same title'])]:
        assert owner_a_client.post('/api/user_todos/save', json={
            'active': active, 'completed': completed, 'revision': saved['revision']}).status_code == 409
        assert client.get(url, headers=headers).json == saved
    for invalid_id in ['{' + ids[0] + '}', 'urn:uuid:' + ids[0]]:
        invalid = {'items': [{'id': invalid_id, 'text': 'No', 'completed': False}], 'revision': saved['revision']}
        assert client.put(url, headers=headers, json=invalid).status_code == 409
        assert owner_a_client.post('/api/user_todos/save', json={
            'active': ['No'], 'completed': [], 'identities': {'active': [invalid_id], 'completed': []}, 'revision': saved['revision']}).status_code == 409
        assert client.get(url, headers=headers).json == saved


def test_legacy_rename_cannot_create_ambiguous_detailed_duplicate(app, db, seed, owner_a_client, restore_todos):
    with app.app_context():
        db.session.add_all([
            UserTodo(user_id=seed['owner_a'], organization_id=seed['org_a'], text='Plan', completed=False, order=0),
            UserTodo(user_id=seed['owner_a'], organization_id=seed['org_a'], text='Call', completed=False, order=1,
                     details={'description': 'Keep with this call'})])
        db.session.commit()
    assert owner_a_client.post('/api/user_todos/save', json={'active': ['Call', 'Call'], 'completed': []}).status_code == 409
    assert owner_a_client.get('/api/user_todos/get').json['active'] == ['Plan', 'Call']


def test_bob_details_invalidate_native_and_web_snapshots(app, client, owner_a_client, seed, restore_todos):
    from services.bob_tools import BobContext, dispatch, undo_action
    from tests.test_agent_api import _owner_token, _auth
    headers = _auth(_owner_token(client))
    url = '/api/agent/v1/todos'
    owner_a_client.post('/api/user_todos/save', json={'active': ['Shared item'], 'completed': []})
    original = client.get(url, headers=headers).json
    with app.app_context():
        ctx = BobContext(user_id=seed['owner_a'], organization_id=seed['org_a'])
        changed = dispatch('update_todo', {'text': 'Shared item', 'description': 'BOB details',
                                         'due_date': '2026-11-09', 'priority': 1}, ctx)
        assert changed.ok, changed.error
    current = client.get(url, headers=headers).json
    assert current['items'][0]['details']['description'] == 'BOB details'
    assert current['items'][0]['id'] == original['items'][0]['id']
    assert current['revision'] != original['revision']
    assert client.put(url, headers=headers, json=original).status_code == 409
    assert owner_a_client.post('/api/user_todos/save', json={
        'active': ['Stale title'], 'completed': [], 'revision': original['revision']}).status_code == 409
    # A current native edit survives an older BOB undo attempt.
    current['items'][0]['details']['description'] = 'Later native edit'
    assert client.put(url, headers=headers, json=current).status_code == 200
    with app.app_context():
        assert not undo_action(changed.action_id, ctx).ok
    assert client.get(url, headers=headers).json['items'][0]['details']['description'] == 'Later native edit'
