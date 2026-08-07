"""Advisory offer overage metering."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from models import SellerOffer, SellerOfferVersion, db
from services.offer_metering import metering_for_transaction


def _offer(org_id, tx_id, user_id, **kwargs):
    defaults = dict(
        organization_id=org_id,
        transaction_id=tx_id,
        created_by_id=user_id,
        buyer_names=kwargs.pop('buyer_names', 'Buyer A'),
        status=kwargs.pop('status', 'new'),
        offer_price=kwargs.pop('offer_price', Decimal('400000')),
        earnest_money=kwargs.pop('earnest_money', Decimal('5000')),
        financing_type=kwargs.pop('financing_type', 'conventional'),
        proposed_close_date=kwargs.pop('proposed_close_date', date(2026, 9, 15)),
    )
    defaults.update(kwargs)
    offer = SellerOffer(**defaults)
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
    db.session.flush()
    return offer


def _cleanup_offers(org_id, tx_id):
    from models import SellerOfferDocument

    offer_ids = [
        row.id
        for row in SellerOffer.query.filter_by(
            organization_id=org_id,
            transaction_id=tx_id,
        ).all()
    ]
    if offer_ids:
        # SQLite test DB often has FK enforcement off — delete children explicitly
        # so orphan seller_offer_documents cannot reattach when IDs are reused.
        SellerOfferDocument.query.filter(
            SellerOfferDocument.offer_id.in_(offer_ids),
        ).delete(synchronize_session=False)
        SellerOfferVersion.query.filter(
            SellerOfferVersion.offer_id.in_(offer_ids),
        ).delete(synchronize_session=False)
        SellerOffer.query.filter(SellerOffer.id.in_(offer_ids)).delete(
            synchronize_session=False,
        )
    db.session.commit()


def test_five_offers_no_overage(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx_id = seed['tx_a']
        user_id = seed['owner_a']
        _cleanup_offers(org_id, tx_id)
        for index in range(5):
            _offer(org_id, tx_id, user_id, buyer_names=f'Buyer {index}')
        db.session.commit()

        try:
            metering = metering_for_transaction(tx_id, org_id)
            assert metering['offer_count'] == 5
            assert metering['over_limit'] is False
            assert metering['overage_count'] == 0
            assert metering['overage_total'] == Decimal('0')
            assert isinstance(metering['overage_total'], Decimal)
            assert isinstance(metering['overage_fee_each'], Decimal)
        finally:
            _cleanup_offers(org_id, tx_id)


def test_seven_offers_two_overage(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx_id = seed['tx_a']
        user_id = seed['owner_a']
        _cleanup_offers(org_id, tx_id)
        for index in range(7):
            _offer(org_id, tx_id, user_id, buyer_names=f'Buyer {index}')
        db.session.commit()

        try:
            metering = metering_for_transaction(tx_id, org_id)
            assert metering['offer_count'] == 7
            assert metering['overage_count'] == 2
            assert metering['overage_total'] == Decimal('50.00')
            assert metering['over_limit'] is True
        finally:
            _cleanup_offers(org_id, tx_id)


def test_replaced_and_withdrawn_excluded(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx_id = seed['tx_a']
        user_id = seed['owner_a']
        _cleanup_offers(org_id, tx_id)
        _offer(org_id, tx_id, user_id, buyer_names='Active', status='new')
        _offer(org_id, tx_id, user_id, buyer_names='Replaced', status='replaced')
        _offer(org_id, tx_id, user_id, buyer_names='Withdrawn', status='withdrawn')
        db.session.commit()

        try:
            metering = metering_for_transaction(tx_id, org_id)
            assert metering['offer_count'] == 1
            assert metering['over_limit'] is False
        finally:
            _cleanup_offers(org_id, tx_id)


def test_other_org_offers_not_counted(app, seed):
    with app.app_context():
        org_a = seed['org_a']
        org_b = seed['org_b']
        tx_a = seed['tx_a']
        tx_b = seed['tx_b']
        user_a = seed['owner_a']
        user_b = seed['owner_b']
        _cleanup_offers(org_a, tx_a)
        _cleanup_offers(org_b, tx_b)
        _offer(org_a, tx_a, user_a, buyer_names='Org A offer')
        _offer(org_b, tx_b, user_b, buyer_names='Org B offer')
        db.session.commit()

        try:
            metering = metering_for_transaction(tx_a, org_a)
            assert metering['offer_count'] == 1
            # Same transaction id queried under the wrong org must not leak.
            assert metering_for_transaction(tx_a, org_b)['offer_count'] == 0
        finally:
            _cleanup_offers(org_a, tx_a)
            _cleanup_offers(org_b, tx_b)
