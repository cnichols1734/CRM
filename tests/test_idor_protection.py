"""
IDOR Protection Tests

Verifies that organization_id filtering is enforced on all transaction
and task routes. Creates two separate organizations with users and data,
then verifies that user A cannot access user B's data.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from models import (
    db, User, Organization, Transaction, TransactionType,
    TransactionDocument, Task, TaskType, TaskSubtype, Contact, ContactGroup
)


@pytest.fixture(scope='module')
def seed_data(app):
    """Create two orgs with users, contacts, tasks, and transactions."""
    with app.app_context():
        # Org A
        org_a = Organization(name='Org A', slug='org-a', subscription_tier='pro', status='active', max_users=5, max_contacts=1000)
        db.session.add(org_a)
        db.session.flush()

        user_a = User(
            organization_id=org_a.id, username='user_a', email='a@test.com',
            first_name='Alice', last_name='Anderson', role='admin', org_role='owner'
        )
        user_a.set_password('password123')
        db.session.add(user_a)
        db.session.flush()

        # Org B
        org_b = Organization(name='Org B', slug='org-b', subscription_tier='pro', status='active', max_users=5, max_contacts=1000)
        db.session.add(org_b)
        db.session.flush()

        user_b = User(
            organization_id=org_b.id, username='user_b', email='b@test.com',
            first_name='Bob', last_name='Builder', role='admin', org_role='owner'
        )
        user_b.set_password('password123')
        db.session.add(user_b)
        db.session.flush()

        # Contact groups for both orgs
        group_a = ContactGroup(name='Default', organization_id=org_a.id, category='general', sort_order=0)
        group_b = ContactGroup(name='Default', organization_id=org_b.id, category='general', sort_order=0)
        db.session.add_all([group_a, group_b])
        db.session.flush()

        # Contacts
        contact_a = Contact(
            organization_id=org_a.id, user_id=user_a.id,
            first_name='Alice Contact', last_name='Smith', email='ac@test.com'
        )
        contact_b = Contact(
            organization_id=org_b.id, user_id=user_b.id,
            first_name='Bob Contact', last_name='Jones', email='bc@test.com'
        )
        db.session.add_all([contact_a, contact_b])
        db.session.flush()

        # Transaction types
        tx_type_a = TransactionType(name='seller', display_name='Seller', organization_id=org_a.id)
        tx_type_b = TransactionType(name='seller', display_name='Seller', organization_id=org_b.id)
        db.session.add_all([tx_type_a, tx_type_b])
        db.session.flush()

        # Transactions
        tx_a = Transaction(
            organization_id=org_a.id, created_by_id=user_a.id,
            transaction_type_id=tx_type_a.id, street_address='123 Org A St',
            city='Austin', state='TX', status='active'
        )
        tx_b = Transaction(
            organization_id=org_b.id, created_by_id=user_b.id,
            transaction_type_id=tx_type_b.id, street_address='456 Org B St',
            city='Dallas', state='TX', status='active'
        )
        db.session.add_all([tx_a, tx_b])
        db.session.flush()

        # Transaction documents
        doc_a = TransactionDocument(
            organization_id=org_a.id, transaction_id=tx_a.id,
            template_slug='listing-agreement', template_name='Listing Agreement',
            status='pending'
        )
        doc_b = TransactionDocument(
            organization_id=org_b.id, transaction_id=tx_b.id,
            template_slug='listing-agreement', template_name='Listing Agreement',
            status='pending'
        )
        db.session.add_all([doc_a, doc_b])
        db.session.flush()

        # Task types
        task_type_a = TaskType(name='Call', organization_id=org_a.id, sort_order=0)
        task_type_b = TaskType(name='Call', organization_id=org_b.id, sort_order=0)
        db.session.add_all([task_type_a, task_type_b])
        db.session.flush()

        # Task subtypes
        subtype_a = TaskSubtype(name='Follow Up', task_type_id=task_type_a.id, organization_id=org_a.id, sort_order=0)
        subtype_b = TaskSubtype(name='Follow Up', task_type_id=task_type_b.id, organization_id=org_b.id, sort_order=0)
        db.session.add_all([subtype_a, subtype_b])
        db.session.flush()

        # Tasks
        task_a = Task(
            organization_id=org_a.id, contact_id=contact_a.id,
            assigned_to_id=user_a.id, created_by_id=user_a.id,
            type_id=task_type_a.id, subtype_id=subtype_a.id,
            subject='Task A', priority='medium', status='pending',
            due_date=db.func.now()
        )
        task_b = Task(
            organization_id=org_b.id, contact_id=contact_b.id,
            assigned_to_id=user_b.id, created_by_id=user_b.id,
            type_id=task_type_b.id, subtype_id=subtype_b.id,
            subject='Task B', priority='medium', status='pending',
            due_date=db.func.now()
        )
        db.session.add_all([task_a, task_b])
        db.session.commit()

        return {
            'org_a': org_a.id, 'org_b': org_b.id,
            'user_a': user_a.id, 'user_b': user_b.id,
            'tx_a': tx_a.id, 'tx_b': tx_b.id,
            'doc_a': doc_a.id, 'doc_b': doc_b.id,
            'task_a': task_a.id, 'task_b': task_b.id,
            'subtype_a': subtype_a.id, 'subtype_b': subtype_b.id,
            'task_type_a': task_type_a.id, 'task_type_b': task_type_b.id,
        }


def login(client, username, password='password123'):
    client.get('/logout')
    return client.post('/login', data={
        'username': username,
        'password': password,
    }, follow_redirects=True)


class TestTransactionIDOR:
    """Verify user A cannot access user B's transactions."""

    def test_user_a_can_access_own_transaction(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_a']}")
            assert resp.status_code == 200, f"User A should access own tx, got {resp.status_code}"

    def test_user_a_cannot_access_org_b_transaction(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}")
            assert resp.status_code == 404, f"User A should NOT access Org B tx, got {resp.status_code}"

    def test_user_b_cannot_access_org_a_transaction(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_b')
            resp = client.get(f"/transactions/{seed_data['tx_a']}")
            assert resp.status_code == 404, f"User B should NOT access Org A tx, got {resp.status_code}"


class TestTransactionDocumentIDOR:
    """Verify user A cannot access user B's transaction documents."""

    def test_user_a_can_access_own_doc_form(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_a']}/documents/{seed_data['doc_a']}/form")
            assert resp.status_code == 200, f"User A should access own doc form, got {resp.status_code}"

    def test_user_a_cannot_access_org_b_doc_form(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}/documents/{seed_data['doc_b']}/form")
            assert resp.status_code == 404, f"User A should NOT access Org B doc, got {resp.status_code}"

    def test_cross_org_doc_on_own_tx_url(self, app, seed_data):
        """Try accessing Org B's doc ID using Org A's transaction ID."""
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_a']}/documents/{seed_data['doc_b']}/form")
            assert resp.status_code == 404, f"Cross-org doc access should fail, got {resp.status_code}"


class TestTransactionAPIIDOR:
    """Verify API endpoints enforce org filtering."""

    def test_status_update_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.post(
                f"/transactions/{seed_data['tx_b']}/status",
                json={'status': 'closed'},
                content_type='application/json'
            )
            assert resp.status_code == 404, f"Cross-org status update should fail, got {resp.status_code}"

    def test_lockbox_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.post(
                f"/transactions/{seed_data['tx_b']}/lockbox-combo",
                json={'lockbox_combo': '1234'},
                content_type='application/json'
            )
            assert resp.status_code == 404, f"Cross-org lockbox update should fail, got {resp.status_code}"

    def test_signers_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/api/{seed_data['tx_b']}/signers")
            assert resp.status_code == 404, f"Cross-org signers access should fail, got {resp.status_code}"

    def test_rentcast_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}/rentcast-data")
            assert resp.status_code == 404, f"Cross-org rentcast access should fail, got {resp.status_code}"


class TestTransactionHistoryIDOR:
    """Verify history endpoints enforce org filtering."""

    def test_history_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}/history")
            assert resp.status_code == 404, f"Cross-org history should fail, got {resp.status_code}"

    def test_history_view_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}/history/view")
            assert resp.status_code == 404, f"Cross-org history view should fail, got {resp.status_code}"


