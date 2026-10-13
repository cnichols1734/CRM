"""
Integration tests for transaction routes.

Covers: CRUD, status updates, documents, participants, API endpoints,
history, intake, download, signing, and cross-org isolation.
"""
import pytest
from models import Contact, Task, Transaction, TransactionDocument, TransactionParticipant, db
from routes.transactions.api import format_contact_address
from types import SimpleNamespace


@pytest.fixture
def transaction_contact(app, seed):
    with app.app_context():
        contact = Contact(organization_id=seed['org_a'], user_id=seed['owner_a'],
                          first_name='TransactionAudit', last_name='Client',
                          email='transaction-audit@test.com', street_address='123 Main St',
                          city='Austin', state='TX', zip_code='78701')
        db.session.add(contact)
        db.session.commit()
        contact_id = contact.id
    yield contact_id
    with app.app_context():
        Task.query.filter_by(contact_id=contact_id).delete(synchronize_session=False)
        db.session.delete(db.session.get(Contact, contact_id))
        db.session.commit()


@pytest.fixture
def transaction_record(app, seed, owner_a_client):
    with app.app_context():
        transaction = Transaction(organization_id=seed['org_a'], created_by_id=seed['owner_a'],
                                  transaction_type_id=seed['tx_type_a'],
                                  street_address='Audit Transaction', city='Austin', state='TX', status='active')
        db.session.add(transaction)
        db.session.commit()
        transaction_id = transaction.id
    yield transaction_id
    owner_a_client.post(f'/transactions/{transaction_id}/delete')


class TestTransactionList:
    """Transaction listing."""

    def test_transactions_no_cross_org(self, owner_a_client, seed):
        resp = owner_a_client.get('/transactions/')
        assert resp.status_code == 200
        assert b'100 Main St' in resp.data
        assert b'200 Oak Ave' not in resp.data


class TestTransactionView:
    """View individual transactions."""

    def test_view_own_transaction(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert resp.status_code == 200
        assert b'100 Main St' in resp.data or b'Main' in resp.data

    def test_view_nonexistent(self, owner_a_client, seed):
        resp = owner_a_client.get('/transactions/99999')
        assert resp.status_code == 404


class TestTransactionCreate:
    """Transaction creation."""

    def test_new_form_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/transactions/new')
        assert resp.status_code == 200
        assert b'clientAddressSelect' in resp.data
        assert b'Use a client address' in resp.data
        assert b'streetAddressInput' in resp.data

    def test_create_edit_and_status_changes_persist(self, owner_a_client, seed, app, transaction_contact):
        response = owner_a_client.post('/transactions/', data={
            'transaction_type_id': str(seed['tx_type_a']), 'contact_ids': str(transaction_contact),
            'street_address': '999 Test Blvd', 'city': 'Houston', 'state': 'TX',
        })
        assert response.status_code == 302
        with app.app_context():
            transaction = Transaction.query.filter_by(street_address='999 Test Blvd').one()
            transaction_id = transaction.id
            assert (transaction.organization_id, transaction.created_by_id, transaction.status) == (
                seed['org_a'], seed['owner_a'], 'preparing_to_list')
            seller = transaction.participants.filter_by(role='seller').one()
            assert seller.contact_id == transaction_contact
        try:
            assert owner_a_client.get(f'/transactions/{transaction_id}/edit').status_code == 200
            response = owner_a_client.post(f'/transactions/{transaction_id}', data={
                'street_address': '999 Revised Blvd', 'city': 'Austin', 'state': 'TX',
            })
            assert response.status_code == 302
            response = owner_a_client.post(f'/transactions/{transaction_id}/status', json={'status': 'active'})
            assert response.status_code == 200
            with app.app_context():
                transaction = db.session.get(Transaction, transaction_id)
                assert (transaction.street_address, transaction.city, transaction.status) == ('999 Revised Blvd', 'Austin', 'active')
        finally:
            owner_a_client.post(f'/transactions/{transaction_id}/delete')


class TestTransactionEdit:
    """Transaction editing."""

    def test_edit_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_b"]}/edit')
        assert resp.status_code == 404

    def test_update_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/transactions/{seed["tx_b"]}', data={
            'street_address': 'Hacked',
        })
        assert resp.status_code == 404


