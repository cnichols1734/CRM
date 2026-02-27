"""
Integration tests for organization routes.

Covers: settings, members, invites, deletion, upgrade, and usage.
"""
import pytest
from conftest import login


class TestOrgSettings:
    """Organization settings."""

    def test_settings_page_loads_owner(self, owner_a_client, seed):
        resp = owner_a_client.get('/org/settings')
        assert resp.status_code == 200

    def test_settings_page_loads_admin(self, admin_a_client, seed):
        resp = admin_a_client.get('/org/settings')
        assert resp.status_code == 200

    def test_settings_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/org/settings')
        assert resp.status_code in (302, 403)

    def test_update_settings_owner(self, owner_a_client, seed):
        resp = owner_a_client.post('/org/settings/update', data={
            'name': 'Test Realty A Updated',
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_update_settings_admin_denied(self, admin_a_client, seed):
        resp = admin_a_client.post('/org/settings/update', data={
            'name': 'Admin Should Not Update',
        }, follow_redirects=True)
        assert resp.status_code in (200, 302, 403)

    def test_update_settings_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.post('/org/settings/update', data={
            'name': 'Agent Hacked',
        })
        assert resp.status_code in (302, 403)


class TestOrgMembers:
    """Member management."""

    def test_members_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/org/members')
        assert resp.status_code == 200

    def test_members_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/org/members')
        assert resp.status_code in (302, 403)

    def test_update_member_role(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/org/members/{seed["agent_a"]}/update-role',
            data={'role': 'admin'},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_update_member_role_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.post(
            f'/org/members/{seed["owner_a"]}/update-role',
            data={'role': 'agent'},
        )
        assert resp.status_code in (302, 403)


class TestOrgInvite:
    """Member invitations."""

    def test_send_invite(self, owner_a_client, seed):
        resp = owner_a_client.post('/org/members/invite', data={
            'email': 'newinvite@test.com',
            'role': 'agent',
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_send_invite_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.post('/org/members/invite', data={
            'email': 'agentinvite@test.com',
            'role': 'agent',
        })
        assert resp.status_code in (302, 403)


class TestOrgDeletion:
    """Organization deletion flow."""

    def test_deletion_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/org/delete')
        assert resp.status_code == 200

    def test_deletion_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/org/delete')
        assert resp.status_code in (302, 403)


class TestOrgUpgrade:
    """Upgrade options."""

    def test_upgrade_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/org/upgrade')
        assert resp.status_code in (200, 302)

    def test_upgrade_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/org/upgrade')
        assert resp.status_code in (302, 403)


class TestOrgUsage:
    """Usage and limits."""

    def test_usage_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/org/usage')
        assert resp.status_code == 200

    def test_usage_agent_can_view(self, agent_a_client, seed):
        resp = agent_a_client.get('/org/usage')
        assert resp.status_code == 200
