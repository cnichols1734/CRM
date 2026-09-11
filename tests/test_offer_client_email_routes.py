"""Preview and send endpoints for the client offer email."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from models import (
    SellerOffer,
    SellerOfferActivity,
    SellerOfferVersion,
    Transaction,
    TransactionParticipant,
    TransactionType,
    db,
)


def _make_transaction(org_id, user_id, type_name, address):
    tx_type = TransactionType.query.filter_by(
        organization_id=org_id, name=type_name,
    ).first()
    if tx_type is None:
        tx_type = TransactionType(
            organization_id=org_id, name=type_name,
            display_name=type_name.title(),
        )
        db.session.add(tx_type)
        db.session.flush()
    tx = Transaction(
        organization_id=org_id,
        created_by_id=user_id,
        transaction_type_id=tx_type.id,
        street_address=address,
        city='Katy',
        state='TX',
        status='active',
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _make_offer(org_id, tx_id, user_id, **fields):
    offer = SellerOffer(
        organization_id=org_id,
        transaction_id=tx_id,
        created_by_id=user_id,
        status='new',
        **fields,
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


def _teardown(tx_id):
    offer_ids = [row.id for row in SellerOffer.query.filter_by(transaction_id=tx_id).all()]
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
    TransactionParticipant.query.filter_by(transaction_id=tx_id).delete(
        synchronize_session=False,
    )
    Transaction.query.filter_by(id=tx_id).delete()
    db.session.commit()


def _seller_listing(seed, *, offers, with_client=True):
    """A seller transaction with the given offer field dicts."""
    tx = _make_transaction(
        seed['org_a'], seed['owner_a'], 'seller', '6048 Heritage Creek Dr',
    )
    if with_client:
        db.session.add(TransactionParticipant(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            role='seller',
            name='Cassie Nichols',
            email='cassie@origenrealty.com',
            is_primary=True,
        ))
    created = [
        _make_offer(seed['org_a'], tx.id, seed['owner_a'], **fields)
        for fields in offers
    ]
    db.session.commit()
    return tx.id, [offer.id for offer in created]


ONE_OFFER = dict(
    buyer_names='Jordan and Riley Vance',
    buyer_agent_name='Dana Reed',
    buyer_agent_brokerage='Keller Williams',
    offer_price=Decimal('425000'),
    financing_type='conventional',
    earnest_money=Decimal('5000'),
    option_fee=Decimal('300'),
    option_period_days=7,
    proposed_close_date=date(2026, 3, 15),
)


def test_preview_builds_a_single_offer_email(app, seed, owner_a_client):
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': offer_ids},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['success'] is True

        draft = body['draft']
        assert draft['mode'] == 'single'
        assert draft['subject'] == 'New offer on 6048 Heritage Creek Dr: $425,000'
        assert draft['greeting'] == 'Hi Cassie,'
        assert draft['recipients'][0]['email'] == 'cassie@origenrealty.com'

        assert '$425,000' in body['html']
        assert 'Conventional loan' in body['html']
        assert '7 days, $300 fee' in body['html']
        assert body['sender']['via'] in ('gmail', 'sendgrid')
        assert body['sender']['from_email']
    finally:
        with app.app_context():
            _teardown(tx_id)


@pytest.mark.parametrize('payload', ({}, {'offer_ids': None}))
def test_preview_covers_every_active_offer_when_none_is_named(
    app, seed, owner_a_client, payload,
):
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview', json=payload,
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['draft']['mode'] == 'compare'
        assert set(body['draft']['offer_ids']) == set(offer_ids)
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_preview_candidate_price_uses_version_terms_when_column_empty(
    app, seed, owner_a_client,
):
    """Unreviewed price lives on the version. The picker has to show it."""
    fields = dict(ONE_OFFER)
    fields.pop('offer_price')
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[fields])
        offer = SellerOffer.query.get(offer_ids[0])
        version = SellerOfferVersion.query.get(offer.current_version_id)
        version.terms_data = {'offer_price': '418000'}
        db.session.commit()
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': offer_ids},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['candidates'][0]['price'] == '$418,000'
        assert body['draft']['offers'][0]['terms']['offer_price']['value'] == '$418,000'
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_preview_compares_several_offers_side_by_side(app, seed, owner_a_client):
    second = dict(ONE_OFFER)
    second.update(
        buyer_names='Priya Shah',
        offer_price=Decimal('440000'),
        financing_type='cash',
        proposed_close_date=date(2026, 2, 20),
    )
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': offer_ids},
        )
        assert response.status_code == 200
        body = response.get_json()
        draft = body['draft']

        assert draft['mode'] == 'compare'
        assert draft['subject'] == (
            '2 offers on 6048 Heritage Creek Dr: $425,000 to $440,000'
        )
        assert [o['label'] for o in draft['offers']] == [
            'Priya Shah', 'Jordan and Riley Vance',
        ]
        assert len(body['candidates']) == 2
        assert 'Offer comparison' in body['html']
        assert 'Priya Shah' in body['html']
        assert 'Jordan and Riley Vance' in body['html']
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_preview_keeps_the_agents_own_wording(app, seed, owner_a_client):
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={
                'offer_ids': offer_ids,
                'subject': 'Strong offer came in',
                'note': 'I would counter at 435.',
                'terms': {str(offer_ids[0]): {'earnest_money': '$9,000'}},
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['draft']['subject'] == 'Strong offer came in'
        assert 'I would counter at 435.' in body['html']
        assert '$9,000' in body['html']
        terms = body['draft']['offers'][0]['terms']
        assert terms['earnest_money']['edited'] is True
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_preview_reflects_a_terms_edit_on_the_next_run(app, seed, owner_a_client):
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER])
    try:
        first = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': offer_ids},
        ).get_json()
        assert '$425,000' in first['html']

        updated = owner_a_client.post(
            f'/transactions/{tx_id}/offers/{offer_ids[0]}',
            json={'terms': {'offer_price': '451000'}},
        )
        assert updated.status_code == 200

        second = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': offer_ids},
        ).get_json()
        assert '$451,000' in second['html']
        assert second['draft']['subject'].endswith('$451,000')
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_preview_warns_when_no_client_email_is_on_file(app, seed, owner_a_client):
    with app.app_context():
        tx_id, offer_ids = _seller_listing(
            seed, offers=[ONE_OFFER], with_client=False,
        )
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': offer_ids},
        )
        assert response.status_code == 200
        assert response.get_json()['draft']['recipients'] == []
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_preview_needs_an_offer_to_summarize(app, seed, owner_a_client):
    with app.app_context():
        tx_id, _ = _seller_listing(seed, offers=[])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview', json={},
        )
        assert response.status_code == 400
        assert 'no active offers' in response.get_json()['error']
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_send_records_the_email_against_every_offer(app, seed, owner_a_client):
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/send',
            json={
                'offer_ids': offer_ids,
                'to': 'cassie@example.com',
                'subject': 'Both offers on Heritage Creek',
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['success'] is True
        # conftest forces TESTING, so nothing leaves the process.
        assert body['skipped'] is True
        assert body['recipients'] == ['cassie@example.com']

        with app.app_context():
            logged = SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(offer_ids),
                SellerOfferActivity.event_type == 'client_email_sent',
            ).all()
            assert len(logged) == 2
            assert '2-offer comparison emailed' in logged[0].label
            assert logged[0].event_data['subject'] == 'Both offers on Heritage Creek'
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_send_rejects_an_empty_offer_list(app, seed, owner_a_client):
    """An empty picker is none selected, not Email all."""
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/send',
            json={'offer_ids': [], 'to': 'cassie@example.com'},
        )
        assert response.status_code == 400
        assert 'at least one offer' in response.get_json()['error']

        with app.app_context():
            assert SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(offer_ids),
                SellerOfferActivity.event_type == 'client_email_sent',
            ).count() == 0
    finally:
        with app.app_context():
            _teardown(tx_id)


@pytest.mark.parametrize('offer_ids', ([None], ['bad'], [True], [False], [1.9]))
def test_send_rejects_unparseable_offer_ids(app, seed, owner_a_client, offer_ids):
    """A present list with no valid IDs is none selected, not Email all."""
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, created_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/send',
            json={'offer_ids': offer_ids, 'to': 'cassie@example.com'},
        )
        assert response.status_code == 400
        assert 'at least one offer' in response.get_json()['error']

        with app.app_context():
            assert SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(created_ids),
                SellerOfferActivity.event_type == 'client_email_sent',
            ).count() == 0
    finally:
        with app.app_context():
            _teardown(tx_id)


@pytest.mark.parametrize('offer_ids', ([None], ['bad'], [True], [False], [1.9]))
def test_preview_rejects_unparseable_offer_ids(app, seed, owner_a_client, offer_ids):
    """Same hole on preview: do not expand junk IDs to every live offer."""
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, _ = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': offer_ids},
        )
        assert response.status_code == 400
        assert 'at least one offer' in response.get_json()['error']
        assert response.get_json().get('draft') is None
    finally:
        with app.app_context():
            _teardown(tx_id)


@pytest.mark.parametrize('offer_ids', ('bad', 123))
def test_send_rejects_non_list_offer_ids(app, seed, owner_a_client, offer_ids):
    """A present non-list is none selected, not Email all."""
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, created_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/send',
            json={'offer_ids': offer_ids, 'to': 'cassie@example.com'},
        )
        assert response.status_code == 400
        assert 'at least one offer' in response.get_json()['error']

        with app.app_context():
            assert SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(created_ids),
                SellerOfferActivity.event_type == 'client_email_sent',
            ).count() == 0
    finally:
        with app.app_context():
            _teardown(tx_id)


@pytest.mark.parametrize('offer_ids', ('bad', 123))
def test_preview_rejects_non_list_offer_ids(app, seed, owner_a_client, offer_ids):
    """Same hole on preview: do not expand a non-list to every live offer."""
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, _ = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': offer_ids},
        )
        assert response.status_code == 400
        assert 'at least one offer' in response.get_json()['error']
        assert response.get_json().get('draft') is None
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_parse_requested_offer_ids_skips_bools_and_fractional_floats():
    """int(True)==1 and int(1.9)==1. Those must not look like a selected offer."""
    from routes.transactions.offers import (
        _none_selected_offer_ids,
        _parse_requested_offer_ids,
    )

    assert _parse_requested_offer_ids([True]) == []
    assert _parse_requested_offer_ids([False]) == []
    assert _parse_requested_offer_ids([1.9]) == []
    assert _parse_requested_offer_ids([1]) == [1]
    assert _parse_requested_offer_ids(['12']) == [12]
    assert _parse_requested_offer_ids(None) is None
    assert _none_selected_offer_ids([True]) is True
    assert _none_selected_offer_ids([False]) is True
    assert _none_selected_offer_ids([1.9]) is True
    assert _none_selected_offer_ids([1]) is False
    assert _none_selected_offer_ids(None) is False


def test_send_covers_only_the_named_offers(app, seed, owner_a_client):
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        chosen_id = offer_ids[0]
        skipped_id = offer_ids[1]
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/send',
            json={
                'offer_ids': [chosen_id],
                'to': 'cassie@example.com',
            },
        )
        assert response.status_code == 200
        assert response.get_json()['success'] is True

        with app.app_context():
            logged = SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(offer_ids),
                SellerOfferActivity.event_type == 'client_email_sent',
            ).all()
            assert [row.offer_id for row in logged] == [chosen_id]
            assert skipped_id not in {row.offer_id for row in logged}
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_preview_covers_only_the_named_offers(app, seed, owner_a_client):
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview',
            json={'offer_ids': [offer_ids[0]]},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['draft']['mode'] == 'single'
        assert body['draft']['offer_ids'] == [offer_ids[0]]
    finally:
        with app.app_context():
            _teardown(tx_id)


@pytest.mark.parametrize('extra', ({}, {'offer_ids': None}))
def test_send_covers_every_active_offer_when_none_is_named(
    app, seed, owner_a_client, extra,
):
    """Header Email all omits offer_ids. That still means every live offer."""
    second = dict(ONE_OFFER)
    second.update(buyer_names='Priya Shah', offer_price=Decimal('440000'))
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER, second])
    try:
        payload = {'to': 'cassie@example.com'}
        payload.update(extra)
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/send',
            json=payload,
        )
        assert response.status_code == 200
        assert response.get_json()['success'] is True

        with app.app_context():
            logged = SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(offer_ids),
                SellerOfferActivity.event_type == 'client_email_sent',
            ).all()
            assert len(logged) == 2
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_send_requires_a_recipient(app, seed, owner_a_client):
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER])
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/send',
            json={'offer_ids': offer_ids, 'to': '   '},
        )
        assert response.status_code == 400
        assert 'who this goes to' in response.get_json()['error']

        with app.app_context():
            assert SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(offer_ids),
                SellerOfferActivity.event_type == 'client_email_sent',
            ).count() == 0
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_a_lease_transaction_has_no_client_offer_email(app, seed, owner_a_client):
    with app.app_context():
        tx = _make_transaction(
            seed['org_a'], seed['owner_a'], 'landlord', '500 Lease Ave',
        )
        db.session.commit()
        tx_id = tx.id
    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/offers/client-email/preview', json={},
        )
        assert response.status_code == 400
        assert 'buyer and seller' in response.get_json()['error']
    finally:
        with app.app_context():
            _teardown(tx_id)


def test_another_org_never_sees_the_offer(app, seed, owner_b_client):
    """Org B is free tier, so the transactions gate turns it away before the
    org scope check runs. Either way, none of org A's terms may come back.
    """
    with app.app_context():
        tx_id, offer_ids = _seller_listing(seed, offers=[ONE_OFFER])
    try:
        for path, payload in (
            ('preview', {'offer_ids': offer_ids}),
            ('send', {'offer_ids': offer_ids, 'to': 'someone@example.com'}),
        ):
            response = owner_b_client.post(
                f'/transactions/{tx_id}/offers/client-email/{path}', json=payload,
            )
            assert response.status_code != 200
            assert b'Heritage Creek' not in response.data
            assert b'425,000' not in response.data

        with app.app_context():
            assert SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(offer_ids),
            ).count() == 0
    finally:
        with app.app_context():
            _teardown(tx_id)
