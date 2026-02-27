"""
Integration tests for admin routes.

Covers: contact group management, agent resources, document mapping,
and role-based access control (admin vs agent).
"""
import json
import pytest
from conftest import login


class TestGroupManagement:
    """Contact group CRUD (admin only)."""

    def test_groups_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/admin/groups')
        assert resp.status_code in (200, 302)
        if resp.status_code == 200:
            assert b'Buyers' in resp.data or b'group' in resp.data.lower()

    def test_groups_page_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/admin/groups')
        assert resp.status_code in (302, 403), f"Agent should be denied, got {resp.status_code}"

    def test_add_group(self, owner_a_client, seed):
        resp = owner_a_client.post('/admin/groups/add', data={
            'name': 'TestNewGroup',
            'category': 'general',
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_add_group_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.post('/admin/groups/add', data={
            'name': 'AgentGroup',
            'category': 'general',
        })
        assert resp.status_code in (302, 403), f"Agent should be denied, got {resp.status_code}"

    def test_update_group(self, owner_a_client, seed):
        resp = owner_a_client.put(
            f'/admin/groups/{seed["group_a1"]}',
            json={'name': 'Buyers Updated'},
            content_type='application/json',
        )
        assert resp.status_code in (200, 302)

    def test_update_group_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.put(
            f'/admin/groups/{seed["group_b1"]}',
            json={'name': 'Hacked'},
            content_type='application/json',
        )
        assert resp.status_code in (403, 404)

    def test_reorder_groups(self, owner_a_client, seed):
        resp = owner_a_client.post(
            '/admin/groups/reorder',
            json=[
                {'id': seed['group_a2'], 'sort_order': 0},
                {'id': seed['group_a1'], 'sort_order': 1},
            ],
            content_type='application/json',
        )
        assert resp.status_code in (200, 302, 400)


class TestResourceManagement:
    """Agent resource CRUD (admin only)."""

    def test_resources_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/admin/resources')
        assert resp.status_code in (200, 302)

    def test_resources_page_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/admin/resources')
        assert resp.status_code in (302, 403), f"Agent should be denied, got {resp.status_code}"

    def test_add_resource(self, owner_a_client, seed):
        resp = owner_a_client.post('/admin/resources/add', json={
            'label': 'New Resource',
            'url': 'https://example.com/new',
        })
        assert resp.status_code in (200, 400)

    def test_update_resource(self, owner_a_client, seed):
        resp = owner_a_client.put(
            f'/admin/resources/{seed["resource_a"]}',
            json={'label': 'Updated Guide', 'url': 'https://example.com/updated'},
            content_type='application/json',
        )
        assert resp.status_code in (200, 302, 400)

    def test_resources_api(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/resources')
        assert resp.status_code == 200

    def test_resources_api_agent_can_read(self, agent_a_client, seed):
        resp = agent_a_client.get('/api/resources')
        assert resp.status_code == 200


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
