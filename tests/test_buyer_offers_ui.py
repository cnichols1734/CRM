"""Buyer/seller offers panel UI on the transaction detail page."""

from __future__ import annotations

from decimal import Decimal

from models import (
    SellerOffer,
    SellerOfferActivity,
    SellerOfferVersion,
    Transaction,
    TransactionType,
    db,
)


def _cleanup_offers(org_id, tx_id):
    offer_ids = [
        row.id
        for row in SellerOffer.query.filter_by(
            organization_id=org_id,
            transaction_id=tx_id,
        ).all()
    ]
    if offer_ids:
        SellerOfferActivity.query.filter(
            SellerOfferActivity.offer_id.in_(offer_ids),
        ).delete(synchronize_session=False)
        SellerOfferVersion.query.filter(
            SellerOfferVersion.offer_id.in_(offer_ids),
        ).delete(synchronize_session=False)
        SellerOffer.query.filter(SellerOffer.id.in_(offer_ids)).delete(
            synchronize_session=False,
        )
    db.session.commit()


def _make_transaction(org_id, user_id, type_name, address):
    tx_type = TransactionType.query.filter_by(
        organization_id=org_id,
        name=type_name,
    ).first()
    if tx_type is None:
        tx_type = TransactionType(
            organization_id=org_id,
            name=type_name,
            display_name=type_name.title(),
        )
        db.session.add(tx_type)
        db.session.flush()
    tx = Transaction(
        organization_id=org_id,
        created_by_id=user_id,
        transaction_type_id=tx_type.id,
        street_address=address,
        city='Austin',
        state='TX',
        status='active',
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _add_offer(org_id, user_id, tx_id, buyer_names, buyer_agent_name=None, price='350000'):
    offer = SellerOffer(
        organization_id=org_id,
        transaction_id=tx_id,
        created_by_id=user_id,
        buyer_names=buyer_names,
        buyer_agent_name=buyer_agent_name,
        status='new',
        offer_price=Decimal(price),
    )
    db.session.add(offer)
    db.session.flush()
    version = SellerOfferVersion(
        organization_id=org_id,
        transaction_id=tx_id,
        offer_id=offer.id,
        created_by_id=user_id,
        version_number=1,
        direction='buyer_offer',
        status='submitted',
        terms_data={},
    )
    db.session.add(version)
    db.session.flush()
    offer.current_version_id = version.id
    return offer


def test_buyer_detail_shows_side_aware_offers_panel(app, seed, owner_a_client):
    with app.app_context():
        buyer_tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'buyer', '700 UI Buyer Ln',
        )
        _add_offer(
            seed['org_a'],
            seed['owner_a'],
            buyer_tx.id,
            buyer_names='Lakeside Seller LLC',
            buyer_agent_name='Pat Listing',
        )
        db.session.commit()
        buyer_tx_id = buyer_tx.id

    try:
        response = owner_a_client.get(f'/transactions/{buyer_tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Offers submitted' in html
        assert 'Lakeside Seller LLC' in html
        assert 'Listing agent' in html
        assert 'Buyer agent' not in html
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], buyer_tx_id)
            Transaction.query.filter_by(id=buyer_tx_id).delete()
            db.session.commit()


def test_seller_detail_still_shows_buyer_agent_and_offers_panel(
    app, seed, owner_a_client,
):
    with app.app_context():
        _cleanup_offers(seed['org_a'], seed['tx_a'])
        _add_offer(
            seed['org_a'],
            seed['owner_a'],
            seed['tx_a'],
            buyer_names='Seller Panel Buyer',
            buyer_agent_name='Agent Smith',
        )
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Buyer agent' in html
        assert 'id="seller-panel-offers"' in html
        assert 'Seller Panel Buyer' in html
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])


def test_buyer_detail_hides_accept_as_primary(app, seed, owner_a_client):
    with app.app_context():
        buyer_tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'buyer', '710 No Accept Rd',
        )
        _add_offer(
            seed['org_a'],
            seed['owner_a'],
            buyer_tx.id,
            buyer_names='No Accept Seller',
        )
        db.session.commit()
        buyer_tx_id = buyer_tx.id

    try:
        response = owner_a_client.get(f'/transactions/{buyer_tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Accept as primary' not in html
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], buyer_tx_id)
            Transaction.query.filter_by(id=buyer_tx_id).delete()
            db.session.commit()


