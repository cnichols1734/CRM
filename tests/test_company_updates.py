"""Company update persistence, engagement, and access boundaries."""
import pytest
from models import CompanyUpdate, CompanyUpdateComment, CompanyUpdateReaction


@pytest.fixture
def update_id(app, db, seed):
    with app.app_context():
        update = CompanyUpdate(organization_id=seed['org_a'], author_id=seed['owner_a'],
                               title='Regression announcement', content='<p>Office meeting.</p>')
        db.session.add(update)
        db.session.commit()
        identifier = update.id
    yield identifier
    with app.app_context():
        update = db.session.get(CompanyUpdate, identifier)
        if update:
            db.session.delete(update)
            db.session.commit()


def test_create_and_edit_update_persist_for_org(owner_a_client, agent_a_client, seed, app, db):
    assert owner_a_client.get('/updates/new').status_code == 200
    response = owner_a_client.post('/updates/new', data={
        'title': 'New regression announcement', 'content': '<p>Office meeting.</p>',
    })
    assert response.status_code == 302
    with app.app_context():
        update = CompanyUpdate.query.filter_by(title='New regression announcement').one()
        identifier = update.id
        assert (update.author_id, update.organization_id) == (seed['owner_a'], seed['org_a'])
    try:
        assert owner_a_client.get(f'/updates/{identifier}/edit').status_code == 200
        response = owner_a_client.post(f'/updates/{identifier}/edit', data={
            'title': 'Revised regression announcement', 'content': '<p>Meeting moved.</p>',
        })
        assert response.status_code == 302
        with app.app_context():
            update = db.session.get(CompanyUpdate, identifier)
            assert (update.title, update.content) == ('Revised regression announcement', '<p>Meeting moved.</p>')
        for client in (owner_a_client, agent_a_client):
            response = client.get('/updates')
            assert response.status_code == 200
            assert b'Revised regression announcement' in response.data
        response = agent_a_client.get(f'/updates/{identifier}')
        assert response.status_code == 200
        assert b'Meeting moved.' in response.data
        assert owner_a_client.get('/api/updates/latest').json['id'] == identifier
    finally:
        with app.app_context():
            db.session.delete(db.session.get(CompanyUpdate, identifier))
            db.session.commit()


@pytest.mark.parametrize('method,path', [
    ('get', '/updates/new'), ('post', '/updates/new'),
    ('get', '/updates/{id}/edit'), ('post', '/updates/{id}/delete'),
])
def test_agent_cannot_manage_updates(agent_a_client, update_id, app, method, path):
    response = getattr(agent_a_client, method)(path.format(id=update_id), data={
        'title': 'Unauthorized update', 'content': 'Not allowed',
    })
    assert response.status_code == 403
    with app.app_context():
        assert CompanyUpdate.query.get(update_id).title == 'Regression announcement'
        assert CompanyUpdate.query.filter_by(title='Unauthorized update').count() == 0


@pytest.mark.parametrize('method,path', [
    ('get', '/updates/{id}'),
    ('post', '/api/updates/{id}/reactions'),
    ('post', '/api/updates/{id}/comments'),
])
def test_other_org_cannot_read_or_engage(owner_b_client, update_id, app, method, path):
    response = getattr(owner_b_client, method)(path.format(id=update_id), json={
        'reaction_type': 'thumbs_up', 'content': 'Unauthorized comment',
    })
    assert response.status_code == 404
    with app.app_context():
        assert CompanyUpdateComment.query.filter_by(update_id=update_id).count() == 0
        assert CompanyUpdateReaction.query.filter_by(update_id=update_id).count() == 0


def test_reaction_toggle_is_visible_and_reversible(owner_a_client, update_id, seed):
    url = f'/api/updates/{update_id}/reactions'
    response = owner_a_client.post(url, json={'reaction_type': 'thumbs_up'})
    assert response.status_code == 200
    assert response.json['action'] == 'added'
    data = owner_a_client.get(url).json
    assert data['reaction_counts']['thumbs_up'] == 1
    assert data['reactions_by_type']['thumbs_up'][0]['user_id'] == seed['owner_a']
    assert owner_a_client.post(url, json={'reaction_type': 'thumbs_up'}).json['action'] == 'removed'
    data = owner_a_client.get(url).json
    assert data['reaction_counts'].get('thumbs_up', 0) == 0
    assert data['user_reactions'] == []


def test_comment_persists_with_author(owner_a_client, update_id, seed):
    url = f'/api/updates/{update_id}/comments'
    response = owner_a_client.post(url, json={'content': 'Agenda received.'})
    assert response.status_code == 200
    identifier = response.json['id']
    data = owner_a_client.get(url).json
    assert data['comment_count'] == 1
    assert [(row['id'], row['user_id'], row['content']) for row in data['comments']] == [
        (identifier, seed['owner_a'], 'Agenda received.')]
