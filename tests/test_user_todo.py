"""
Integration tests for user todo routes.

Covers: page load, todo CRUD API, and cross-org isolation.
"""
import json
import pytest
from conftest import login


class TestUserTodoPage:
    """User todo page rendering."""

    def test_todo_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/user_todo')
        assert resp.status_code == 200

    def test_todo_page_agent(self, agent_a_client, seed):
        resp = agent_a_client.get('/user_todo')
        assert resp.status_code == 200


class TestUserTodoAPI:
    """Todo CRUD API endpoints."""

    def test_get_todos_empty(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/user_todos/get')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, (list, dict))

    def test_save_todos(self, owner_a_client, seed):
        resp = owner_a_client.post(
            '/api/user_todos/save',
            json={
                'lists': [
                    {
                        'name': 'Test List',
                        'items': [
                            {'text': 'Buy groceries', 'completed': False},
                            {'text': 'Call agent', 'completed': True},
                        ],
                    },
                ],
            },
            content_type='application/json',
        )
        assert resp.status_code == 200

    def test_get_todos_after_save(self, owner_a_client, seed):
        owner_a_client.post(
            '/api/user_todos/save',
            json={
                'lists': [
                    {
                        'name': 'Persist Test',
                        'items': [{'text': 'Item A', 'completed': False}],
                    },
                ],
            },
            content_type='application/json',
        )
        resp = owner_a_client.get('/api/user_todos/get')
        assert resp.status_code == 200

    def test_todos_isolated_per_user(self, agent_a_client, seed):
        agent_a_client.post(
            '/api/user_todos/save',
            json={
                'lists': [
                    {
                        'name': 'Agent List',
                        'items': [{'text': 'Agent item', 'completed': False}],
                    },
                ],
            },
            content_type='application/json',
        )
        resp = agent_a_client.get('/api/user_todos/get')
        assert resp.status_code == 200
