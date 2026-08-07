"""Read-only seller net-sheet estimator."""

from __future__ import annotations

import json
from decimal import Decimal

from models import SellerCommissionTerms, SellerOffer, SellerOfferVersion, Transaction, db
from services.net_sheet import build_for_offer, build_for_offers


def _tx(seed, **kwargs):
    """Dedicated transaction so session-scoped seed['tx_a'] is not polluted."""
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=seed['tx_type_a'],
        street_address=kwargs.pop('street_address', '900 Net Sheet Ln'),
        city=kwargs.pop('city', 'Austin'),
        state=kwargs.pop('state', 'TX'),
        status=kwargs.pop('status', 'active'),
        **kwargs,
    )
    db.session.add(tx)
    db.session.flush()
    return tx


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


def _clear_commission_terms(org_id, tx_id):
    """Wipe per-tx terms so tests stay isolated on the session DB."""
    SellerCommissionTerms.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
    ).delete()
    db.session.flush()


def _commission_terms(org_id, tx_id, user_id, **kwargs):
    _clear_commission_terms(org_id, tx_id)
    terms = SellerCommissionTerms(
        organization_id=org_id,
        transaction_id=tx_id,
        created_by_id=user_id,
        **kwargs,
    )
    db.session.add(terms)
    db.session.flush()
    return terms


def _line_map(sheet):
    return {line.key: line for line in sheet.lines}


def test_full_happy_path_amounts(app, seed):
    """
    Hand-computed expected net (do not re-derive via net_sheet):

      Sales price                          +721,500.00
      Listing commission 3%                 -21,645.00   (0.03 × 721500)
      Buyer-agent coop 3%                   -21,645.00   (0.03 × 721500)
      Seller concessions                     -4,000.00
      Option fee (credit)                      +300.00
      ────────────────────────────────────────────────
      Estimated net                         674,510.00

      721500 + 300 − 21645 − 21645 − 4000 = 674510
    """
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        offer = _offer(
            org_id, tx.id, user_id,
            offer_price=Decimal('721500'),
            seller_concessions_amount=Decimal('4000'),
            option_fee=Decimal('300'),
        )
        terms = _commission_terms(
            org_id, tx.id, user_id,
            listing_commission_percent=Decimal('3'),
            coop_compensation_percent=Decimal('3'),
        )
        db.session.commit()

        sheet = build_for_offer(offer, commission_terms=terms)
        lines = _line_map(sheet)

        assert lines['sales_price'].amount == Decimal('721500.00')
        assert lines['sales_price'].kind == 'credit'
        assert lines['sales_price'].known is True

        assert lines['listing_commission'].amount == Decimal('21645.00')
        assert lines['listing_commission'].kind == 'cost'
        assert lines['listing_commission'].basis == '3% of $721,500'

        assert lines['buyer_agent_commission'].amount == Decimal('21645.00')
        assert lines['buyer_agent_commission'].kind == 'cost'
        assert 'listing coop' in (lines['buyer_agent_commission'].basis or '').lower()

        assert lines['seller_concessions'].amount == Decimal('4000.00')
        assert lines['option_fee'].amount == Decimal('300.00')
        assert lines['option_fee'].kind == 'credit'

        # Literal expected net — computed by hand above, not via code under test.
        assert sheet.estimated_net == Decimal('674510.00')
        assert lines['estimated_net'].amount == Decimal('674510.00')
        assert lines['estimated_net'].kind == 'total'
        assert sheet.total_known_costs == Decimal('47290.00')


