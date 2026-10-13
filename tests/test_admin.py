"""
Integration tests for admin routes.

Covers: contact group management, agent resources, document mapping,
and role-based access control (admin vs agent).
"""


class TestLegacyGroupRedirects:
    """Legacy /admin/groups routes redirect or return 410."""

    def test_admin_groups_redirects_to_customize(self, owner_a_client, seed):
        resp = owner_a_client.get('/admin/groups', follow_redirects=False)
        assert resp.status_code == 301
        assert '/groups' in (resp.headers.get('Location') or '')

    def test_legacy_mutations_gone(self, owner_a_client, seed):
        resp = owner_a_client.post('/admin/groups/add', data={
            'name': 'TestNewGroup',
            'category': 'general',
        })
        assert resp.status_code == 410


class TestResourceManagement:
    def test_admin_resource_page_and_agent_denial(self, owner_a_client, agent_a_client):
        assert owner_a_client.get('/admin/resources').status_code == 200
        denied = agent_a_client.get('/admin/resources')
        assert denied.status_code == 302
        assert denied.location.endswith('/contacts')

    def test_resource_create_edit_and_sidebar_visibility(self, owner_a_client, agent_a_client, owner_b_client, app, db):
        from models import AgentResource
        response = owner_a_client.post('/admin/resources/add', json={
            'label': 'Regression guide', 'url': 'https://example.com/guide',
        })
        assert response.status_code == 200
        resource_id = response.json['resource']['id']
        try:
            response = owner_a_client.put(f'/admin/resources/{resource_id}', json={
                'label': 'Revised guide', 'url': 'https://example.com/revised',
            })
            assert response.status_code == 200
            for client in (owner_a_client, agent_a_client):
                resources = client.get('/api/resources').json['resources']
                resource = next(row for row in resources if row['id'] == resource_id)
                assert (resource['label'], resource['url']) == ('Revised guide', 'https://example.com/revised')
            assert resource_id not in [row['id'] for row in owner_b_client.get('/api/resources').json['resources']]
        finally:
            with app.app_context():
                db.session.delete(db.session.get(AgentResource, resource_id))
                db.session.commit()


class TestDocumentMapping:
    """Document mapping admin routes."""

    def test_mapping_list_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/admin/document-mapping')
        assert resp.status_code in (200, 302)

    def test_mapping_list_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/admin/document-mapping')
        assert resp.status_code in (302, 403), f"Agent should be denied, got {resp.status_code}"

    def test_mapper_v2_list(self, owner_a_client, seed):
        resp = owner_a_client.get('/admin/document-mapper-v2')
        assert resp.status_code in (200, 302)
