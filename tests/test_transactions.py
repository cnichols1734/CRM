"""
Integration tests for transaction routes.

Covers: CRUD, status updates, documents, participants, API endpoints,
history, intake, download, signing, and cross-org isolation.
"""
import pytest
from conftest import login


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
