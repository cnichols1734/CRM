"""Assignment-aware transaction auth: view, deny, and list visibility."""

from models import Transaction, TransactionAssignment, TransactionDocument, User, db
from services.transaction_auth import (
    CAP_EDIT,
    CAP_VIEW,
    can_edit_transaction,
    can_view_transaction,
    get_transaction_for_user,
    transactions_visible_query,
)


def _assign(org_id, tx_id, user_id, role='transaction_coordinator'):
    row = TransactionAssignment(
        organization_id=org_id,
        transaction_id=tx_id,
        user_id=user_id,
        role=role,
    )
    db.session.add(row)
    db.session.flush()
    return row


def test_assignee_can_view_transaction(app, seed):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        agent = User.query.get(seed['agent_a'])
        assert tx.created_by_id != agent.id

        assert can_view_transaction(tx, agent).allowed is False

        _assign(seed['org_a'], tx.id, agent.id, role='lead_agent')
        decision = can_view_transaction(tx, agent)
        assert decision.allowed
        assert decision.reason.startswith('assignment:')

        loaded, auth = get_transaction_for_user(tx.id, user=agent)
        assert loaded is not None
        assert loaded.id == tx.id
        assert auth.allowed


def test_non_assignee_cannot_view_transaction(app, seed):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        agent = User.query.get(seed['agent_a'])
        assert tx.created_by_id != agent.id

        decision = can_view_transaction(tx, agent)
        assert decision.allowed is False
        assert decision.reason == 'not_assigned'

        loaded, auth = get_transaction_for_user(tx.id, user=agent)
        assert loaded is None
        assert auth.allowed is False


def _clear_agent_assignments(org_id, tx_id, user_id):
    TransactionAssignment.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
        user_id=user_id,
    ).delete()
    db.session.commit()


def test_list_includes_assigned_transactions(app, seed, agent_a_client):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        agent = User.query.get(seed['agent_a'])
        assert tx.created_by_id != agent.id
        _clear_agent_assignments(seed['org_a'], tx.id, agent.id)

        before = {
            row.id for row in transactions_visible_query(agent).all()
        }
        assert tx.id not in before

        _assign(seed['org_a'], tx.id, agent.id, role='collaborator')
        db.session.commit()

        after = {
            row.id for row in transactions_visible_query(agent).all()
        }
        assert tx.id in after

    try:
        resp = agent_a_client.get('/transactions/')
        assert resp.status_code == 200
        assert b'100 Main St' in resp.data
    finally:
        with app.app_context():
            _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])


def test_assignee_can_open_transaction_detail(app, seed, agent_a_client):
    with app.app_context():
        _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])
        _assign(
            seed['org_a'], seed['tx_a'], seed['agent_a'],
            role='transaction_coordinator',
        )
        db.session.commit()

    try:
        resp = agent_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert resp.status_code == 200
    finally:
        with app.app_context():
            _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])


def test_non_assignee_detail_forbidden(app, seed, agent_a_client):
    with app.app_context():
        _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])

    resp = agent_a_client.get(f'/transactions/{seed["tx_a"]}')
    assert resp.status_code == 403


def test_assignee_can_access_intake_edit_gate(app, seed, agent_a_client):
    """TC assignee passes CAP_EDIT used by intake / upload-fulfill routes."""
    with app.app_context():
        _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])
        agent = User.query.get(seed['agent_a'])
        tx = Transaction.query.get(seed['tx_a'])
        assert can_edit_transaction(tx, agent).allowed is False

        _assign(
            seed['org_a'], seed['tx_a'], seed['agent_a'],
            role='transaction_coordinator',
        )
        db.session.commit()

        loaded, decision = get_transaction_for_user(
            seed['tx_a'], user=agent, capability=CAP_EDIT,
        )
        assert loaded is not None
        assert decision.allowed

    try:
        resp = agent_a_client.get(f'/transactions/{seed["tx_a"]}/intake')
        # 200 if schema exists, or redirect when none — not 403
        assert resp.status_code in (200, 302)
        assert resp.status_code != 403
    finally:
        with app.app_context():
            _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])


def test_assignee_can_access_download_view_gate(app, seed, agent_a_client):
    """Collaborator assignee passes CAP_VIEW used by download routes."""
    with app.app_context():
        _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])
        _assign(
            seed['org_a'], seed['tx_a'], seed['agent_a'],
            role='collaborator',
        )
        db.session.commit()

        agent = User.query.get(seed['agent_a'])
        loaded, decision = get_transaction_for_user(
            seed['tx_a'], user=agent, capability=CAP_VIEW,
        )
        assert loaded is not None
        assert decision.allowed
        # Collaborator cannot edit
        assert can_edit_transaction(loaded, agent).allowed is False

        doc = TransactionDocument.query.get(seed['doc_a'])
        doc.status = 'signed'
        doc.signed_file_path = 'org/tx/doc-signed.pdf'
        db.session.commit()

    try:
        # Auth gate must allow through; storage/mock may still 500 — not 403
        resp = agent_a_client.get(
            f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/download'
        )
        assert resp.status_code != 403
        assert resp.status_code != 404
    finally:
        with app.app_context():
            doc = TransactionDocument.query.get(seed['doc_a'])
            doc.status = 'pending'
            doc.signed_file_path = None
            db.session.commit()
            _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])


def test_non_assignee_blocked_from_intake_and_download(app, seed, agent_a_client):
    with app.app_context():
        _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])

    intake = agent_a_client.get(f'/transactions/{seed["tx_a"]}/intake')
    assert intake.status_code == 403

    download = agent_a_client.get(
        f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/download'
    )
    assert download.status_code == 403