def test_offer_buyer_agent_commission_overrides_coop(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        offer = _offer(
            org_id, tx.id, user_id,
            offer_price=Decimal('721500'),
            buyer_agent_commission_percent=Decimal('2.5'),
        )
        terms = _commission_terms(
            org_id, tx.id, user_id,
            listing_commission_percent=Decimal('3'),
            coop_compensation_percent=Decimal('3'),
        )
        db.session.commit()

        sheet = build_for_offer(offer, commission_terms=terms)
        line = _line_map(sheet)['buyer_agent_commission']

        assert line.amount == Decimal('18037.50')  # 2.5% of 721500
        assert line.known is True
        basis = line.basis or ''
        assert '2.5%' in basis
        assert 'offer' in basis.lower()
        assert 'coop' not in basis.lower() or 'offer' in basis.lower()


def test_no_commission_terms_marks_commission_lines_unknown(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        _clear_commission_terms(org_id, tx.id)
        offer = _offer(
            org_id, tx.id, user_id,
            offer_price=Decimal('500000'),
            seller_concessions_amount=Decimal('1000'),
            option_fee=Decimal('200'),
        )
        db.session.commit()

        sheet = build_for_offer(offer)
        lines = _line_map(sheet)

        for key in (
            'listing_commission',
            'buyer_agent_commission',
            'bonus',
            'referral_fee',
            'admin_transaction_fee',
        ):
            assert lines[key].known is False
            assert lines[key].amount is None
            assert key in sheet.unknown_keys

        # Known lines still contribute: 500000 − 1000 + 200 = 499200
        assert sheet.estimated_net == Decimal('499200.00')


def test_missing_sales_price_returns_sheet_with_null_net(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        _clear_commission_terms(org_id, tx.id)
        offer = _offer(org_id, tx.id, user_id, offer_price=None)
        db.session.commit()

        sheet = build_for_offer(offer)
        assert sheet.sales_price is None
        assert sheet.estimated_net is None
        assert _line_map(sheet)['sales_price'].known is False
        assert 'sales_price' in sheet.unknown_keys


def test_title_and_closing_costs_always_unknown(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        offer = _offer(
            org_id, tx.id, user_id,
            offer_price=Decimal('721500'),
            seller_concessions_amount=Decimal('4000'),
            option_fee=Decimal('300'),
        )
        terms = _commission_terms(
            org_id, tx.id, user_id,
            listing_commission_percent=Decimal('3'),
            coop_compensation_percent=Decimal('3'),
            bonus_amount=Decimal('1000'),
            referral_fee_flat=Decimal('500'),
            admin_transaction_fee=Decimal('250'),
        )
        db.session.commit()

        sheet = build_for_offer(offer, commission_terms=terms)
        title = _line_map(sheet)['title_and_closing_costs']
        assert title.known is False
        assert title.amount is None
        assert 'title_and_closing_costs' in sheet.unknown_keys
        assert 'title company' in (title.basis or '').lower()


def test_loan_payoff_applied_when_provided(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        _clear_commission_terms(org_id, tx.id)
        offer = _offer(
            org_id, tx.id, user_id,
            offer_price=Decimal('500000'),
            seller_concessions_amount=Decimal('0'),
            option_fee=Decimal('0'),
        )
        db.session.commit()

        without = build_for_offer(offer)
        assert 'loan_payoff' in without.unknown_keys
        assert _line_map(without)['loan_payoff'].known is False

        with_payoff = build_for_offer(offer, loan_payoff=Decimal('200000'))
        payoff_line = _line_map(with_payoff)['loan_payoff']
        assert payoff_line.known is True
        assert payoff_line.amount == Decimal('200000.00')
        assert payoff_line.kind == 'cost'
        assert 'loan_payoff' not in with_payoff.unknown_keys
        # 500000 − 200000 = 300000 (zero concessions/option are known zeros)
        assert with_payoff.estimated_net == Decimal('300000.00')


def test_flat_fee_commission_path(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        offer = _offer(
            org_id, tx.id, user_id,
            offer_price=Decimal('721500'),
            seller_concessions_amount=Decimal('4000'),
            option_fee=Decimal('300'),
        )
        terms = _commission_terms(
            org_id, tx.id, user_id,
            listing_commission_flat=Decimal('10000'),
            coop_compensation_flat=Decimal('8000'),
        )
        db.session.commit()

        sheet = build_for_offer(offer, commission_terms=terms)
        lines = _line_map(sheet)

        assert lines['listing_commission'].amount == Decimal('10000.00')
        assert lines['listing_commission'].basis == 'Flat fee'
        assert lines['listing_commission'].known is True

        assert lines['buyer_agent_commission'].amount == Decimal('8000.00')
        assert 'Flat fee' in (lines['buyer_agent_commission'].basis or '')
        assert lines['buyer_agent_commission'].known is True

        # Same shape as percent path: all twelve line keys, known net.
        assert [line.key for line in sheet.lines] == [
            'sales_price',
            'listing_commission',
            'buyer_agent_commission',
            'seller_concessions',
            'residential_service_contract',
            'bonus',
            'referral_fee',
            'admin_transaction_fee',
            'option_fee',
            'loan_payoff',
            'title_and_closing_costs',
            'estimated_net',
        ]
        # 721500 − 10000 − 8000 − 4000 + 300 = 699800
        assert sheet.estimated_net == Decimal('699800.00')


def test_as_dict_serializes_money_as_strings(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        _clear_commission_terms(org_id, tx.id)
        offer = _offer(
            org_id, tx.id, user_id,
            offer_price=Decimal('721500.00'),
            option_fee=Decimal('300.00'),
        )
        db.session.commit()

        sheet = build_for_offer(offer)
        payload = sheet.as_dict()

        assert isinstance(payload['sales_price'], str)
        assert payload['sales_price'] == '721500.00'
        sales_line = next(line for line in payload['lines'] if line['key'] == 'sales_price')
        assert isinstance(sales_line['amount'], str)
        assert sales_line['amount'] == '721500.00'

        # Must round-trip through json without TypeError
        encoded = json.dumps(payload)
        assert '721500.00' in encoded


def test_zero_concession_is_known(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        _clear_commission_terms(org_id, tx.id)
        offer = _offer(
            org_id, tx.id, user_id,
            offer_price=Decimal('400000'),
            seller_concessions_amount=Decimal('0'),
        )
        db.session.commit()

        sheet = build_for_offer(offer)
        concessions = _line_map(sheet)['seller_concessions']
        assert concessions.known is True
        assert concessions.amount == Decimal('0.00')
        assert 'seller_concessions' not in sheet.unknown_keys


def test_build_for_offers_preserves_input_order(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        tx = _tx(seed)
        user_id = seed['owner_a']

        _clear_commission_terms(org_id, tx.id)
        first = _offer(
            org_id, tx.id, user_id,
            buyer_names='First',
            offer_price=Decimal('400000'),
        )
        second = _offer(
            org_id, tx.id, user_id,
            buyer_names='Second',
            offer_price=Decimal('450000'),
        )
        third = _offer(
            org_id, tx.id, user_id,
            buyer_names='Third',
            offer_price=Decimal('500000'),
        )
        db.session.commit()

        sheets = build_for_offers([first, second, third])
        assert len(sheets) == 3
        assert [s.offer_id for s in sheets] == [first.id, second.id, third.id]
        assert sheets[0].sales_price == Decimal('400000.00')
        assert sheets[1].sales_price == Decimal('450000.00')
        assert sheets[2].sales_price == Decimal('500000.00')
