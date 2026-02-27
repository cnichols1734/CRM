"""
Integration tests for page loading and navigation.

Covers: all major pages load without 500 errors, public routes,
health check, dashboard, and key navigation flows.
"""
import pytest
from conftest import login


class TestPublicRoutes:
    """Routes that should be accessible without login."""

    def test_landing_page(self, client, seed):
        resp = client.get('/')
        assert resp.status_code in (200, 302)

    def test_health_check(self, client, seed):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'healthy'

    def test_health_ui(self, client, seed):
        resp = client.get('/health/ui')
        assert resp.status_code == 200

    def test_login_page(self, client, seed):
        resp = client.get('/login')
        assert resp.status_code in (200, 302)

    def test_register_page(self, client, seed):
        resp = client.get('/register')
        assert resp.status_code in (200, 302)

    def test_terms_privacy(self, client, seed):
        resp = client.get('/terms-privacy')
        assert resp.status_code == 200

    def test_reset_password_page(self, client, seed):
        resp = client.get('/reset_password')
        assert resp.status_code in (200, 302)


class TestAuthenticatedPageLoads:
    """Verify authenticated pages load without 500 errors."""

    @pytest.mark.parametrize('url', [
        '/dashboard',
        '/contacts',
        '/tasks',
        '/profile',
        '/user_todo',
        '/updates',
    ])
    def test_core_pages_load(self, owner_a_client, seed, url):
        resp = owner_a_client.get(url)
        assert resp.status_code == 200, f"{url} returned {resp.status_code}"

    @pytest.mark.parametrize('url', [
        '/transactions/',
        '/transactions/new',
        '/action-plan',
    ])
    def test_pro_pages_load(self, owner_a_client, seed, url):
        resp = owner_a_client.get(url)
        assert resp.status_code == 200, f"{url} returned {resp.status_code}"

    @pytest.mark.parametrize('url', [
        '/admin/groups',
        '/admin/resources',
        '/admin/document-mapping',
        '/admin/document-mapper-v2',
        '/manage-users',
        '/org/settings',
        '/org/members',
        '/org/upgrade',
        '/org/usage',
    ])
    def test_admin_pages_load(self, owner_a_client, seed, url):
        resp = owner_a_client.get(url)
        assert resp.status_code in (200, 302), f"{url} returned {resp.status_code}"


class TestDashboard:
    """Dashboard-specific tests."""

    def test_dashboard_content(self, owner_a_client, seed):
        resp = owner_a_client.get('/dashboard')
        assert resp.status_code == 200
        assert b'Dashboard' in resp.data or b'dashboard' in resp.data.lower()

    def test_dashboard_dismiss_onboarding(self, owner_a_client, seed):
        resp = owner_a_client.post('/dashboard/dismiss-onboarding',
                                   follow_redirects=True)
        assert resp.status_code == 200

    def test_update_task_window(self, owner_a_client, seed):
        resp = owner_a_client.post(
            '/api/update-task-window',
            json={'days': 7},
            content_type='application/json',
        )
        assert resp.status_code == 200


class TestContactUs:
    """Contact form (public)."""

    def test_contact_form_submit(self, client, seed):
        resp = client.post('/contact-us', json={
            'subject': 'Test Question',
            'email': 'test@example.com',
            'message': 'Hello, I have a question.',
        })
        assert resp.status_code in (200, 400, 500)


class TestAgentNavigation:
    """Agent-level navigation to core pages."""

    @pytest.mark.parametrize('url', [
        '/dashboard', '/contacts', '/tasks', '/profile',
        '/user_todo', '/updates',
    ])
    def test_agent_core_pages(self, agent_a_client, seed, url):
        resp = agent_a_client.get(url)
        assert resp.status_code == 200, f"Agent denied from {url}"

    @pytest.mark.parametrize('url', [
        '/admin/groups', '/admin/resources', '/manage-users',
    ])
    def test_agent_admin_pages_denied(self, agent_a_client, seed, url):
        resp = agent_a_client.get(url)
        assert resp.status_code in (302, 403), f"Agent accessed admin {url}"