def test_buyer_detail_shows_overage_notice_at_six_offers(app, seed, owner_a_client):
    with app.app_context():
        buyer_tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'buyer', '720 Overage Ave',
        )
        for index in range(6):
            _add_offer(
                seed['org_a'],
                seed['owner_a'],
                buyer_tx.id,
                buyer_names=f'Overage Seller {index + 1}',
                price=str(300000 + index * 1000),
            )
        db.session.commit()
        buyer_tx_id = buyer_tx.id

    try:
        response = owner_a_client.get(f'/transactions/{buyer_tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert '6 offers logged' in html
        assert 'Your plan includes 5' in html
        assert '$25' in html
        assert 'may bill' in html
        assert 'estimated' in html
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], buyer_tx_id)
            Transaction.query.filter_by(id=buyer_tx_id).delete()
            db.session.commit()


def test_offer_snapshot_reads_non_realty_items_from_the_addenda_bag(
    app, seed, owner_a_client,
):
    with app.app_context():
        _cleanup_offers(seed['org_a'], seed['tx_a'])
        offer = _add_offer(
            seed['org_a'],
            seed['owner_a'],
            seed['tx_a'],
            buyer_names='Snapshot Fallback Buyer',
        )
        offer.non_realty_items = None
        offer.terms_summary = {
            'addenda': {
                'non_realty_items_addendum': {
                    'items': ['Refrigerator', 'Pool equipment'],
                },
            },
        }
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        start = html.index('offer-command__snapshot')
        snapshot = html[start:start + 8000]
        assert 'Non-realty' in snapshot
        assert 'Refrigerator' in snapshot
        assert 'Pool equipment' in snapshot
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])


def test_blank_offer_form_clears_non_realty_items(app, seed, owner_a_client):
    with app.app_context():
        _cleanup_offers(seed['org_a'], seed['tx_a'])
        offer = _add_offer(
            seed['org_a'],
            seed['owner_a'],
            seed['tx_a'],
            buyer_names='Clear Items Buyer',
        )
        offer.non_realty_items = 'Refrigerator'
        offer.terms_summary = {
            'addenda': {'non_realty_items_addendum': {'items': ['Refrigerator']}},
        }
        db.session.commit()
        offer_id = offer.id

    response = owner_a_client.post(
        f'/transactions/{seed["tx_a"]}/offers/{offer_id}',
        json={'terms_data': {'non_realty_items': ''}},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    try:
        with app.app_context():
            saved = db.session.get(SellerOffer, offer_id)
            assert saved.non_realty_items == ''
            from services import offer_addenda
            assert offer_addenda.non_realty_items(saved) is None
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])


def test_buyer_detail_hides_overage_notice_under_limit(app, seed, owner_a_client):
    with app.app_context():
        buyer_tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'buyer', '730 Under Limit Blvd',
        )
        for index in range(3):
            _add_offer(
                seed['org_a'],
                seed['owner_a'],
                buyer_tx.id,
                buyer_names=f'Under Limit Seller {index + 1}',
                price=str(310000 + index * 1000),
            )
        db.session.commit()
        buyer_tx_id = buyer_tx.id

    try:
        response = owner_a_client.get(f'/transactions/{buyer_tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert '3 offers logged' in html or '3 offer' in html
        assert 'Your plan includes' not in html
        assert 'may bill' not in html
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], buyer_tx_id)
            Transaction.query.filter_by(id=buyer_tx_id).delete()
            db.session.commit()


def test_offer_form_js_sends_a_blank_non_realty_field():
    from pathlib import Path

    source = Path('static/js/transaction_detail.js').read_text()
    assert "field === 'non_realty_items'" in source
    assert 'if (value !== \'\' || field === \'non_realty_items\') terms[field] = value;' in source


def test_offer_form_shows_extracted_title_policy_payer(
    app, seed, owner_a_client,
):
    """Scoped intake leaves the column unset until review. The terms
    form still has to show the extracted payer from the current version."""
    with app.app_context():
        _cleanup_offers(seed['org_a'], seed['tx_a'])
        offer = _add_offer(
            seed['org_a'],
            seed['owner_a'],
            seed['tx_a'],
            buyer_names='Title Policy Form Buyer',
        )
        offer.title_policy_payer = None
        offer.terms_summary = {}
        version = db.session.get(SellerOfferVersion, offer.current_version_id)
        version.terms_data = {'title_policy_payer': 'Seller'}
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert (
            'name="terms_data[title_policy_payer]" class="crm-input" '
            'value="Seller"'
        ) in html
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])
