"""Buyer (and non-offer) transaction access to shared offer endpoints."""

from __future__ import annotations

from decimal import Decimal

from flask import template_rendered

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


def test_buyer_can_list_and_create_offers(app, seed, owner_a_client):
    with app.app_context():
        buyer_tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'buyer', '400 Buyer Lane',
        )
        db.session.commit()
        buyer_tx_id = buyer_tx.id
        _cleanup_offers(seed['org_a'], buyer_tx_id)

    try:
        list_response = owner_a_client.get(f'/transactions/{buyer_tx_id}/offers')
        assert list_response.status_code == 200
        assert list_response.get_json()['success'] is True
        assert list_response.get_json()['offers'] == []

        create_response = owner_a_client.post(
            f'/transactions/{buyer_tx_id}/offers',
            json={
                'buyer_names': 'Our Buyer',
                'buyer_agent_name': 'Listing Agent',
                'terms': {'offer_price': '375000'},
            },
        )
        assert create_response.status_code == 201
        payload = create_response.get_json()
        assert payload['success'] is True
        assert payload['offer']['buyer_names'] == 'Our Buyer'

        with app.app_context():
            offer = SellerOffer.query.filter_by(
                id=payload['offer']['id'],
                organization_id=seed['org_a'],
                transaction_id=buyer_tx_id,
            ).first()
            assert offer is not None
            version = SellerOfferVersion.query.filter_by(
                id=offer.current_version_id,
                organization_id=seed['org_a'],
            ).first()
            assert version is not None
            assert version.direction == 'buyer_offer'
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], buyer_tx_id)
            Transaction.query.filter_by(id=buyer_tx_id).delete()
            db.session.commit()


def test_landlord_offers_still_rejected(app, seed, owner_a_client):
    with app.app_context():
        landlord_tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'landlord', '500 Lease Ave',
        )
        db.session.commit()
        landlord_tx_id = landlord_tx.id

    try:
        response = owner_a_client.get(f'/transactions/{landlord_tx_id}/offers')
        assert response.status_code == 400
        body = response.get_json()
        assert body['success'] is False
        assert 'buyer and seller' in body['error']
    finally:
        with app.app_context():
            Transaction.query.filter_by(id=landlord_tx_id).delete()
            db.session.commit()


def test_buyer_detail_loads_offers_in_context(app, seed, owner_a_client):
    with app.app_context():
        buyer_tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'buyer', '600 Context Rd',
        )
        db.session.flush()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=buyer_tx.id,
            created_by_id=seed['owner_a'],
            buyer_names='Context Buyer',
            status='new',
            offer_price=Decimal('410000'),
        )
        db.session.add(offer)
        db.session.flush()
        version = SellerOfferVersion(
            organization_id=seed['org_a'],
            transaction_id=buyer_tx.id,
            offer_id=offer.id,
            created_by_id=seed['owner_a'],
            version_number=1,
            direction='buyer_offer',
            status='submitted',
            terms_data={},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id
        db.session.commit()
        buyer_tx_id = buyer_tx.id
        offer_id = offer.id

    recorded = []

    def _capture(sender, template, context, **extra):
        if template.name == 'transactions/detail.html':
            recorded.append(context)

    try:
        with template_rendered.connected_to(_capture, app):
            response = owner_a_client.get(f'/transactions/{buyer_tx_id}')
        assert response.status_code == 200
        assert recorded, 'detail template context was not captured'
        context = recorded[0]
        assert context['offer_side'] == 'buyer'
        assert context['offer_labels']['panel_title'] == 'Offers submitted'
        assert context['offer_metering'] is not None
        assert context['offer_metering']['offer_count'] == 1
        assert any(row.id == offer_id for row in context['seller_offers'])
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], buyer_tx_id)
            Transaction.query.filter_by(id=buyer_tx_id).delete()
            db.session.commit()


def test_buyer_transaction_cannot_open_seller_offer_compare(app, seed, owner_a_client):
    """The compare net sheet is seller proceeds; buyer files must not reach it."""
    with app.app_context():
        buyer_tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'buyer', '402 Buyer Lane',
        )
        db.session.commit()
        buyer_tx_id = buyer_tx.id

    try:
        response = owner_a_client.get(f'/transactions/{buyer_tx_id}/offers/compare')
        assert response.status_code == 404

        response = owner_a_client.post(
            f'/transactions/{buyer_tx_id}/offers/highest-and-best',
            json={'deadline_at': '2026-09-01T17:00', 'offer_ids': []},
        )
        assert response.status_code == 404
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], buyer_tx_id)
            Transaction.query.filter_by(id=buyer_tx_id).delete()
            db.session.commit()
