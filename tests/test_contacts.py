"""
Integration tests for contact routes.

Covers: CRUD, search/filtering, interactions, import/export,
group assignment, file operations, and cross-org isolation.
"""
import pytest
from conftest import login


class TestContactList:
    """Contact listing, search, and filtering."""

    def test_contacts_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/contacts')
        assert resp.status_code == 200
        assert b'Jane' in resp.data or b'Contact' in resp.data

    def test_contacts_search(self, owner_a_client, seed):
        resp = owner_a_client.get('/contacts?q=Jane')
        assert resp.status_code == 200
        assert b'Jane' in resp.data

    def test_contacts_search_no_results(self, owner_a_client, seed):
        resp = owner_a_client.get('/contacts?q=NonExistentXYZ')
        assert resp.status_code == 200

    def test_contacts_group_filter(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contacts?group_id={seed["group_a1"]}')
        assert resp.status_code == 200

    def test_contacts_pagination(self, owner_a_client, seed):
        resp = owner_a_client.get('/contacts?page=1')
        assert resp.status_code == 200


class TestContactView:
    """View individual contacts."""

    def test_view_own_contact(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_a"]}')
        assert resp.status_code == 200
        assert b'Jane' in resp.data

    def test_view_contact_ajax(self, owner_a_client, seed):
        resp = owner_a_client.get(
            f'/contact/{seed["contact_a"]}',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200

    def test_view_cross_org_contact_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_b"]}')
        assert resp.status_code == 404

    def test_view_nonexistent_contact(self, owner_a_client, seed):
        resp = owner_a_client.get('/contact/99999')
        assert resp.status_code == 404


class TestContactCreate:
    """Contact creation."""

    def test_create_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/contacts/create')
        assert resp.status_code == 200

    def test_create_contact_success(self, owner_a_client, seed):
        resp = owner_a_client.post('/contacts/create', data={
            'first_name': 'NewContact',
            'last_name': 'Test',
            'email': 'newcontact@test.com',
            'phone': '5553334444',
            'group_ids': str(seed['group_a1']),
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_create_contact_missing_name(self, owner_a_client, seed):
        resp = owner_a_client.post('/contacts/create', data={
            'first_name': '',
            'last_name': '',
            'email': 'empty@test.com',
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_create_contact_agent_can(self, agent_a_client, seed):
        resp = agent_a_client.post('/contacts/create', data={
            'first_name': 'AgentCreated',
            'last_name': 'Contact',
            'email': 'agentcreated@test.com',
            'group_ids': str(seed['group_a1']),
        }, follow_redirects=True)
        assert resp.status_code == 200


class TestContactEdit:
    """Contact editing."""

    def test_edit_contact(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/contacts/{seed["contact_a"]}/edit', data={
            'first_name': 'JaneEdited',
            'last_name': 'Doe',
            'email': 'jane@test.com',
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_edit_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/contacts/{seed["contact_b"]}/edit', data={
            'first_name': 'Hacked',
        })
        assert resp.status_code == 404

    def test_edit_nonexistent_contact(self, owner_a_client, seed):
        resp = owner_a_client.post('/contacts/99999/edit', data={
            'first_name': 'Ghost',
        })
        assert resp.status_code == 404


class TestContactDelete:
    """Contact deletion."""

    def test_delete_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/contacts/{seed["contact_b"]}/delete')
        assert resp.status_code == 404


class TestContactInteractions:
    """Interaction logging on contacts."""

    def test_log_activity(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/contact/{seed["contact_a"]}/log-activity', data={
            'activity_type': 'call',
            'notes': 'Called about listing',
            'activity_date': '2026-02-27',
        }, follow_redirects=True)
        assert resp.status_code in (200, 302)

    def test_get_interactions(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_a"]}/interactions')
        assert resp.status_code == 200

    def test_log_activity_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/contact/{seed["contact_b"]}/log-activity', data={
            'activity_type': 'call', 'notes': 'Hacked',
            'activity_date': '2026-01-01',
        })
        assert resp.status_code == 404

    def test_get_interactions_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_b"]}/interactions')
        assert resp.status_code == 404


class TestContactTimeline:
    """Contact timeline endpoint."""

    def test_get_timeline(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_a"]}/timeline')
        assert resp.status_code == 200

    def test_timeline_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_b"]}/timeline')
        assert resp.status_code == 404


class TestContactExport:
    """Contact import/export."""

    def test_export_contacts(self, owner_a_client, seed):
        resp = owner_a_client.get('/export-contacts')
        assert resp.status_code == 200
        assert b'first_name' in resp.data.lower() or resp.content_type == 'text/csv'


class TestContactOnboarding:
    """Onboarding dismissal."""

    def test_dismiss_onboarding(self, owner_a_client, seed):
        resp = owner_a_client.post('/contacts/dismiss-onboarding',
                                   follow_redirects=True)
        assert resp.status_code == 200


class TestContactFiles:
    """File upload/download/list endpoints."""

    def test_list_files(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_a"]}/files')
        assert resp.status_code == 200

    def test_list_files_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_b"]}/files')
        assert resp.status_code == 404


class TestContactVoiceMemos:
    """Voice memo endpoints."""

    def test_list_voice_memos(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_a"]}/voice-memos')
        assert resp.status_code == 200

    def test_list_voice_memos_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_b"]}/voice-memos')
        assert resp.status_code == 404


class TestContactEmails:
    """Contact email thread endpoints."""

    def test_get_emails(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_a"]}/emails')
        assert resp.status_code == 200

    def test_get_emails_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/contact/{seed["contact_b"]}/emails')
        assert resp.status_code == 404
