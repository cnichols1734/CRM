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