class TestTransactionIntakeIDOR:
    """Verify intake endpoints enforce org filtering."""

    def test_intake_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}/intake")
            assert resp.status_code == 404, f"Cross-org intake should fail, got {resp.status_code}"


class TestTransactionDownloadIDOR:
    """Verify download endpoints enforce org filtering."""

    def test_download_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}/documents/{seed_data['doc_b']}/download")
            assert resp.status_code == 404, f"Cross-org download should fail, got {resp.status_code}"

    def test_print_all_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}/documents/print-all-pdf")
            assert resp.status_code == 404, f"Cross-org print-all should fail, got {resp.status_code}"


class TestTransactionSigningIDOR:
    """Verify signing endpoints enforce org filtering."""

    def test_preview_all_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/transactions/{seed_data['tx_b']}/documents/preview-all")
            assert resp.status_code == 404, f"Cross-org preview-all should fail, got {resp.status_code}"


class TestTransactionParticipantsIDOR:
    """Verify participant endpoints enforce org filtering."""

    def test_add_participant_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.post(
                f"/transactions/{seed_data['tx_b']}/participants",
                data={'role': 'buyer', 'contact_id': '999'}
            )
            assert resp.status_code == 404, f"Cross-org participant add should fail, got {resp.status_code}"


class TestTaskIDOR:
    """Verify task routes enforce org filtering."""

    def test_user_a_can_view_own_task(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/tasks/{seed_data['task_a']}")
            assert resp.status_code == 200, f"User A should access own task, got {resp.status_code}"

    def test_user_a_cannot_view_org_b_task(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/tasks/{seed_data['task_b']}")
            assert resp.status_code == 404, f"User A should NOT access Org B task, got {resp.status_code}"

    def test_edit_task_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.post(
                f"/tasks/{seed_data['task_b']}/edit",
                data={'subject': 'hacked', 'status': 'completed', 'priority': 'high',
                      'due_date': '2026-12-31'}
            )
            assert resp.status_code == 404, f"Cross-org task edit should fail, got {resp.status_code}"

    def test_delete_task_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.post(f"/tasks/{seed_data['task_b']}/delete")
            assert resp.status_code == 404, f"Cross-org task delete should fail, got {resp.status_code}"

    def test_quick_update_task_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.post(
                f"/tasks/{seed_data['task_b']}/quick-update",
                data={'status': 'completed'}
            )
            assert resp.status_code == 404, f"Cross-org task quick-update should fail, got {resp.status_code}"


class TestTaskSubtypeIDOR:
    """Verify task subtype endpoint enforces org filtering."""

    def test_subtypes_returns_own_org_only(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/tasks/types/{seed_data['task_type_a']}/subtypes")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 1
            assert data[0]['name'] == 'Follow Up'

    def test_subtypes_blocked_cross_org(self, app, seed_data):
        with app.test_client() as client:
            login(client, 'user_a')
            resp = client.get(f"/tasks/types/{seed_data['task_type_b']}/subtypes")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data) == 0, f"Cross-org subtypes should return empty, got {len(data)} items"
