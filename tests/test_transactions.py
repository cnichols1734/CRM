"""
Integration tests for transaction routes.

Covers: CRUD, status updates, documents, participants, API endpoints,
history, intake, download, signing, and cross-org isolation.
"""
import pytest
from conftest import login
from models import Contact, db
from routes.transactions.api import format_contact_address
from types import SimpleNamespace


class TestTransactionList:
    """Transaction listing."""

    def test_transactions_page_loads(self, owner_a_client, seed):
        resp = owner_a_client.get('/transactions/')
        assert resp.status_code == 200

    def test_transactions_no_cross_org(self, owner_a_client, seed):
        resp = owner_a_client.get('/transactions/')
        assert b'200 Oak Ave' not in resp.data

    def test_free_tier_no_transactions(self, owner_b_client, seed):
        resp = owner_b_client.get('/transactions/', follow_redirects=True)
        assert resp.status_code == 200


class TestTransactionView:
    """View individual transactions."""

    def test_view_own_transaction(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert resp.status_code == 200
        assert b'100 Main St' in resp.data or b'Main' in resp.data

    def test_view_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_b"]}')
        assert resp.status_code == 404

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

    def test_create_transaction(self, owner_a_client, seed):
        resp = owner_a_client.post('/transactions/', data={
            'transaction_type_id': str(seed['tx_type_a']),
            'street_address': '999 Test Blvd',
            'city': 'Houston',
            'state': 'TX',
        }, follow_redirects=True)
        assert resp.status_code == 200


class TestTransactionEdit:
    """Transaction editing."""

    def test_edit_form_loads(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_a"]}/edit')
        assert resp.status_code == 200

    def test_update_transaction(self, owner_a_client, seed):
        resp = owner_a_client.post(f'/transactions/{seed["tx_a"]}', data={
            'transaction_type_id': str(seed['tx_type_a']),
            'street_address': '100 Main St Updated',
            'city': 'Austin',
            'state': 'TX',
        }, follow_redirects=True)
        assert resp.status_code == 200

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


class TestTransactionStatusAPI:
    """Status update API."""

    def test_update_status(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/status',
            json={'status': 'active'},
            content_type='application/json',
        )
        assert resp.status_code == 200

    def test_update_status_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/transactions/{seed["tx_b"]}/status',
            json={'status': 'closed'},
            content_type='application/json',
        )
        assert resp.status_code == 404


class TestTransactionLockboxAPI:
    """Lockbox combo API."""

    def test_lockbox_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/transactions/{seed["tx_b"]}/lockbox-combo',
            json={'lockbox_combo': '1234'},
            content_type='application/json',
        )
        assert resp.status_code == 404


class TestTransactionSignersAPI:
    """Signers API."""

    def test_get_signers(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/api/{seed["tx_a"]}/signers')
        assert resp.status_code == 200

    def test_signers_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/api/{seed["tx_b"]}/signers')
        assert resp.status_code == 404


class TestTransactionContactSearch:
    """Contact search API within transactions."""

    def test_search_contacts(self, owner_a_client, seed):
        resp = owner_a_client.get('/transactions/api/contacts/search?q=Jane')
        assert resp.status_code == 200

    def test_search_contacts_includes_address(self, app, owner_a_client, seed):
        with app.app_context():
            contact = Contact.query.get(seed['contact_a'])
            contact.street_address = '123 Main St'
            contact.city = 'Austin'
            contact.state = 'TX'
            contact.zip_code = '78701'
            db.session.commit()

        resp = owner_a_client.get('/transactions/api/contacts/search?q=Jane')
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload
        match = next(item for item in payload if item['id'] == seed['contact_a'])
        assert match['street_address'] == '123 Main St'
        assert match['city'] == 'Austin'
        assert match['state'] == 'TX'
        assert match['zip_code'] == '78701'
        assert match['full_address'] == '123 Main St, Austin, TX 78701'

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

    def test_document_form_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(
            f'/transactions/{seed["tx_b"]}/documents/{seed["doc_a"]}/form'
        )
        assert resp.status_code == 404

    def test_add_document(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/documents',
            data={'template_slug': 'listing-agreement'},
            follow_redirects=True,
        )
        assert resp.status_code in (200, 302, 400)

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

    def test_history_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_b"]}/history')
        assert resp.status_code == 404

    def test_history_view(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_a"]}/history/view')
        assert resp.status_code == 200

    def test_history_view_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_b"]}/history/view')
        assert resp.status_code == 404


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

    def test_intake_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_b"]}/intake')
        assert resp.status_code == 404


class TestTransactionDownload:
    """Document download endpoints."""

    def test_download_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(
            f'/transactions/{seed["tx_b"]}/documents/{seed["doc_a"]}/download'
        )
        assert resp.status_code == 404

    def test_print_all_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(
            f'/transactions/{seed["tx_b"]}/documents/print-all-pdf'
        )
        assert resp.status_code == 404


class TestTransactionParticipants:
    """Participant management endpoints."""

    def test_add_participant(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/participants',
            data={
                'role': 'buyer',
                'contact_id': str(seed['contact_a']),
            },
            follow_redirects=True,
        )
        assert resp.status_code in (200, 302, 400)

    def test_add_participant_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.post(
            f'/transactions/{seed["tx_b"]}/participants',
            data={'role': 'buyer', 'contact_id': '999'},
        )
        assert resp.status_code == 404


class TestTransactionSigning:
    """Signing/preview endpoints."""

    def test_preview_all_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(
            f'/transactions/{seed["tx_b"]}/documents/preview-all'
        )
        assert resp.status_code == 404


class TestTransactionRentcast:
    """RentCast API endpoints."""

    def test_rentcast_cross_org_blocked(self, owner_a_client, seed):
        resp = owner_a_client.get(f'/transactions/{seed["tx_b"]}/rentcast-data')
        assert resp.status_code == 404
