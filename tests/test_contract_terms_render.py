"""The contract terms card must render for every transaction side, not just seller."""

from models import Transaction, TransactionDocument, db


def _set_contract_field_data(seed, field_data):
    doc = db.session.get(TransactionDocument, seed['doc_a'])
    previous = (doc.template_slug, doc.field_data)
    doc.template_slug = 'one-to-four-family-contract'
    doc.field_data = field_data
    db.session.commit()
    return previous


def _restore_document(seed, previous):
    doc = db.session.get(TransactionDocument, seed['doc_a'])
    doc.template_slug, doc.field_data = previous
    db.session.commit()


def _set_transaction_type(seed, type_id):
    tx = db.session.get(Transaction, seed['tx_a'])
    previous = tx.transaction_type_id
    tx.transaction_type_id = type_id
    db.session.commit()
    return previous


def test_seller_detail_renders_contract_terms_from_uploaded_contract(
    app, seed, owner_a_client,
):
    previous = None
    try:
        with app.app_context():
            previous = _set_contract_field_data(seed, {
                'sales_price': 412000,
                'effective_date': '2026-03-04',
                'closing_date': '2026-04-15',
                'title_company': 'Lone Star Title',
            })

        response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'id="contract-terms-card"' in html
        assert 'Contract terms' in html
        assert '$412,000' in html
        assert 'Lone Star Title' in html
        assert 'From uploaded contract' in html
    finally:
        with app.app_context():
            if previous:
                _restore_document(seed, previous)


def test_buyer_detail_renders_contract_terms_and_hides_listing_info(
    app, seed, owner_a_client,
):
    previous_doc = None
    previous_type = None
    try:
        with app.app_context():
            previous_doc = _set_contract_field_data(seed, {
                'purchase_price': 289500,
                'closing_date': '2026-05-01',
            })
            previous_type = _set_transaction_type(seed, seed['tx_type_a2'])

        response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'id="contract-terms-card"' in html
        assert '$289,500' in html
        assert 'id="listing-info-card"' not in html
        assert 'data-seller-listing-tab-content' not in html.split(
            'id="contract-terms-card"', 1,
        )[1].split('</section>', 1)[0]
    finally:
        with app.app_context():
            if previous_type:
                _set_transaction_type(seed, previous_type)
            if previous_doc:
                _restore_document(seed, previous_doc)


def test_seller_detail_shows_empty_state_without_a_listing_agreement(
    app, seed, owner_a_client,
):
    previous = None
    try:
        with app.app_context():
            previous = _set_contract_field_data(seed, {})

        response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'No listing agreement on file' in html
        assert 'Upload listing agreement' in html
        assert 'Not on file' in html
        assert 'Loaded' not in html.split('id="listing-info-card"', 1)[1].split(
            '</section>', 1,
        )[0]
    finally:
        with app.app_context():
            if previous:
                _restore_document(seed, previous)
