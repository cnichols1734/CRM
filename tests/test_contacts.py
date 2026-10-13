"""
Integration tests for contact routes.

Covers: CRUD, search/filtering, interactions, import/export,
group assignment, file operations, and cross-org isolation.
"""
import pytest
from models import Contact, ContactGroup, Interaction, db


@pytest.fixture(autouse=True)
def cleanup_contacts(app, seed):
    with app.app_context():
        before = {row.id for row in Contact.query.with_entities(Contact.id)}
    yield
    with app.app_context():
        db.session.rollback()
        for contact in Contact.query.filter(~Contact.id.in_(before)).all():
            Interaction.query.filter_by(contact_id=contact.id).delete(synchronize_session=False)
            db.session.delete(contact)
        db.session.commit()


@pytest.fixture
def contact_records(app, seed):
    with app.app_context():
        contacts = []
        for first, group in [('AuditSelected', 'group_a1'), ('AuditExcluded', 'group_a2')]:
            contact = Contact(organization_id=seed['org_a'], user_id=seed['owner_a'],
                              created_by_id=seed['owner_a'], first_name=first,
                              last_name='Contact', email=f'{first}@test.com')
            contact.groups = [db.session.get(ContactGroup, seed[group])]
            contacts.append(contact)
        db.session.add_all(contacts)
        db.session.commit()
        return [contact.id for contact in contacts]


class TestContactList:
    @pytest.mark.parametrize('filter_kind', ['search', 'group', 'no_match'])
    def test_filter_includes_matches_and_excludes_other_contacts(self, owner_a_client, seed, contact_records, filter_kind):
        query = {'search': '?q=AuditSelected', 'group': f'?groups={seed["group_a1"]}',
                 'no_match': '?q=NoMatchingAuditContact'}[filter_kind]
        response = owner_a_client.get('/contacts' + query)
        assert response.status_code == 200
        assert (b'AuditSelected' in response.data) is (filter_kind != 'no_match')
        assert b'AuditExcluded' not in response.data
        assert b'bc@test.com' not in response.data


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

    @pytest.mark.parametrize('user, group', [('owner_a', 'group_a1'), ('agent_a', 'group_agent_a1')])
    def test_create_contact_persists_details_and_ownership(self, request, seed, app, user, group):
        client = request.getfixturevalue(f'{user}_client')
        response = client.post('/contacts/create', data={
            'first_name': 'NewContact', 'last_name': 'Test',
            'email': 'newcontact@test.com', 'phone': '5553334444',
            'potential_commission': '5000', 'group_ids': str(seed[group]),
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 200
        with app.app_context():
            contact = db.session.get(Contact, response.json['contact']['id'])
            assert (contact.first_name, contact.last_name, contact.email) == ('NewContact', 'Test', 'newcontact@test.com')
            assert contact.phone == '(555) 333-4444'
            assert (contact.organization_id, contact.user_id, contact.created_by_id) == (seed['org_a'], seed[user], seed[user])
            assert [group.id for group in contact.groups] == [seed[group]]

    def test_create_contact_missing_name_does_not_persist(self, owner_a_client, app):
        response = owner_a_client.post('/contacts/create', data={
            'first_name': '', 'last_name': '', 'email': 'empty@test.com',
            'potential_commission': '5000',
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert response.status_code == 400
        assert {'first_name', 'last_name'} <= response.json['errors'].keys()
        with app.app_context():
            assert Contact.query.filter_by(email='empty@test.com').first() is None


class TestContactEdit:
    """Contact editing."""

    def test_edit_contact(self, owner_a_client, seed, app, contact_records):
        contact_id = contact_records[0]
        response = owner_a_client.post(f'/contacts/{contact_id}/edit', data={
            'first_name': 'Revised', 'last_name': 'Contact', 'email': 'revised@test.com',
            'group_ids': str(seed['group_a2']),
        })
        assert response.status_code == 200
        with app.app_context():
            contact = db.session.get(Contact, contact_id)
            assert (contact.first_name, contact.email) == ('Revised', 'revised@test.com')
            assert [group.id for group in contact.groups] == [seed['group_a2']]

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

    def test_logged_activity_is_readable_and_updates_last_contact(self, owner_a_client, app, contact_records):
        from datetime import date
        contact_id = contact_records[0]
        response = owner_a_client.post(f'/contact/{contact_id}/log-activity', data={
            'activity_type': 'call', 'notes': 'Called about listing',
            'activity_date': '2026-02-27',
        })
        assert response.status_code == 200
        interaction_id = response.json['interaction']['id']
        readback = owner_a_client.get(f'/contact/{contact_id}/interactions')
        assert readback.status_code == 200
        assert [(row['id'], row['type'], row['notes'], row['date'])
                for row in readback.json['interactions']] == [
                    (interaction_id, 'call', 'Called about listing', 'Feb 27, 2026')]
        with app.app_context():
            contact = db.session.get(Contact, contact_id)
            assert contact.last_phone_call_date == date(2026, 2, 27)
            assert contact.last_contact_date == date(2026, 2, 27)

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
