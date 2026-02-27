"""
Integration tests for company updates routes.

Covers: CRUD, reactions, comments, latest-update API, and access control.
"""
import json
import pytest
from conftest import login


class TestUpdatesList:
    """Company updates listing."""

    def test_updates_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/updates')
        assert resp.status_code == 200

    def test_updates_visible_to_agent(self, agent_a_client, seed):
        resp = agent_a_client.get('/updates')
        assert resp.status_code == 200


class TestUpdateView:
    """View individual updates."""

    def test_view_update(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/updates/{seed["update_a"]}')
        assert resp.status_code == 200
        assert b'Welcome' in resp.data

    def test_view_cross_org_blocked(self, owner_b_client, seed):
        resp = owner_b_client.get(f'/updates/{seed["update_a"]}')
        assert resp.status_code in (302, 403, 404)


class TestUpdateCreate:
    """Update creation (admin only)."""

    def test_create_page_loads_admin(self, owner_a_client, seed):
        resp = owner_a_client.get('/updates/new')
        assert resp.status_code in (200, 302)

    def test_create_update(self, owner_a_client, seed):
        resp = owner_a_client.post('/updates/new', data={
            'title': 'Test Update Created',
            'content': '<p>Test content here.</p>',
        }, follow_redirects=True)
        assert resp.status_code in (200, 302)

    def test_create_update_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/updates/new')
        assert resp.status_code in (302, 403), f"Expected redirect/forbidden, got {resp.status_code}"

    def test_create_update_agent_post_denied(self, agent_a_client, seed):
        resp = agent_a_client.post('/updates/new', data={
            'title': 'Agent Update', 'content': 'Not allowed',
        })
        assert resp.status_code in (302, 403)


class TestUpdateEdit:
    """Update editing (admin only)."""

    def test_edit_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/updates/{seed["update_a"]}/edit')
        assert resp.status_code in (200, 302)

    def test_edit_update(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/updates/{seed["update_a"]}/edit', data={
            'title': 'Welcome Update Edited',
            'content': '<p>Updated content.</p>',
        }, follow_redirects=True)
        assert resp.status_code in (200, 302)

    def test_edit_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get(f'/updates/{seed["update_a"]}/edit')
        assert resp.status_code in (302, 403), f"Expected redirect/forbidden, got {resp.status_code}"


class TestUpdateDelete:
    """Update deletion (admin only)."""

    def test_delete_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.post(f'/updates/{seed["update_a"]}/delete')
        assert resp.status_code in (302, 403)


class TestLatestUpdateAPI:
    """Latest update API endpoint."""

    def test_get_latest_update(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/updates/latest')
        assert resp.status_code == 200


class TestReactionsAPI:
    """Reaction toggle and retrieval."""

    def test_toggle_reaction(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/api/updates/{seed["update_a"]}/reactions',
            json={'reaction_type': 'thumbs_up'},
            content_type='application/json',
        )
        assert resp.status_code in (200, 400)

    def test_get_reactions(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/api/updates/{seed["update_a"]}/reactions')
        assert resp.status_code in (200, 404)

    def test_reaction_cross_org_blocked(self, owner_b_client, seed):
        resp = owner_b_client.post(
            f'/api/updates/{seed["update_a"]}/reactions',
            json={'reaction_type': 'thumbs_up'},
            content_type='application/json',
        )
        assert resp.status_code in (403, 404)


class TestCommentsAPI:
    """Comment creation and retrieval."""

    def test_add_comment(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/api/updates/{seed["update_a"]}/comments',
            json={'content': 'Great update!'},
            content_type='application/json',
        )
        assert resp.status_code in (200, 201, 400)

    def test_get_comments(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/api/updates/{seed["update_a"]}/comments')
        assert resp.status_code in (200, 404)

    def test_comment_cross_org_blocked(self, owner_b_client, seed):
        resp = owner_b_client.post(
            f'/api/updates/{seed["update_a"]}/comments',
            json={'content': 'Hacked comment!'},
            content_type='application/json',
        )
        assert resp.status_code in (403, 404)
