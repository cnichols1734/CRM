"""
Integration tests for task routes.

Covers: CRUD, filtering, quick-update, subtypes API, cross-org isolation.
"""
import pytest
from conftest import login


class TestTaskList:
    """Task listing and filtering."""

    def test_tasks_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/tasks')
        assert resp.status_code == 200
        assert b'Call Jane' in resp.data or b'Task' in resp.data

    def test_tasks_filter_status(self, owner_a_client, seed):
        resp = owner_a_client.get('/tasks?status=pending')
        assert resp.status_code == 200

    def test_tasks_filter_completed(self, owner_a_client, seed):
        resp = owner_a_client.get('/tasks?status=completed')
        assert resp.status_code == 200

    def test_tasks_filter_priority(self, owner_a_client, seed):
        resp = owner_a_client.get('/tasks?priority=high')
        assert resp.status_code == 200

    def test_tasks_no_cross_org_leakage(self, owner_a_client, seed):
        resp = owner_a_client.get('/tasks')
        assert b'Task B Only' not in resp.data


class TestTaskView:
    """View individual tasks."""

    def test_view_own_task(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/tasks/{seed["task_a"]}')
        assert resp.status_code == 200
        assert b'Call Jane' in resp.data or b'Jane' in resp.data

    def test_view_task_ajax(self, owner_a_client, seed):
        resp = owner_a_client.get(
            f'/tasks/{seed["task_a"]}',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200

    def test_view_cross_org_task_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/tasks/{seed["task_b"]}')
        assert resp.status_code == 404

    def test_view_nonexistent_task(self, owner_a_client, seed):
        resp = owner_a_client.get('/tasks/99999')
        assert resp.status_code == 404


class TestTaskCreate:
    """Task creation."""

    def test_create_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/tasks/new')
        assert resp.status_code == 200

    def test_create_task_success(self, owner_a_client, seed):
        resp = owner_a_client.post('/tasks/new', data={
            'contact_id': str(seed['contact_a']),
            'type_id': str(seed['task_type_a']),
            'subtype_id': str(seed['subtype_a']),
            'subject': 'New Integration Test Task',
            'description': 'Created by integration test',
            'priority': 'low',
            'due_date': '2026-03-15',
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_create_task_missing_fields(self, owner_a_client, seed):
        resp = owner_a_client.post('/tasks/new', data={
            'subject': '',
        }, follow_redirects=True)
        assert resp.status_code == 200


class TestTaskEdit:
    """Task editing."""

    def test_edit_task(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/tasks/{seed["task_a"]}/edit', data={
            'subject': 'Edited Task Subject',
            'status': 'pending',
            'priority': 'high',
            'due_date': '2026-04-01',
            'contact_id': str(seed['contact_a']),
            'type_id': str(seed['task_type_a']),
            'subtype_id': str(seed['subtype_a']),
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_edit_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/tasks/{seed["task_b"]}/edit', data={
            'subject': 'Hacked', 'status': 'completed',
            'priority': 'high', 'due_date': '2026-12-31',
        })
        assert resp.status_code == 404


class TestTaskQuickUpdate:
    """Quick status/priority updates."""

    def test_quick_update_status(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/tasks/{seed["task_a"]}/quick-update', data={
            'status': 'completed',
        })
        assert resp.status_code in (200, 302)

    def test_quick_update_priority(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/tasks/{seed["task_a"]}/quick-update', data={
            'priority': 'low',
        })
        assert resp.status_code in (200, 302)

    def test_quick_update_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/tasks/{seed["task_b"]}/quick-update', data={
            'status': 'completed',
        })
        assert resp.status_code == 404


class TestTaskDelete:
    """Task deletion."""

    def test_delete_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/tasks/{seed["task_b"]}/delete')
        assert resp.status_code == 404


class TestTaskSubtypes:
    """Task subtypes API."""

    def test_get_subtypes(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/tasks/types/{seed["task_type_a"]}/subtypes')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_subtypes_cross_org_returns_empty(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/tasks/types/{seed["task_type_b"]}/subtypes')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 0


class TestTaskAgentAccess:
    """Agent-level access to tasks."""

    def test_agent_sees_tasks_page(self, agent_a_client, seed):
        resp = agent_a_client.get('/tasks')
        assert resp.status_code == 200

    def test_agent_can_create_task(self, agent_a_client, seed):
        resp = agent_a_client.get('/tasks/new')
        assert resp.status_code == 200

    def test_agent_can_view_assigned_task(self, agent_a_client, seed):
        resp = agent_a_client.get(f'/tasks/{seed["task_a2"]}')
        assert resp.status_code == 200
