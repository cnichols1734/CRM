"""Stage-aware transaction detail: hide empty irrelevant surfaces, keep data."""

from datetime import date, datetime, timedelta

from models import (
    SellerAcceptedContract,
    SellerOffer,
    Transaction,
    TransactionDocument,
    db,
)


def _make_seller_tx(seed, *, street='900 Stage Gate St', with_listing=True):
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=seed['tx_type_a'],
        street_address=street,
        city='Austin',
        state='TX',
        status='active',
    )
    db.session.add(tx)
    db.session.flush()
    if with_listing:
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
        ))
    db.session.commit()
    return tx.id


def _cleanup_tx(tx_id):
    SellerOffer.query.filter_by(transaction_id=tx_id).delete(synchronize_session=False)
    SellerAcceptedContract.query.filter_by(transaction_id=tx_id).delete(
        synchronize_session=False,
    )
    TransactionDocument.query.filter_by(transaction_id=tx_id).delete(
        synchronize_session=False,
    )
    Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
    db.session.commit()


def test_fresh_seller_listing_keeps_offers_and_contract_discoverable(app, seed, owner_a_client):
    """Listed seller with no offers/contract still sees lifecycle tabs + empty states."""
    tx_id = None
    with app.app_context():
        tx_id = _make_seller_tx(seed, street='901 Fresh Listing Ave')

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'Stage ' in html
        assert 'id="seller-workspace"' in html
        assert 'id="seller-tab-listing"' in html
        assert 'id="seller-tab-offers"' in html
        assert 'id="seller-tab-contract"' in html
        assert 'id="seller-panel-contract"' in html
        assert 'Contract workspace' in html or 'No controlling contract yet' in html
        assert 'href="#seller-workspace"' in html
        assert 'href="#listing-documents"' in html or 'href="#seller-workspace"' in html
    finally:
        with app.app_context():
            _cleanup_tx(tx_id)


def test_detail_header_price_uses_listing_agreement_list_price(app, seed, owner_a_client):
    """Listing-stage header shows list price even when sales_price is unset."""
    tx_id = None
    with app.app_context():
        tx_id = _make_seller_tx(seed, street='903 List Amount Lane')
        doc = TransactionDocument.query.filter_by(
            transaction_id=tx_id,
            template_slug='listing-agreement',
        ).one()
        doc.field_data = {
            'list_price': '485000',
            'listing_start_date': '2026-01-18',
            'listing_end_date': '2026-07-31',
        }
        doc.status = 'signed'
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        marker = 'class="mb-6 border-b border-slate-200 pb-5"'
        assert marker in html
        header = html.split(marker, 1)[1].split('</header>', 1)[0]
        assert '$485,000' in header
        price_label = '>Price</dt>'
        assert price_label in header
        price_cell = header.split(price_label, 1)[1].split('>File</dt>', 1)[0]
        assert '$485,000' in price_cell
        assert 'Not set' not in price_cell
    finally:
        with app.app_context():
            _cleanup_tx(tx_id)


def test_seller_with_contract_shows_contract_tab(app, seed, owner_a_client):
    """Active primary contract always surfaces the Contract tab."""
    tx_id = None
    with app.app_context():
        tx_id = _make_seller_tx(seed, street='902 Contract Surface Ave')
        db.session.add(SellerAcceptedContract(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            created_by_id=seed['owner_a'],
            position='primary',
            status='active',
            accepted_price=500000,
            closing_date=date.today() + timedelta(days=30),
        ))
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="seller-tab-contract"' in html
        assert 'id="seller-panel-contract"' in html
        assert 'Primary contract active' in html
        assert 'data-default-seller-tab="contract"' in html
        assert 'No primary contract yet' not in html
    finally:
        with app.app_context():
            _cleanup_tx(tx_id)


def test_declined_offers_keep_offers_panel_visible(app, seed, owner_a_client):
    """Declined offer history beats stage-hidden offers surface."""
    tx_id = None
    with app.app_context():
        tx_id = _make_seller_tx(seed, street='903 Declined Offers Ave')
        db.session.add(SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=tx_id,
            created_by_id=seed['owner_a'],
            status='declined',
            received_at=datetime.utcnow() - timedelta(days=2),
            offer_price=480000,
            buyer_names='Declined Buyer',
            creation_source='manual',
        ))
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="seller-tab-offers"' in html
        assert 'id="seller-panel-offers"' in html
        assert 'Declined Buyer' in html
    finally:
        with app.app_context():
            _cleanup_tx(tx_id)


def test_documents_nav_link_targets_rendered_section(app, seed, owner_a_client):
    response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Packages make seller workspace the canonical documents target.
    assert 'href="#seller-workspace"' in html or 'href="#listing-documents"' in html
    assert 'id="seller-workspace"' in html or 'id="listing-documents"' in html


def test_buyer_detail_unaffected_by_seller_tab_gating(app, seed, owner_a_client):
    buyer_tx_id = None
    with app.app_context():
        buyer_tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a2'],
            street_address='55 Buyer Lane',
            city='Austin',
            state='TX',
            status='showing',
        )
        db.session.add(buyer_tx)
        db.session.commit()
        buyer_tx_id = buyer_tx.id

    try:
        response = owner_a_client.get(f'/transactions/{buyer_tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="seller-workspace"' not in html
        assert 'id="seller-tab-contract"' not in html
        assert 'Stage ' in html
        assert 'href="#buyer-document-packages"' in html or 'href="#listing-documents"' in html
        assert 'id="buyer-document-packages"' in html or 'id="listing-documents"' in html
        assert 'href="#transaction-offers"' in html
        assert 'href="#buyer-contract-packages"' in html
    finally:
        with app.app_context():
            Transaction.query.filter_by(id=buyer_tx_id).delete()
            db.session.commit()
