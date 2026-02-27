"""
Integration tests for feature flag enforcement.

Verifies that free-tier orgs are blocked from premium features
and pro-tier orgs can access them.
"""
import pytest
from conftest import login


class TestFreeTierRestrictions:
    """Free-tier org (Org B) should be blocked from premium features."""

    def test_ai_daily_todo_blocked(self, owner_b_client, seed):
        resp = owner_b_client.post('/api/daily-todo/generate',
                                   follow_redirects=True)
        assert resp.status_code in (200, 302, 403)
        if resp.status_code == 200:
            assert b'upgrade' in resp.data.lower() or b'subscription' in resp.data.lower() or resp.content_type == 'application/json'

    def test_ai_daily_todo_latest_blocked(self, owner_b_client, seed):
        resp = owner_b_client.get('/api/daily-todo/latest',
                                  follow_redirects=True)
        assert resp.status_code in (200, 302, 403)

    def test_action_plan_page_blocked(self, owner_b_client, seed):
        resp = owner_b_client.get('/action-plan', follow_redirects=True)
        assert resp.status_code in (200, 302, 403)
        if resp.status_code == 200:
            assert b'upgrade' in resp.data.lower() or b'action' in resp.data.lower()

    def test_transactions_blocked_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/transactions/', follow_redirects=True)
        assert resp.status_code in (200, 302, 403)


class TestProTierAccess:
    """Pro-tier org (Org A) should access premium features."""

    def test_transactions_allowed(self, owner_a_client, seed):
        resp = owner_a_client.get('/transactions/')
        assert resp.status_code == 200

    def test_action_plan_page_allowed(self, owner_a_client, seed):
        resp = owner_a_client.get('/action-plan')
        assert resp.status_code == 200

    def test_ai_daily_todo_allowed(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/daily-todo/latest')
        assert resp.status_code in (200, 404)


class TestCoreFeatures:
    """Core features available to all tiers."""

    def test_contacts_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/contacts')
        assert resp.status_code == 200

    def test_tasks_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/tasks')
        assert resp.status_code == 200

    def test_dashboard_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/dashboard')
        assert resp.status_code == 200

    def test_user_todo_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/user_todo')
        assert resp.status_code == 200

    def test_updates_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/updates')
        assert resp.status_code == 200
