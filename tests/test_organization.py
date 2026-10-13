"""
Integration tests for organization routes.

Covers: settings, members, invites, deletion, upgrade, and usage.
"""
import pytest
from models import Organization, OrganizationInvite, User, db


class TestOrgSettings:
    """Organization settings."""

    @pytest.mark.parametrize('tier', ['free', 'pro', 'enterprise'])
    def test_settings_and_web_navigation_hide_messages(self, owner_a_client, seed, app, tier):
        with app.app_context():
            org = db.session.get(Organization, seed['org_a'])
            original_tier = org.subscription_tier
            org.subscription_tier = tier
            db.session.commit()
        try:
            resp = owner_a_client.get('/org/settings')
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert 'Organization information' in html
            assert 'href="/contacts"' in html
            assert 'href="/messages"' not in html
            if tier != 'free':
                assert 'Brokerage app link' in html
        finally:
            with app.app_context():
                db.session.get(Organization, seed['org_a']).subscription_tier = original_tier
                db.session.commit()

    def test_settings_page_loads_admin(self, admin_a_client, seed):
        resp = admin_a_client.get('/org/settings')
        assert resp.status_code == 200

    def test_settings_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/org/settings')
        assert resp.status_code in (302, 403)

    @pytest.mark.parametrize('actor, allowed', [('owner_a', True), ('admin_a', False), ('agent_a', False)])
    def test_only_owner_can_save_settings(self, request, app, seed, actor, allowed):
        client = request.getfixturevalue(f'{actor}_client')
        with app.app_context():
            org = db.session.get(Organization, seed['org_a'])
            original_name, original_accent = org.name, org.brand_accent
        try:
            response = client.post('/org/settings/update', data={'name': 'Revised Realty'})
            assert response.status_code == (302 if allowed else 403)
            if allowed:
                assert response.location.endswith('/org/settings')
            with app.app_context():
                assert db.session.get(Organization, seed['org_a']).name == ('Revised Realty' if allowed else original_name)
        finally:
            with app.app_context():
                org = db.session.get(Organization, seed['org_a'])
                org.name, org.brand_accent = original_name, original_accent
                db.session.commit()


class TestOrgMembers:
    """Member management."""

    def test_members_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/org/members')
        assert resp.status_code == 200

    def test_members_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/org/members')
        assert resp.status_code in (302, 403)

    def test_update_member_role(self, owner_a_client, seed, app):
        with app.app_context():
            original = db.session.get(User, seed['agent_a']).org_role
        try:
            response = owner_a_client.post(f'/org/members/{seed["agent_a"]}/update-role', data={'role': 'admin'})
            assert response.status_code == 200
            assert response.json == {'success': True, 'new_role': 'admin'}
            with app.app_context():
                assert db.session.get(User, seed['agent_a']).org_role == 'admin'
        finally:
            with app.app_context():
                db.session.get(User, seed['agent_a']).org_role = original
                db.session.commit()

    def test_update_member_role_agent_denied(self, agent_a_client, seed, app):
        response = agent_a_client.post(f'/org/members/{seed["owner_a"]}/update-role', data={'role': 'agent'})
        assert response.status_code == 403
        with app.app_context():
            assert db.session.get(User, seed['owner_a']).org_role == 'owner'


class TestOrgInvite:
    """Member invitations."""

    def test_send_invite(self, owner_a_client, seed, app, monkeypatch):
        delivered = []
        def send(org, inviter, email, token):
            delivered.append((org.id, inviter.id, email, token))
            return True
        monkeypatch.setattr('services.org_notifications.send_invite_email', send)
        email = 'newinvite@test.com'
        try:
            response = owner_a_client.post('/org/members/invite', data={'email': email, 'role': 'agent'})
            assert response.status_code == 302
            assert response.location.endswith('/org/members')
            with app.app_context():
                invite = OrganizationInvite.query.filter_by(email=email).one()
                assert (invite.organization_id, invite.invited_by_id, invite.role) == (seed['org_a'], seed['owner_a'], 'agent')
                assert delivered == [(seed['org_a'], seed['owner_a'], email, invite.token)]
        finally:
            with app.app_context():
                OrganizationInvite.query.filter_by(email=email).delete()
                db.session.commit()

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
