"""Phase 2 offer compare assist (read-only)."""

from decimal import Decimal

from models import SellerOffer, SellerOfferVersion, Transaction, db
from services.offer_compare import OfferCompareService


def _offer(org_id, tx_id, user_id, **kwargs):
    defaults = dict(
        organization_id=org_id,
        transaction_id=tx_id,
        created_by_id=user_id,
        buyer_names=kwargs.pop('buyer_names', 'Buyer A'),
        status='new',
        offer_price=kwargs.pop('offer_price', Decimal('400000')),
        earnest_money=kwargs.pop('earnest_money', Decimal('5000')),
        financing_type=kwargs.pop('financing_type', 'conventional'),
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


def test_compare_offers_highlights_differences(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = Transaction.query.get(seed['tx_a'])
        user_id = seed['owner_a']

        _offer(
            org_id, tx.id, user_id,
            buyer_names='Alpha',
            offer_price=Decimal('410000'),
            option_fee=Decimal('200'),
            financing_type='conventional',
        )
        _offer(
            org_id, tx.id, user_id,
            buyer_names='Bravo',
            offer_price=Decimal('425000'),
            option_fee=Decimal('500'),
            financing_type='cash',
        )
        db.session.commit()

        result = OfferCompareService.compare_offers(tx)
        assert result['read_only'] is True
        assert result['offer_count'] == 2
        assert 'offer_price' in result['differing_fields']
        assert result['highlights']['highest_price']['buyer_label'] == 'Bravo'
        assert 'Comparing 2 offers' in result['summary']


def test_compare_offers_filters_by_ids(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = Transaction.query.get(seed['tx_a'])
        user_id = seed['owner_a']
        a = _offer(org_id, tx.id, user_id, buyer_names='Only A', offer_price=Decimal('300000'))
        _offer(org_id, tx.id, user_id, buyer_names='Skip Me', offer_price=Decimal('350000'))
        db.session.commit()

        result = OfferCompareService.compare_offers(tx, offer_ids=[a.id])
        assert result['offer_count'] == 1
        assert result['offers'][0]['buyer_names'] == 'Only A'
