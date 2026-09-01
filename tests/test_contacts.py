"""
Integration tests for contact routes.

Covers: CRUD, search/filtering, interactions, import/export,
group assignment, file operations, and cross-org isolation.
"""
import pytest
from conftest import login
from models import Contact, db


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
        resp = owner_a_client.get(f'/contacts?groups={seed["group_a1"]}')
        assert resp.status_code == 200
        assert b'Jane' in resp.data

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

    def test_copy_icons_for_filled_email_and_phone_only(self, owner_a_client, seed, app):
        # Own row. Seed Jane's phone gets reformatted by earlier suite tests.
        with app.app_context():
            contact = Contact(
                organization_id=seed['org_a'],
                user_id=seed['owner_a'],
                created_by_id=seed['owner_a'],
                first_name='Copy',
                last_name='Fields',
                email='copy.fields@test.com',
                phone='5551110000',
            )
            db.session.add(contact)
            db.session.commit()
            contact_id = contact.id

        html = owner_a_client.get(f'/contact/{contact_id}').get_data(as_text=True)
        assert 'data-copy-field-text-value="copy.fields@test.com"' in html
        assert 'data-copy-field-text-value="5551110000"' in html
        assert 'aria-label="Copy email"' in html
        assert 'aria-label="Copy phone"' in html
        assert 'aria-label="Copy address"' not in html
        assert html.count('class="crm-copy"') == 2

    def test_copy_icon_for_address_when_present(self, owner_a_client, seed, app):
        with app.app_context():
            contact = Contact(
                organization_id=seed['org_a'],
                user_id=seed['owner_a'],
                created_by_id=seed['owner_a'],
                first_name='Pat',
                last_name='AddressOnly',
                street_address='9 Oak Ave',
                city='Dallas',
                state='TX',
                zip_code='75201',
            )
            db.session.add(contact)
            db.session.commit()
            contact_id = contact.id

        html = owner_a_client.get(f'/contact/{contact_id}').get_data(as_text=True)
        assert 'data-copy-field-text-value="9 Oak Ave, Dallas, TX 75201"' in html
        assert 'aria-label="Copy address"' in html
        assert 'aria-label="Copy email"' not in html
        assert 'aria-label="Copy phone"' not in html
        assert html.count('class="crm-copy"') == 1


class TestContactAddress:
    def test_full_address_joins_parts(self):
        contact = Contact(
            first_name='A', last_name='B', user_id=1,
            street_address='123 Main St', city='Austin', state='TX', zip_code='78701',
        )
        assert contact.full_address == '123 Main St, Austin, TX 78701'

    def test_full_address_skips_blank_parts(self):
        contact = Contact(first_name='A', last_name='B', user_id=1, city='Austin')
        assert contact.full_address == 'Austin'

    def test_full_address_empty_when_missing(self):
        contact = Contact(first_name='A', last_name='B', user_id=1)
        assert contact.full_address == ''


class TestContactPreview:
    def test_preview_copy_icons_for_email_phone_location(self, owner_a_client, seed, app):
        with app.app_context():
            contact = Contact(
                organization_id=seed['org_a'],
                user_id=seed['owner_a'],
                created_by_id=seed['owner_a'],
                first_name='Rail',
                last_name='Copy',
                email='rail.copy@test.com',
                phone='5554443333',
                city='Baytown',
                state='TX',
                zip_code='77523',
            )
            db.session.add(contact)
            db.session.commit()
            contact_id = contact.id

        html = owner_a_client.get(f'/contact/{contact_id}/preview').get_data(as_text=True)
        assert 'data-copy-field-text-value="rail.copy@test.com"' in html
        assert 'data-copy-field-text-value="5554443333"' in html
        assert 'data-copy-field-text-value="Baytown, TX, 77523"' in html
        assert 'aria-label="Copy email"' in html
        assert 'aria-label="Copy phone"' in html
        assert 'aria-label="Copy location"' in html
        assert html.count('class="crm-copy"') == 3

    def test_preview_copy_icons_omit_empty_fields(self, owner_a_client, seed, app):
        with app.app_context():
            contact = Contact(
                organization_id=seed['org_a'],
                user_id=seed['owner_a'],
                created_by_id=seed['owner_a'],
                first_name='Rail',
                last_name='EmailOnly',
                email='rail.emailonly@test.com',
            )
            db.session.add(contact)
            db.session.commit()
            contact_id = contact.id

        html = owner_a_client.get(f'/contact/{contact_id}/preview').get_data(as_text=True)
        assert 'data-copy-field-text-value="rail.emailonly@test.com"' in html
        assert 'aria-label="Copy email"' in html
        assert 'aria-label="Copy phone"' not in html
        assert 'aria-label="Copy location"' not in html
        assert html.count('class="crm-copy"') == 1


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
            'group_ids': str(seed['group_agent_a1']),
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
