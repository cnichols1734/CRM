"""HTTP routes for the offer compare workspace + highest-and-best."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from models import (
    SellerListingProfile,
    SellerOffer,
    SellerOfferActivity,
    SellerOfferVersion,
    TransactionAssignment,
    db,
)


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


def _clear_agent_assignments(org_id, tx_id, user_id):
    TransactionAssignment.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
        user_id=user_id,
    ).delete()
    db.session.commit()


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
    SellerListingProfile.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
    ).delete(synchronize_session=False)
    db.session.commit()


def test_compare_page_two_offers_renders(app, seed, owner_a_client):
    with app.app_context():
        org_id = seed['org_a']
        tx_id = seed['tx_a']
        user_id = seed['owner_a']
        _cleanup_offers(org_id, tx_id)
        _offer(
            org_id, tx_id, user_id,
            buyer_names='Alpha Buyer',
            offer_price=Decimal('410000'),
        )
        _offer(
            org_id, tx_id, user_id,
            buyer_names='Bravo Buyer',
            offer_price=Decimal('425000'),
        )
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}/offers/compare')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Alpha Buyer' in html
        assert 'Bravo Buyer' in html
        assert 'Estimated net' in html

        # The matrix must render real term values, not placeholders. Buyer
        # labels alone rendered fine while every cell was an em dash.
        assert '$410,000' in html
        assert '$425,000' in html
        assert 'Sep 15, 2026' in html
        # Net sheet totals are computed, not the unused net_to_seller_estimate.
        assert 'Estimated net to seller' in html
        assert '$410,000' in html.split('Estimated net to seller')[0]
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])


def test_compare_page_one_offer_honest_state(app, seed, owner_a_client):
    with app.app_context():
        org_id = seed['org_a']
        tx_id = seed['tx_a']
        user_id = seed['owner_a']
        _cleanup_offers(org_id, tx_id)
        _offer(org_id, tx_id, user_id, buyer_names='Solo Buyer')
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}/offers/compare')
        assert response.status_code == 200
        assert response.status_code not in (301, 302, 303, 307, 308)
        html = response.get_data(as_text=True)
        assert 'Nothing to compare yet' in html
        assert 'at least two active offers' in html
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])


def test_compare_page_other_org_blocked(app, seed, owner_a_client):
    with app.app_context():
        org_id = seed['org_b']
        tx_id = seed['tx_b']
        user_id = seed['owner_b']
        _cleanup_offers(org_id, tx_id)
        _offer(org_id, tx_id, user_id, buyer_names='Other Org A')
        _offer(org_id, tx_id, user_id, buyer_names='Other Org B')
        db.session.commit()

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}/offers/compare')
        assert response.status_code != 200
        assert response.status_code in (403, 404)
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_b'], seed['tx_b'])


def test_highest_and_best_records_state(app, seed, owner_a_client):
    with app.app_context():
        org_id = seed['org_a']
        tx_id = seed['tx_a']
        user_id = seed['owner_a']
        _cleanup_offers(org_id, tx_id)
        included_a = _offer(org_id, tx_id, user_id, buyer_names='Include A')
        included_b = _offer(org_id, tx_id, user_id, buyer_names='Include B')
        excluded = _offer(org_id, tx_id, user_id, buyer_names='Leave Out')
        db.session.commit()
        a_id, b_id, excluded_id = included_a.id, included_b.id, excluded.id

    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/highest-and-best',
            json={
                'deadline_at': '2026-08-20T17:00:00',
                'message': 'Please submit your best offer',
                'offer_ids': [a_id, b_id],
            },
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['success'] is True
        assert payload.get('sent') is False
        assert set(payload['offer_ids']) == {a_id, b_id}

        with app.app_context():
            profile = SellerListingProfile.query.filter_by(
                organization_id=seed['org_a'],
                transaction_id=tx_id,
            ).first()
            assert profile is not None
            assert profile.highest_best_enabled is True
            assert profile.highest_best_deadline_at == datetime(2026, 8, 20, 17, 0, 0)
            assert profile.highest_best_message == 'Please submit your best offer'

            a = db.session.get(SellerOffer, a_id)
            b = db.session.get(SellerOffer, b_id)
            left = db.session.get(SellerOffer, excluded_id)
            assert a.included_in_highest_best is True
            assert b.included_in_highest_best is True
            assert left.included_in_highest_best is False
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])


def test_highest_and_best_unparseable_deadline(app, seed, owner_a_client):
    with app.app_context():
        org_id = seed['org_a']
        tx_id = seed['tx_a']
        user_id = seed['owner_a']
        _cleanup_offers(org_id, tx_id)
        offer = _offer(org_id, tx_id, user_id, buyer_names='Deadline Bad')
        db.session.commit()
        offer_id = offer.id

    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/highest-and-best',
            json={
                'deadline_at': 'not-a-real-date',
                'offer_ids': [offer_id],
            },
        )
        assert response.status_code == 400
        payload = response.get_json()
        assert payload['success'] is False
        assert 'deadline' in payload['error'].lower() or 'date' in payload['error'].lower()
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])


def test_collaborator_cannot_post_highest_and_best(app, seed, agent_a_client):
    with app.app_context():
        org_id = seed['org_a']
        tx_id = seed['tx_a']
        user_id = seed['owner_a']
        _cleanup_offers(org_id, tx_id)
        _clear_agent_assignments(org_id, tx_id, seed['agent_a'])
        offer = _offer(org_id, tx_id, user_id, buyer_names='No Edit')
        assignment = TransactionAssignment(
            organization_id=org_id,
            transaction_id=tx_id,
            user_id=seed['agent_a'],
            role='collaborator',
        )
        db.session.add(assignment)
        db.session.commit()
        offer_id = offer.id

    try:
        response = agent_a_client.post(
            f'/transactions/{tx_id}/offers/highest-and-best',
            json={
                'deadline_at': '2026-08-20T17:00:00',
                'offer_ids': [offer_id],
            },
        )
        assert response.status_code != 200
        assert response.status_code in (403, 404)
    finally:
        with app.app_context():
            _cleanup_offers(seed['org_a'], seed['tx_a'])
            _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])
