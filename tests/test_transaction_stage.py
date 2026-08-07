"""Whole-transaction stage derivation and surface relevance."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from models import (
    SellerAcceptedContract,
    SellerOffer,
    SellerOfferVersion,
    Transaction,
    TransactionType,
    db,
)
from services.transaction_stage import (
    BUYER_STAGE_KEYS,
    SELLER_STAGE_KEYS,
    relevant_surfaces,
    stage_for_transaction,
    stage_keys_for_side,
    surface_visibility,
)


def _cleanup_tx(org_id, tx_id):
    offer_ids = [
        row.id
        for row in SellerOffer.query.filter_by(
            organization_id=org_id,
            transaction_id=tx_id,
        ).all()
    ]
    if offer_ids:
        SellerOfferVersion.query.filter(
            SellerOfferVersion.offer_id.in_(offer_ids),
        ).delete(synchronize_session=False)
        SellerOffer.query.filter(SellerOffer.id.in_(offer_ids)).delete(
            synchronize_session=False,
        )
    SellerAcceptedContract.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
    ).delete(synchronize_session=False)
    Transaction.query.filter_by(id=tx_id, organization_id=org_id).delete(
        synchronize_session=False,
    )
    db.session.commit()


def _tx_type(org_id, name):
    tx_type = TransactionType.query.filter_by(
        organization_id=org_id,
        name=name,
    ).first()
    if tx_type is None:
        tx_type = TransactionType(
            organization_id=org_id,
            name=name,
            display_name=name.title(),
        )
        db.session.add(tx_type)
        db.session.flush()
    return tx_type


def _fresh_tx(seed, *, side='seller', status='active', **kwargs):
    tx_type = _tx_type(seed['org_a'], side)
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=tx_type.id,
        street_address=kwargs.pop('street_address', '900 Stage St'),
        city='Austin',
        state='TX',
        status=status,
        **kwargs,
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _offer(seed, tx_id, **kwargs):
    defaults = dict(
        organization_id=seed['org_a'],
        transaction_id=tx_id,
        created_by_id=seed['owner_a'],
        buyer_names=kwargs.pop('buyer_names', 'Stage Buyer'),
        status='new',
        offer_price=kwargs.pop('offer_price', Decimal('400000')),
    )
    defaults.update(kwargs)
    offer = SellerOffer(**defaults)
    db.session.add(offer)
    db.session.flush()
    return offer


def _primary_contract(seed, tx_id, **kwargs):
    defaults = dict(
        organization_id=seed['org_a'],
        transaction_id=tx_id,
        created_by_id=seed['owner_a'],
        position='primary',
        status='active',
        accepted_price=Decimal('410000'),
        closing_date=kwargs.pop('closing_date', date(2026, 9, 15)),
    )
    defaults.update(kwargs)
    contract = SellerAcceptedContract(**defaults)
    db.session.add(contract)
    db.session.flush()
    return contract


class TestSellerStages:
    def test_active_without_listing_agreement_is_prelisting(self, app, seed):
        with app.app_context():
            tx_id = None
            try:
                tx = _fresh_tx(seed, side='seller', status='active')
                tx_id = tx.id
                db.session.commit()
                tx = Transaction.query.get(tx_id)
                stage = stage_for_transaction(tx, has_listing_agreement=False)
                assert stage.key == 'prelisting'
                assert stage.label == 'Pre-listing'
                assert stage.is_terminal is False
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id)

    def test_listing_agreement_no_offers_is_listed(self, app, seed):
        with app.app_context():
            tx_id = None
            try:
                tx = _fresh_tx(seed, side='seller', status='active')
                tx_id = tx.id
                db.session.commit()
                tx = Transaction.query.get(tx_id)
                stage = stage_for_transaction(
                    tx,
                    has_listing_agreement=True,
                    open_offers=0,
                )
                assert stage.key == 'listed'
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id)

    def test_two_open_offers_no_contract_is_offers(self, app, seed):
        with app.app_context():
            tx_id = None
            try:
                tx = _fresh_tx(seed, side='seller', status='active')
                tx_id = tx.id
                _offer(seed, tx_id, buyer_names='Alpha')
                _offer(seed, tx_id, buyer_names='Bravo')
                db.session.commit()
                tx = Transaction.query.get(tx_id)
                offers = SellerOffer.query.filter_by(
                    organization_id=seed['org_a'],
                    transaction_id=tx_id,
                ).all()
                stage = stage_for_transaction(
                    tx,
                    has_listing_agreement=True,
                    open_offers=offers,
                )
                assert stage.key == 'offers'
                assert stage.summary
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id)

    def test_active_primary_contract_outranks_active_status(self, app, seed):
        with app.app_context():
            tx_id = None
            try:
                tx = _fresh_tx(seed, side='seller', status='active')
                tx_id = tx.id
                contract = _primary_contract(
                    seed, tx_id,
                    closing_date=date.today() + timedelta(days=40),
                )
                db.session.commit()
                tx = Transaction.query.get(tx_id)
                stage = stage_for_transaction(
                    tx,
                    has_listing_agreement=True,
                    open_offers=2,
                    primary_contract=contract,
                    today=date.today(),
                )
                assert stage.key == 'under_contract'
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id)

    def test_closing_window_vs_under_contract(self, app, seed):
        with app.app_context():
            tx_id = None
            try:
                today = date(2026, 8, 5)
                tx = _fresh_tx(
                    seed, side='seller', status='under_contract',
                )
                tx_id = tx.id
                near = _primary_contract(
                    seed, tx_id,
                    closing_date=today + timedelta(days=5),
                )
                db.session.commit()
                tx = Transaction.query.get(tx_id)
                near_stage = stage_for_transaction(
                    tx, primary_contract=near, today=today,
                )
                assert near_stage.key == 'closing'

                far = SimpleNamespace(
                    position='primary',
                    status='active',
                    closing_date=today + timedelta(days=40),
                )
                far_stage = stage_for_transaction(
                    tx, primary_contract=far, today=today,
                )
                assert far_stage.key == 'under_contract'
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id)

    def test_under_contract_without_closing_date_stays_under_contract(
        self, app, seed,
    ):
        with app.app_context():
            tx_id = None
            try:
                tx = _fresh_tx(seed, side='seller', status='under_contract')
                tx.expected_close_date = None
                tx_id = tx.id
                contract = _primary_contract(seed, tx_id, closing_date=None)
                contract.closing_date = None
                db.session.commit()
                tx = Transaction.query.get(tx_id)
                stage = stage_for_transaction(
                    tx,
                    primary_contract=contract,
                    today=date.today(),
                )
                assert stage.key == 'under_contract'
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id)

    def test_cancelled_and_closed_are_terminal(self, app, seed):
        with app.app_context():
            tx_ids = []
            try:
                cancelled = _fresh_tx(seed, side='seller', status='cancelled')
                closed = _fresh_tx(
                    seed,
                    side='seller',
                    status='closed',
                    street_address='901 Stage St',
                )
                tx_ids = [cancelled.id, closed.id]
                contract = _primary_contract(seed, cancelled.id)
                _offer(seed, closed.id)
                db.session.commit()

                cancelled = Transaction.query.get(tx_ids[0])
                closed = Transaction.query.get(tx_ids[1])

                cancelled_stage = stage_for_transaction(
                    cancelled,
                    has_listing_agreement=True,
                    open_offers=3,
                    primary_contract=contract,
                )
                assert cancelled_stage.key == 'cancelled'
                assert cancelled_stage.is_terminal is True

                closed_stage = stage_for_transaction(
                    closed,
                    has_listing_agreement=True,
                    open_offers=3,
                    primary_contract=contract,
                )
                assert closed_stage.key == 'closed'
                assert closed_stage.is_terminal is True
            finally:
                for tx_id in tx_ids:
                    _cleanup_tx(seed['org_a'], tx_id)


class TestBuyerAndSimpleSides:
    def test_buyer_offers_and_under_contract(self, app, seed):
        with app.app_context():
            tx_ids = []
            try:
                offers_tx = _fresh_tx(
                    seed, side='buyer', status='showing',
                    street_address='910 Buyer Ave',
                )
                contract_tx = _fresh_tx(
                    seed, side='buyer', status='showing',
                    street_address='911 Buyer Ave',
                )
                tx_ids = [offers_tx.id, contract_tx.id]
                _offer(seed, offers_tx.id)
                contract = _primary_contract(
                    seed,
                    contract_tx.id,
                    closing_date=date.today() + timedelta(days=30),
                )
                db.session.commit()

                offers_tx = Transaction.query.get(tx_ids[0])
                contract_tx = Transaction.query.get(tx_ids[1])

                offer_stage = stage_for_transaction(
                    offers_tx, open_offers=1,
                )
                assert offer_stage.key == 'offers'
                assert offer_stage.side == 'buyer'

                uc_stage = stage_for_transaction(
                    contract_tx,
                    open_offers=1,
                    primary_contract=contract,
                    today=date.today(),
                )
                assert uc_stage.key == 'under_contract'
            finally:
                for tx_id in tx_ids:
                    _cleanup_tx(seed['org_a'], tx_id)

    def test_landlord_and_tenant_do_not_raise(self, app, seed):
        with app.app_context():
            tx_ids = []
            try:
                landlord = _fresh_tx(
                    seed, side='landlord', status='active',
                    street_address='920 Lease Ave',
                )
                tenant = _fresh_tx(
                    seed, side='tenant', status='showing',
                    street_address='921 Lease Ave',
                )
                tx_ids = [landlord.id, tenant.id]
                db.session.commit()

                landlord = Transaction.query.get(tx_ids[0])
                tenant = Transaction.query.get(tx_ids[1])

                landlord_stage = stage_for_transaction(landlord)
                tenant_stage = stage_for_transaction(tenant)
                assert landlord_stage.key == 'listed'
                assert tenant_stage.key == 'searching'
                assert landlord_stage.total == len(stage_keys_for_side('landlord'))
                assert tenant_stage.total == len(stage_keys_for_side('tenant'))
            finally:
                for tx_id in tx_ids:
                    _cleanup_tx(seed['org_a'], tx_id)


class TestSurfacesAndIndex:
    def test_relevant_surfaces_contract_and_offers(self):
        listed = stage_for_transaction(
            SimpleNamespace(
                status='active',
                transaction_type=SimpleNamespace(name='seller'),
                expected_close_date=None,
            ),
            has_listing_agreement=True,
        )
        under_contract = stage_for_transaction(
            SimpleNamespace(
                status='under_contract',
                transaction_type=SimpleNamespace(name='seller'),
                expected_close_date=None,
            ),
        )
        prelisting = stage_for_transaction(
            SimpleNamespace(
                status='active',
                transaction_type=SimpleNamespace(name='seller'),
                expected_close_date=None,
            ),
            has_listing_agreement=False,
        )

        assert relevant_surfaces(listed)['contract'] == 'hidden'
        assert surface_visibility(listed, 'contract') == 'hidden'
        assert relevant_surfaces(under_contract)['contract'] != 'hidden'
        assert surface_visibility(under_contract, 'contract') in (
            'primary', 'secondary',
        )
        assert relevant_surfaces(prelisting)['offers'] == 'hidden'
        assert surface_visibility(prelisting, 'unknown_surface') == 'secondary'

    @pytest.mark.parametrize('side,keys', [
        ('seller', SELLER_STAGE_KEYS),
        ('buyer', BUYER_STAGE_KEYS),
    ])
    def test_index_total_self_consistent(self, side, keys):
        for key in keys:
            if key in ('under_contract', 'closing', 'closed'):
                status = 'under_contract' if key != 'closed' else 'closed'
                tx = SimpleNamespace(
                    status=status,
                    transaction_type=SimpleNamespace(name=side),
                    expected_close_date=(
                        date.today() + timedelta(days=5)
                        if key == 'closing'
                        else date.today() + timedelta(days=40)
                    ),
                )
                kwargs = {}
                if key == 'closing':
                    kwargs['primary_contract'] = SimpleNamespace(
                        position='primary',
                        status='active',
                        closing_date=date.today() + timedelta(days=5),
                    )
                    kwargs['today'] = date.today()
                elif key == 'under_contract':
                    kwargs['primary_contract'] = SimpleNamespace(
                        position='primary',
                        status='active',
                        closing_date=date.today() + timedelta(days=40),
                    )
                    kwargs['today'] = date.today()
                stage = stage_for_transaction(tx, **kwargs)
            elif key == 'offers':
                stage = stage_for_transaction(
                    SimpleNamespace(
                        status='showing' if side == 'buyer' else 'active',
                        transaction_type=SimpleNamespace(name=side),
                        expected_close_date=None,
                    ),
                    has_listing_agreement=True,
                    open_offers=1,
                )
            elif key in ('listed', 'prelisting'):
                stage = stage_for_transaction(
                    SimpleNamespace(
                        status='active',
                        transaction_type=SimpleNamespace(name=side),
                        expected_close_date=None,
                    ),
                    has_listing_agreement=(key == 'listed'),
                )
            else:  # searching
                stage = stage_for_transaction(
                    SimpleNamespace(
                        status='showing',
                        transaction_type=SimpleNamespace(name=side),
                        expected_close_date=None,
                    ),
                    open_offers=0,
                )

            assert stage.key == key
            assert stage.total == len(keys)
            assert 0 <= stage.index < stage.total
            assert stage.index == keys.index(key)


def test_terminal_stage_lands_on_last_index_for_every_side():
    """A closed file must sit at the end of its own progress bar.

    Lease sides name their terminal stage 'leased', so routing them through the
    generic 'closed' key would fall back to index 0 and render "stage 1 of 4".
    """
    expected_terminal = {
        'seller': 'closed',
        'buyer': 'closed',
        'landlord': 'leased',
        'tenant': 'leased',
        'referral': 'closed',
    }
    for side, terminal_key in expected_terminal.items():
        stage = stage_for_transaction(
            SimpleNamespace(
                status='closed',
                transaction_type=SimpleNamespace(name=side),
                expected_close_date=None,
            ),
        )
        keys = stage_keys_for_side(side)
        assert stage.key == terminal_key, side
        assert stage.is_terminal is True, side
        assert stage.index == len(keys) - 1, side
