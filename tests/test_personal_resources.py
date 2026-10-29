import pytest

from models import PersonalResource, db


def create_resource(client, label='My MLS', url='example.com/mls'):
    client.get('/resources/new')
    return post_form(client, '/resources/new', {'label': label, 'url': url})


def post_form(client, path, data=None):
    with client.session_transaction() as session:
        token = session.get('personal_resources_csrf', '')
    return client.post(path, data={**(data or {}), 'csrf_token': token})


def test_agent_resource_lifecycle(app, seed, agent_a_client):
    assert agent_a_client.get('/resources/').status_code == 200
    assert create_resource(agent_a_client).status_code == 302
    with app.app_context():
        item = PersonalResource.query.filter_by(user_id=seed['agent_a'], label='My MLS').one()
        resource_id = item.id
        assert item.organization_id == seed['org_a']
        assert item.url == 'https://example.com/mls'
    resources = agent_a_client.get('/api/resources').get_json()['resources']
    assert any(r['label'] == 'Training Guide' for r in resources)
    assert any(r['label'] == 'My MLS' for r in resources)
    assert agent_a_client.get(f'/resources/{resource_id}/edit').status_code == 200
    assert post_form(agent_a_client, f'/resources/{resource_id}/edit', {
        'label': 'Updated MLS', 'url': 'https://example.com/new'
    }).status_code == 302
    assert agent_a_client.get(f'/resources/{resource_id}/delete').status_code == 200
    with app.app_context():
        assert db.session.get(PersonalResource, resource_id) is not None
    assert post_form(agent_a_client, f'/resources/{resource_id}/delete').status_code == 302
    with app.app_context():
        assert db.session.get(PersonalResource, resource_id) is None


def test_personal_resources_are_private(app, seed, agent_a_client, owner_a_client, owner_b_client):
    assert create_resource(agent_a_client, 'Private link').status_code == 302
    with app.app_context():
        resource_id = PersonalResource.query.filter_by(label='Private link').one().id
    for other in (owner_a_client, owner_b_client):
        assert b'Private link' not in other.get('/resources/').data
        assert all(r['label'] != 'Private link' for r in other.get('/api/resources').get_json()['resources'])
        for action in ('edit', 'delete'):
            assert other.get(f'/resources/{resource_id}/{action}').status_code == 404
            assert other.post(f'/resources/{resource_id}/{action}', data={'label': 'Stolen', 'url': 'https://example.com'}).status_code == 404
    # Organization scope still applies if an agent changes organizations.
    with app.app_context():
        item = db.session.get(PersonalResource, resource_id)
        item.organization_id = seed['org_b']
        db.session.commit()
    assert agent_a_client.get(f'/resources/{resource_id}/edit').status_code == 404
    assert all(r['label'] != 'Private link' for r in agent_a_client.get('/api/resources').get_json()['resources'])


def test_free_tier_can_save_personal_links(owner_b_client):
    assert create_resource(owner_b_client, 'Free tier resource').status_code == 302
    assert any(r['label'] == 'Free tier resource' for r in owner_b_client.get('/api/resources').get_json()['resources'])


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,test', '//evil.example', 'https://', 'https://user:pass@example.com', 'https://example.com:bad', 'https://example.com/\npath', 'https://example.com\\@evil.example', 'a' * 2001])
def test_invalid_urls_are_rejected(agent_a_client, url):
    assert create_resource(agent_a_client, 'Invalid link', url).status_code == 400


def test_required_fields_and_length(agent_a_client):
    assert create_resource(agent_a_client, '').status_code == 400
    assert create_resource(agent_a_client, 'x' * 101).status_code == 400
    assert create_resource(agent_a_client, 'Missing URL', '').status_code == 400


def test_anonymous_cannot_manage_resources(client):
    assert client.get('/resources/').status_code == 302
    assert client.post('/resources/new', data={'label': 'x', 'url': 'https://example.com'}).status_code == 302


def test_forms_escape_saved_text_and_include_csrf(agent_a_client):
    assert create_resource(agent_a_client, '<script>alert(1)</script>').status_code == 302
    page = agent_a_client.get('/resources/').data
    assert b'&lt;script&gt;alert(1)&lt;/script&gt;' in page
    assert b'<script>alert(1)</script>' not in page
    assert b'name="csrf_token"' in agent_a_client.get('/resources/new').data


def test_mutations_require_csrf(agent_a_client):
    agent_a_client.get('/resources/new')
    for token in ('', 'invalid', 'malformed-☃'):
        assert agent_a_client.post('/resources/new', data={
            'label': 'Blocked', 'url': 'https://example.com', 'csrf_token': token
        }).status_code == 400