class TestTransactionDelete:
    """Transaction deletion."""

    def test_delete_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/transactions/{seed["tx_b"]}/delete')
        assert resp.status_code == 404

    def test_delete_with_seller_commission_terms(self, app, seed, owner_a_client):
        """Commission terms must be purged — not nullified — on SQLite delete."""
        from decimal import Decimal

        from models import SellerCommissionTerms, Transaction, db

        with app.app_context():
            # Dedicated tx — session seed tx_a accumulates VTC rows that block delete.
            tx = Transaction(
                organization_id=seed['org_a'],
                created_by_id=seed['owner_a'],
                transaction_type_id=seed['tx_type_a'],
                street_address='Delete Commission St',
                city='Austin',
                state='TX',
                status='active',
            )
            db.session.add(tx)
            db.session.flush()
            terms = SellerCommissionTerms(
                organization_id=seed['org_a'],
                transaction_id=tx.id,
                created_by_id=seed['owner_a'],
                listing_commission_flat=Decimal('8000'),
                coop_compensation_percent=Decimal('2'),
                source='listing_agreement_extraction',
            )
            db.session.add(terms)
            db.session.commit()
            terms_id = terms.id
            tx_id = tx.id

        resp = owner_a_client.post(
            f'/transactions/{tx_id}/delete',
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert b'Error deleting transaction' not in (resp.data or b'')

        with app.app_context():
            assert Transaction.query.get(tx_id) is None
            assert SellerCommissionTerms.query.get(terms_id) is None

    def test_delete_with_controlling_contract_documents(self, app, seed, owner_a_client):
        """Contract package rows must be deleted — not nullified — on Postgres."""
        from models import (
            SellerAcceptedContract,
            SellerContractDocument,
            Transaction,
            TransactionDocument,
            db,
        )

        with app.app_context():
            tx = Transaction(
                organization_id=seed['org_a'],
                created_by_id=seed['owner_a'],
                transaction_type_id=seed['tx_type_a'],
                street_address='Delete Contract Docs St',
                city='Austin',
                state='TX',
                status='under_contract',
            )
            db.session.add(tx)
            db.session.flush()
            doc = TransactionDocument(
                organization_id=seed['org_a'],
                transaction_id=tx.id,
                template_slug='seller-accepted-contract',
                template_name='Executed Contract',
                status='signed',
                signed_file_path='test/executed.pdf',
            )
            db.session.add(doc)
            db.session.flush()
            contract = SellerAcceptedContract(
                organization_id=seed['org_a'],
                transaction_id=tx.id,
                created_by_id=seed['owner_a'],
                position='primary',
                status='active',
            )
            db.session.add(contract)
            db.session.flush()
            link = SellerContractDocument(
                organization_id=seed['org_a'],
                transaction_id=tx.id,
                accepted_contract_id=contract.id,
                transaction_document_id=doc.id,
                created_by_id=seed['owner_a'],
                document_type='final_acceptance',
                display_name='Executed Contract',
                is_primary_contract_document=True,
            )
            db.session.add(link)
            db.session.commit()
            tx_id = tx.id
            contract_id = contract.id
            link_id = link.id
            doc_id = doc.id

        resp = owner_a_client.post(
            f'/transactions/{tx_id}/delete',
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), resp.get_data(as_text=True)
        assert b'Error deleting transaction' not in (resp.data or b'')

        with app.app_context():
            assert Transaction.query.get(tx_id) is None
            assert SellerAcceptedContract.query.get(contract_id) is None
            assert SellerContractDocument.query.get(link_id) is None
            assert TransactionDocument.query.get(doc_id) is None


class TestTransactionSignersAPI:
    """Signers API."""

    def test_get_signers(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/api/{seed["tx_a"]}/signers')
        assert resp.status_code == 200


class TestTransactionContactSearch:
    """Contact search API within transactions."""

    def test_search_contacts_includes_address(self, owner_a_client, transaction_contact):
        response = owner_a_client.get('/transactions/api/contacts/search?q=TransactionAudit')
        assert response.status_code == 200
        assert len(response.json) == 1
        contact = response.json[0]
        assert contact['id'] == transaction_contact
        assert (contact['street_address'], contact['city'], contact['state'], contact['zip_code']) == (
            '123 Main St', 'Austin', 'TX', '78701')
        assert contact['full_address'] == '123 Main St, Austin, TX 78701'

    def test_format_contact_address(self):
        contact = SimpleNamespace(
            street_address='6004 Lakeside Dr',
            city='Austin',
            state='TX',
            zip_code='78746',
        )
        assert format_contact_address(contact) == '6004 Lakeside Dr, Austin, TX 78746'
        assert format_contact_address(SimpleNamespace(
            street_address='  10 Main  ', city='', state='', zip_code='',
        )) == '10 Main'
        assert format_contact_address(SimpleNamespace(
            street_address='', city='', state='', zip_code='',
        )) == ''


class TestTransactionDocuments:
    """Document management within transactions."""

    def test_document_form_loads(self, owner_a_client, seed):
        resp = owner_a_client.get(
            f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/form'
        )
        assert resp.status_code == 200

    def test_add_document(self, owner_a_client, seed, app, transaction_record):
        response = owner_a_client.post(f'/transactions/{transaction_record}/documents', data={
            'template_slug': 'listing-agreement', 'template_name': 'Listing Agreement',
        })
        assert response.status_code == 200
        with app.app_context():
            document = db.session.get(TransactionDocument, response.json['document']['id'])
            assert (document.organization_id, document.transaction_id, document.template_slug) == (
                seed['org_a'], transaction_record, 'listing-agreement')

    def test_add_document_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/transactions/{seed["tx_b"]}/documents',
            data={'template_slug': 'listing-agreement'},
        )
        assert resp.status_code == 404


class TestTransactionHistory:
    """Transaction history endpoints."""

    def test_history_loads(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_a"]}/history')
        assert resp.status_code == 200

    def test_history_view(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_a"]}/history/view')
        assert resp.status_code == 200


class TestTransactionIntake:
    """Intake questionnaire endpoints."""

    def test_intake_loads(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_a"]}/intake')
        assert resp.status_code in (200, 302)
        if resp.status_code == 200:
            assert b'data-answer="yes"' in resp.data
            assert b'data-answer="no"' in resp.data
            assert b'.toggle-input:checked + .toggle-card[data-answer="yes"]' in resp.data
            assert b'var(--positive-soft)' in resp.data
            assert b'var(--danger-soft)' in resp.data


class TestTransactionParticipants:
    """Participant management endpoints."""

    def test_add_participant(self, owner_a_client, seed, app, transaction_contact, transaction_record):
        response = owner_a_client.post(f'/transactions/{transaction_record}/participants', data={
            'role': 'buyer', 'contact_id': str(transaction_contact),
        })
        assert response.status_code == 200
        with app.app_context():
            participant = db.session.get(TransactionParticipant, response.json['participant']['id'])
            assert (participant.organization_id, participant.transaction_id, participant.contact_id, participant.role) == (
                seed['org_a'], transaction_record, transaction_contact, 'buyer')
