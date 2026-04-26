"""
Integration tests for JSON API endpoints.

Covers: AI chat, action plan, daily todo, and miscellaneous
API endpoints. Tests both access control and basic response formats.
"""
import json
import pytest
from conftest import login


class TestAIChatAPI:
    """AI Chat (B.O.B.) API endpoints."""

    def test_list_conversations(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/ai-chat/conversations')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, (list, dict))

    def test_create_conversation(self, owner_a_client, seed):
        resp = owner_a_client.post(
            '/api/ai-chat/conversations',
            json={'title': 'Test Conversation'},
            content_type='application/json',
        )
        assert resp.status_code in (200, 201)

    def test_clear_chat(self, owner_a_client, seed):
        resp = owner_a_client.post('/api/ai-chat/clear')
        assert resp.status_code == 200

    def test_search_contacts_for_mentions(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/ai-chat/search-contacts?q=Jane')
        assert resp.status_code == 200

    def test_chat_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.post('/api/ai-chat', json={'message': 'Hello'})
        assert resp.status_code in (302, 401, 403)


class TestActionPlanAPI:
    """Action plan API endpoints."""

    def test_action_plan_page(self, owner_a_client, seed):
        resp = owner_a_client.get('/action-plan')
        assert resp.status_code == 200

    def test_get_action_plan(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/action-plan/get')
        assert resp.status_code == 200

    def test_action_plan_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.get('/action-plan')
        assert resp.status_code in (302, 401, 403)


class TestDailyTodoAPI:
    """Daily todo API endpoints."""

    def test_get_latest_todo_globally_disabled(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/daily-todo/latest')
        assert resp.status_code in (302, 403)

    def test_daily_todo_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.get('/api/daily-todo/latest')
        assert resp.status_code in (302, 401, 403, 404)


class TestTaskWindowAPI:
    """Task window preference API."""

    def test_update_valid_window(self, owner_a_client, seed):
        resp = owner_a_client.post(
            '/api/update-task-window',
            json={'days': 30},
            content_type='application/json',
        )
        assert resp.status_code == 200

    def test_update_window_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.post(
            '/api/update-task-window',
            json={'days': 30},
            content_type='application/json',
        )
        assert resp.status_code in (302, 401, 403)


class TestResourcesAPI:
    """Agent resources API."""

    def test_get_resources(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/resources')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, (list, dict))

    def test_resources_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.get('/api/resources')
        assert resp.status_code in (302, 401, 403)


class TestRegistrationStatusAPI:
    """Registration status endpoint."""

    def test_check_status(self, client, seed):
        resp = client.get('/registration-status?email=owner_a@test.com')
        assert resp.status_code == 200


class TestHealthAPI:
    """Health check endpoints."""

    def test_health_json(self, client, seed):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'status' in data
        assert data['status'] == 'healthy'

    def test_health_ui(self, client, seed):
        resp = client.get('/health/ui')
        assert resp.status_code == 200
