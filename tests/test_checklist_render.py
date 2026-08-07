"""Render tests for the merged transaction checklist on detail pages."""

from __future__ import annotations

from datetime import datetime, timedelta

from models import (
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    TransactionType,
    db,
)


def _buyer_type(org_id):
    tx_type = TransactionType.query.filter_by(
        organization_id=org_id, name='buyer',
    ).first()
    if tx_type is None:
        tx_type = TransactionType(
            organization_id=org_id, name='buyer', display_name='Buyer',
        )
        db.session.add(tx_type)
        db.session.flush()
    return tx_type


def _make_tx(seed, *, side='seller', address='900 Checklist Render Ln'):
    if side == 'seller':
        type_id = seed['tx_type_a']
    else:
        type_id = _buyer_type(seed['org_a']).id
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=type_id,
        street_address=address,
        city='Austin',
        state='TX',
        status='under_contract',
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _cleanup(org_id, tx_id):
    TransactionRequirement.query.filter_by(
        organization_id=org_id, transaction_id=tx_id,
    ).delete(synchronize_session=False)
    TransactionDocument.query.filter_by(
        organization_id=org_id, transaction_id=tx_id,
    ).delete(synchronize_session=False)
    Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
    db.session.commit()


def test_seller_detail_renders_merged_checklist(app, seed, owner_a_client):
    tx_id = None
    with app.app_context():
        tx = _make_tx(seed, side='seller', address='901 Seller Checklist Ln')
        tx_id = tx.id
        now = datetime.utcnow()
        db.session.add(TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            package_key='seller_ctc',
            phase_key='due_diligence',
            requirement_key='survey',
            title='Survey Completed',
            work_status='pending',
            deadline_rule_version='v1',
            due_at=now + timedelta(days=5),
            responsible_party_label='Seller',
        ))
        db.session.add(TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            package_key='seller_ctc',
            phase_key='financing',
            requirement_key='appraisal',
            title='Appraisal Completed',
            work_status='pending',
            deadline_rule_version='v1',
            due_at=now + timedelta(days=12),
        ))
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            template_slug='survey',
            template_name='Survey',
            status='pending',
            is_placeholder=True,
            document_source='placeholder',
            included_reason='Required by: Survey Completed',
        ))
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            template_slug='standalone-hoa',
            template_name='HOA Packet',
            status='pending',
            is_placeholder=True,
            document_source='placeholder',
        ))
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'id="transaction-checklist"' in html
        assert 'Survey Completed' in html
        assert 'Appraisal Completed' in html
        assert 'HOA Packet' in html
        assert 'Add upload' in html or 'Upload' in html
        # Seller/buyer files use the package workspace as the documents surface;
        # the legacy table's "On checklist" / included_reason hints no longer render there.

        checklist_block = html.split('id="transaction-checklist"', 1)[1]
        checklist_block = checklist_block.split('id="transaction-assignments"', 1)[0]
        # Folded survey must not also appear as a standalone checklist document row.
        assert checklist_block.count('data-checklist-key="survey"') == 1
        assert 'data-checklist-kind="document" data-checklist-key="survey"' not in checklist_block
        # Appraisal has no expected document — no create/upload control on that row.
        appraisal_row = None
        for chunk in checklist_block.split('data-checklist-key="'):
            if chunk.startswith('appraisal"'):
                appraisal_row = chunk.split('</li>', 1)[0]
                break
        assert appraisal_row is not None
        assert 'Add upload' not in appraisal_row
        assert 'showFulfillPlaceholderModal' not in appraisal_row
        assert '/pdf' not in appraisal_row
        assert 'viewStoredDocument' not in appraisal_row
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)


def test_deadlines_and_required_documents_are_separate_lists(app, seed, owner_a_client):
    """Deadlines answer "when"; the document tracker answers "what's missing"."""
    tx_id = None
    with app.app_context():
        tx = _make_tx(seed, side='seller', address='903 Split Checklist Ln')
        tx_id = tx.id
        db.session.add(TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            package_key='listing',
            phase_key='listing_prep',
            requirement_key='photos_ready',
            title='Photos Ready',
            work_status='pending',
            deadline_rule_version='v1',
            due_at=datetime.utcnow() + timedelta(days=4),
        ))
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            template_slug='iabs',
            template_name='Information About Brokerage Services',
            status='pending',
            is_placeholder=True,
            document_source='placeholder',
        ))
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            template_slug='wire-fraud-warning',
            template_name='Wire Fraud Warning',
            status='signed',
            is_placeholder=False,
            document_source='completed',
            signed_file_path='/tmp/wire.pdf',
        ))
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'id="transaction-checklist"' in html
        assert 'id="transaction-required-documents"' in html

        deadlines_block, documents_block = html.split(
            'id="transaction-required-documents"', 1,
        )
        deadlines_block = deadlines_block.split('id="transaction-checklist"', 1)[1]

        # Deadlines list carries the dated work item, not the document placeholders.
        assert 'Photos Ready' in deadlines_block
        assert 'Information About Brokerage Services' not in deadlines_block

        # Document tracker carries placeholders and uploaded docs with a count.
        assert 'Information About Brokerage Services' in documents_block
        assert 'Wire Fraud Warning' in documents_block
        assert '1 of 2 uploaded' in documents_block
        assert 'data-document-state="missing"' in documents_block
        assert 'data-document-state="uploaded"' in documents_block
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)


def test_buyer_detail_renders_merged_checklist(app, seed, owner_a_client):
    tx_id = None
    with app.app_context():
        tx = _make_tx(seed, side='buyer', address='902 Buyer Checklist Ln')
        tx_id = tx.id
        now = datetime.utcnow()
        db.session.add(TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            package_key='buyer_ctc',
            phase_key='due_diligence',
            requirement_key='inspection',
            title='Inspection Completed',
            work_status='pending',
            deadline_rule_version='v1',
            due_at=now + timedelta(days=3),
        ))
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            template_slug='inspection-report',
            template_name='Inspection Report',
            status='signed',
            is_placeholder=False,
            document_source='completed',
            source_file_path='/tmp/inspection.pdf',
            included_reason='Required by: Inspection Completed',
        ))
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'id="transaction-checklist"' in html
        assert 'Inspection Completed' in html
        assert f'/transactions/{tx_id}/documents/' in html
        assert '/pdf' in html
        assert 'viewStoredDocument' not in html

        checklist_block = html.split('id="transaction-checklist"', 1)[1]
        checklist_block = checklist_block.split('</section>', 1)[0]
        assert checklist_block.count('data-checklist-key="inspection"') == 1
        assert checklist_block.count('Inspection Report') >= 1
        # No duplicate standalone document row for the folded inspection report.
        assert 'data-checklist-kind="document"' not in checklist_block or (
            'data-checklist-key="inspection-report"' not in checklist_block
        )
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)
