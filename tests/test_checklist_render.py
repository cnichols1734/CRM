"""Render tests for the transaction checklist on detail pages."""

from __future__ import annotations

from datetime import datetime, timedelta

from models import (
    SellerListingProfile,
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


def _make_tx(seed, *, side='seller', address='900 Checklist Render Ln', status='under_contract'):
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
        status=status,
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


def test_seller_preparing_to_list_renders_grouped_checklist(app, seed, owner_a_client):
    tx_id = None
    with app.app_context():
        tx = _make_tx(
            seed, side='seller', address='901 Seller Checklist Ln',
            status='preparing_to_list',
        )
        tx_id = tx.id
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'id="transaction-checklist"' in html
        assert 'Checklist' in html
        assert 'Preparing to List' in html
        assert 'Listing Documents' in html
        assert 'Property &amp; Marketing Prep' in html or 'Property & Marketing Prep' in html
        assert 'MLS Setup' in html
        assert 'Sign Listing Agreement' in html
        assert 'Seller&#39;s Disclosure' in html or "Seller's Disclosure" in html
        assert 'Upload Remaining Listing Documents' in html
        assert 'still needed' in html
        assert 'Confirm Property Details with Seller' in html
        assert 'Add Property Description' in html
        assert 'data-listing-description-toggle' in html
        assert 'Draft listing description' in html or 'Add listing description' in html
        assert 'Write' not in html.split('id="transaction-checklist"', 1)[1].split('</section>', 1)[0]
        assert 'MLS public remarks' in html
        assert 'Draft uses the listing agreement' in html
        assert 'Draft with AI' in html or 'AI drafting is not set up' in html
        assert 'data-checklist-date-button' in html
        assert 'data-checklist-count' in html
        assert 'Add date' in html
        assert 'data-checklist-add' in html
        assert 'Add an item' in html
        assert 'Next step: Sign Listing Agreement' in html
        assert 'href="#transaction-checklist"' in html
        assert 'id="transaction-required-documents"' not in html
        assert 'click any date' not in html.lower()
        assert 'Overdue' not in html.split('id="transaction-checklist"', 1)[1].split('</section>', 1)[0]
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)


def test_listing_agreement_on_file_auto_checks_sign_row(app, seed, owner_a_client):
    tx_id = None
    with app.app_context():
        tx = _make_tx(
            seed, side='seller', address='904 Listing File Ln',
            status='preparing_to_list',
        )
        tx_id = tx.id
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            is_placeholder=False,
            document_source='completed',
            signed_file_path='/tmp/listing-agreement.pdf',
        ))
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        checklist = html.split('id="transaction-checklist"', 1)[1].split('</section>', 1)[0]
        assert 'Sign Listing Agreement' in checklist
        assert 'On file' in checklist
        assert 'data-item-key="listing_agreement"' in checklist
        assert 'data-auto="true"' in checklist
        assert 'Review with PDF' in html
        assert 'id="listing-info-compare"' in html
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)


def test_listing_description_row_marks_ai_draft(app, seed, owner_a_client):
    tx_id = None
    with app.app_context():
        tx = _make_tx(
            seed, side='seller', address='906 Remarks Draft Ln',
            status='preparing_to_list',
        )
        tx_id = tx.id
        db.session.add(SellerListingProfile(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            created_by_id=seed['owner_a'],
            extra_data={
                'listing_description': (
                    'Built in 2018, this four-bedroom home in Fosters Ridge '
                    'offers 2,638 square feet on a large lot.'
                ),
                'listing_description_source': 'ai',
            },
        ))
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        checklist = html.split('id="transaction-checklist"', 1)[1].split('</section>', 1)[0]
        assert 'Add Property Description' in checklist
        assert 'AI draft' in checklist
        assert 'data-listing-description-source="ai"' in checklist
        assert 'Built in 2018' in checklist
        assert checklist.count('Add date') >= 1
    finally:
        with app.app_context():
            SellerListingProfile.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            _cleanup(seed['org_a'], tx_id)


def test_required_documents_block_is_gone(app, seed, owner_a_client):
    tx_id = None
    with app.app_context():
        tx = _make_tx(
            seed, side='seller', address='903 Split Checklist Ln',
            status='preparing_to_list',
        )
        tx_id = tx.id
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            template_slug='iabs',
            template_name='Information About Brokerage Services',
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
        assert 'id="transaction-required-documents"' not in html
        assert '1 of 2 uploaded' not in html
        assert 'data-document-state="missing"' not in html
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)


def test_seller_under_contract_shows_dated_deadlines_not_listing_prep(app, seed, owner_a_client):
    tx_id = None
    with app.app_context():
        tx = _make_tx(seed, side='seller', address='905 Under Contract Ln')
        tx_id = tx.id
        db.session.add(TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            package_key='seller_ctc',
            phase_key='due_diligence',
            requirement_key='survey',
            title='Survey Completed',
            work_status='pending',
            deadline_rule_version='v1',
            due_at=datetime.utcnow() + timedelta(days=5),
        ))
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="transaction-checklist"' in html
        checklist = html.split('id="transaction-checklist"', 1)[1].split('</section>', 1)[0]
        assert 'Deadlines' in checklist
        assert 'Survey Completed' in checklist
        assert 'Sign Listing Agreement' not in checklist
        assert 'Add Property Description' not in checklist
        assert 'id="transaction-required-documents"' not in html
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)


def test_buyer_detail_renders_quiet_deadline_list(app, seed, owner_a_client):
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
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'id="transaction-checklist"' in html
        assert 'Inspection Completed' in html
        assert 'id="transaction-required-documents"' not in html
        checklist_block = html.split('id="transaction-checklist"', 1)[1]
        checklist_block = checklist_block.split('</section>', 1)[0]
        assert 'Deadlines' in checklist_block
        assert 'data-checklist-toggle' not in checklist_block
        assert 'data-checklist-key=' not in checklist_block
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)
