"""
Integration tests for authentication routes.

Covers: login, logout, registration, profile view/update, password reset,
user management (admin), and access control.
"""
from pathlib import Path
import pytest
from conftest import login
from models import ContactGroup, TaskType, TransactionType, User


class TestLogin:
    """Login and session management."""

    def test_login_page_loads(self, client, seed):
        resp = client.get('/login')
        assert resp.status_code in (200, 302)
        if resp.status_code == 200:
            assert b'login' in resp.data.lower() or b'Log In' in resp.data or b'Sign In' in resp.data

    def test_login_success(self, client, seed):
        resp = login(client, 'owner_a')
        assert resp.status_code == 200
        assert b'Dashboard' in resp.data or b'dashboard' in resp.data.lower()

    def test_login_wrong_password(self, client, seed):
        client.get('/logout')
        resp = client.post('/login', data={
            'username': 'owner_a', 'password': 'wrongpassword',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'Invalid' in resp.data or b'incorrect' in resp.data.lower()

    def test_login_nonexistent_user(self, client, seed):
        client.get('/logout')  # Ensure no session carryover from other tests
        resp = client.post('/login', data={
            'username': 'nonexistent_user_xyz_999', 'password': 'wrongpass',
        }, follow_redirects=True)
        # Should not reach dashboard (has dashboard-card). Expect error or login form.
        assert b'dashboard-card' not in resp.data, "Should not reach dashboard with invalid creds"
        assert (
            b'Invalid' in resp.data or b'incorrect' in resp.data.lower()
            or b'Sign in' in resp.data or b'sign in' in resp.data.lower()
        )

    def test_logout(self, client, seed):
        login(client, 'owner_a')
        resp = client.get('/logout', follow_redirects=True)
        assert resp.status_code == 200
        dash = client.get('/dashboard')
        assert dash.status_code in (302, 401)


class TestProtectedRoutes:
    """Verify unauthenticated users are redirected to login."""

    @pytest.mark.parametrize('url', [
        '/dashboard', '/contacts', '/tasks', '/profile',
        '/tasks/new', '/contacts/create', '/admin/groups',
    ])
    def test_unauthenticated_redirect(self, client, seed, url):
        resp = client.get(url)
        assert resp.status_code in (302, 401, 403)

    def test_unauthenticated_post_blocked(self, client, seed):
        resp = client.post('/profile/update', data={'first_name': 'Hacker'})
        assert resp.status_code in (302, 401, 403)


class TestProfile:
    """Profile view and update."""

    def test_view_profile(self, owner_a_client, seed):
        resp = owner_a_client.get('/profile')
        assert resp.status_code == 200
        assert b'Alice' in resp.data or b'owner_a' in resp.data

    def test_update_profile(self, owner_a_client, seed):
        resp = owner_a_client.post('/profile/update', data={
            'first_name': 'AliceUpdated',
            'last_name': 'Owner',
            'email': 'owner_a@test.com',
            'phone': '5559999999',
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_agent_can_view_own_profile(self, agent_a_client, seed):
        resp = agent_a_client.get('/profile')
        assert resp.status_code == 200
        assert b'Amy' in resp.data or b'agent_a' in resp.data


class TestRegistration:
    """Registration form."""

    def test_register_page_loads(self, client, seed):
        resp = client.get('/register')
        assert resp.status_code in (200, 302)

    def test_register_template_shows_confirm_password_and_not_phone(self, seed):
        template = Path(__file__).resolve().parents[1] / 'templates' / 'auth' / 'register.html'
        contents = template.read_text()
        assert 'form.confirm_password' in contents
        assert 'Confirm password' in contents
        assert 'form.phone' not in contents

    def test_register_allows_immediate_login(self, client, seed):
        resp = client.post('/register', data={
            'company_name': 'Fast Lane Realty',
            'first_name': 'Nina',
            'last_name': 'Agent',
            'email': 'nina@test.com',
            'password': 'supersecure123',
            'confirm_password': 'supersecure123',
        }, follow_redirects=True)

        assert resp.status_code == 200
        resp = client.get('/logout', follow_redirects=True)
        assert resp.status_code == 200

        login_resp = client.post('/login', data={
            'username': 'nina@test.com',
            'password': 'supersecure123',
        }, follow_redirects=True)

        assert login_resp.status_code == 200
        assert b'pending approval' not in login_resp.data.lower()
        assert b'Dashboard' in login_resp.data or b'dashboard' in login_resp.data.lower()

    def test_register_seeds_default_org_data(self, client, app, seed):
        resp = client.post('/register', data={
            'company_name': 'Seeded Realty',
            'first_name': 'Sam',
            'last_name': 'Seeder',
            'email': 'sam.seeded@test.com',
            'password': 'supersecure123',
            'confirm_password': 'supersecure123',
        }, follow_redirects=True)

        assert resp.status_code == 200

        with app.app_context():
            user = User.query.filter_by(email='sam.seeded@test.com').first()
            assert user is not None
            assert ContactGroup.query.filter_by(organization_id=user.organization_id).count() > 0
            assert TaskType.query.filter_by(organization_id=user.organization_id).count() > 0
            assert TransactionType.query.filter_by(organization_id=user.organization_id).count() > 0

    def test_terms_privacy_page(self, client, seed):
        resp = client.get('/terms-privacy')
        assert resp.status_code == 200


class TestPasswordReset:
    """Password reset flow."""

    def test_reset_request_page_loads(self, client, seed):
        resp = client.get('/reset_password')
        assert resp.status_code in (200, 302)

    def test_reset_with_invalid_token(self, client, seed):
        resp = client.get('/reset_password/invalidtoken123')
        assert resp.status_code in (200, 302)


class TestUserManagement:
    """Admin user management routes."""

    def test_manage_users_admin(self, owner_a_client, seed):
        resp = owner_a_client.get('/manage-users')
        assert resp.status_code == 200

    def test_manage_users_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.get('/manage-users')
        assert resp.status_code in (302, 403)

    def test_update_user_role_admin(self, owner_a_client, seed):
        resp = owner_a_client.post(f"/user/{seed['agent_a']}/role", data={
            'role': 'admin',
        }, follow_redirects=True)
        assert resp.status_code == 200
        # Restore agent role so later tests (agent_admin_pages_denied) aren't affected
        owner_a_client.post(f"/user/{seed['agent_a']}/role", data={
            'role': 'agent',
        }, follow_redirects=True)

    def test_update_user_role_agent_denied(self, agent_a_client, seed):
        resp = agent_a_client.post(f"/user/{seed['owner_a']}/role", data={
            'role': 'agent',
        })
        assert resp.status_code in (302, 403)

    def test_view_user_action_plan_admin(self, owner_a_client, seed):
        resp = owner_a_client.get(f"/user/{seed['agent_a']}/action-plan")
        assert resp.status_code in (200, 302)

    def test_edit_user_page_admin(self, owner_a_client, seed):
        resp = owner_a_client.get(f"/user/{seed['agent_a']}/edit")
        assert resp.status_code in (200, 302)
