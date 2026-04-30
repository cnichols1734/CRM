"""Tests for org-wide Partner Directory behavior."""
from models import db, PartnerContact, PartnerOrganization, TransactionParticipant
from services.partners import sync_partner_contact_fields, sync_partner_organization_fields


def _partner(org_id, user_id, name='Remax Extreme', partner_type='brokerage'):
    partner = PartnerOrganization(
        organization_id=org_id,
        created_by_id=user_id,
        updated_by_id=user_id,
        name=name,
        partner_type=partner_type,
        street_address='123 Closing Way',
        city='Austin',
        state='TX',
        zip_code='78701',
    )
    sync_partner_organization_fields(partner)
    db.session.add(partner)
    db.session.flush()
    return partner


def _partner_contact(org_id, user_id, partner, first='Julie', last='Agent', email='julie@example.com'):
    contact = PartnerContact(
        organization_id=org_id,
        partner_organization_id=partner.id,
        created_by_id=user_id,
        updated_by_id=user_id,
        first_name=first,
        last_name=last,
        email=email,
        phone='5553334444',
    )
    sync_partner_contact_fields(contact)
    db.session.add(contact)
    db.session.flush()
    return contact


class TestPartnerDirectory:
    def test_agent_can_create_partner_company(self, app, agent_a_client, seed):
        resp = agent_a_client.post('/partners/', data={
            'name': 'Austin Title Co',
            'partner_type': 'title_company',
            'phone': '555-111-2222',
            'street_address': '10 Main Street',
            'city': 'Austin',
            'state': 'TX',
            'zip_code': '78701',
        }, follow_redirects=True)

        assert resp.status_code == 200
        with app.app_context():
            partner = PartnerOrganization.query.filter_by(
                organization_id=seed['org_a'],
                normalized_name='austin title co',
            ).first()
            assert partner is not None
            assert partner.created_by_id == seed['agent_a']

    def test_agent_cannot_edit_existing_partner(self, app, agent_a_client, seed):
        with app.app_context():
            partner = _partner(seed['org_a'], seed['owner_a'], name='Admin Managed Title', partner_type='title_company')
            db.session.commit()
            partner_id = partner.id

        resp = agent_a_client.post(f'/partners/{partner_id}/edit', data={
            'name': 'Changed Title',
            'partner_type': 'title_company',
        })

        assert resp.status_code == 403

    def test_exact_company_duplicate_is_blocked(self, app, owner_a_client, seed):
        with app.app_context():
            partner = _partner(seed['org_a'], seed['owner_a'], name='Clean Title')
            db.session.commit()
            partner_id = partner.id

        resp = owner_a_client.post('/partners/', data={
            'name': ' clean   title ',
            'partner_type': 'title_company',
        }, follow_redirects=False)

        assert resp.status_code == 302
        assert f'/partners/{partner_id}' in resp.headers['Location']
        with app.app_context():
            assert PartnerOrganization.query.filter_by(
                organization_id=seed['org_a'],
                normalized_name='clean title',
            ).count() == 1

    def test_child_duplicate_name_is_blocked_but_email_match_warns(self, app, owner_a_client, seed):
        with app.app_context():
            partner = _partner(seed['org_a'], seed['owner_a'], name='Brokerage With People')
            _partner_contact(seed['org_a'], seed['owner_a'], partner, first='Julie', last='Agent', email='julie@example.com')
            db.session.commit()
            partner_id = partner.id

        duplicate_name = owner_a_client.post(f'/partners/{partner_id}/contacts', data={
            'first_name': 'Julie',
            'last_name': 'Agent',
            'email': 'other@example.com',
        })
        assert duplicate_name.status_code == 200

        duplicate_email = owner_a_client.post(f'/partners/{partner_id}/contacts', data={
            'first_name': 'Julia',
            'last_name': 'Closer',
            'email': 'julie@example.com',
        })
        assert duplicate_email.status_code == 200
        assert b'Possible duplicate person found' in duplicate_email.data

        with app.app_context():
            assert PartnerContact.query.filter_by(partner_organization_id=partner_id).count() == 1

    def test_transaction_can_attach_partner_snapshot(self, app, owner_a_client, seed):
        with app.app_context():
            partner = _partner(seed['org_a'], seed['owner_a'], name='Snapshot Title', partner_type='title_company')
            contact = _partner_contact(seed['org_a'], seed['owner_a'], partner, first='Tina', last='Closer', email='tina@example.com')
            db.session.commit()
            partner_id = partner.id
            contact_id = contact.id

        search_resp = owner_a_client.get('/transactions/api/partners/search?q=Snapshot&role=title_company')
        assert search_resp.status_code == 200
        assert any(result['company'] == 'Snapshot Title' for result in search_resp.get_json())

        attach_resp = owner_a_client.post(f'/transactions/{seed["tx_a"]}/participants', data={
            'role': 'title_company',
            'partner_organization_id': str(partner_id),
            'partner_contact_id': str(contact_id),
        })
        assert attach_resp.status_code == 200
        assert attach_resp.get_json()['success'] is True

        with app.app_context():
            participant = TransactionParticipant.query.filter_by(
                transaction_id=seed['tx_a'],
                partner_organization_id=partner_id,
            ).first()
            assert participant is not None
            assert participant.name == 'Tina Closer'
            assert participant.company == 'Snapshot Title'
            assert participant.email == 'tina@example.com'
