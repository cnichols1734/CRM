"""Listing-info merge behavior for seller transaction workspaces."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from services.transaction_helpers import build_listing_info, resolve_header_price_display


def test_header_price_falls_back_to_listing_list_price():
    transaction = SimpleNamespace(extra_data={})
    listing_info = {'list_price': '$485,000'}
    assert resolve_header_price_display(transaction, listing_info=listing_info) == '$485,000'


def test_header_price_prefers_sales_price_over_list_price():
    transaction = SimpleNamespace(extra_data={'sales_price': '510000', 'list_price': '485000'})
    listing_info = {'list_price': '$485,000'}
    assert resolve_header_price_display(
        transaction,
        listing_info=listing_info,
    ) == '$510,000'


def test_header_price_uses_contract_terms_when_extra_empty():
    transaction = SimpleNamespace(extra_data={})
    assert resolve_header_price_display(
        transaction,
        contract_terms={'sales_price': '$500,000'},
        listing_info={'list_price': '$485,000'},
    ) == '$500,000'


def test_listing_info_merges_profile_questionnaire_extraction_and_overrides():
    transaction = SimpleNamespace(intake_data={'has_hoa': True})
    profile = SimpleNamespace(
        current_list_price=Decimal('425000.00'),
        go_live_date=date(2026, 8, 10),
    )
    listing_document = SimpleNamespace(
        template_slug='listing-agreement',
        field_data={
            'listing_start_date': '2026-08-08',
            'listing_end_date': '2027-02-08',
            'total_commission': '5',
            'buyer_agent_percent': '2.5',
        },
    )

    info = build_listing_info(
        [listing_document],
        {'list_price': '430000'},
        transaction=transaction,
        listing_profile=profile,
    )

    assert info['list_price'] == '$430,000'
    assert info['go_live_date'] == 'August 10, 2026'
    assert info['listing_start_date'] == 'August 08, 2026'
    assert info['listing_end_date'] == 'February 08, 2027'
    assert info['has_hoa'] == 'Yes'
    assert info['total_commission'] == '5%'
    assert info['listing_side_commission'] == '2.5%'


def test_listing_info_never_uses_contract_sales_price_as_list_price():
    transaction = SimpleNamespace(intake_data={})
    contract = SimpleNamespace(
        template_slug='seller-accepted-contract',
        field_data={'sales_price': '510000'},
    )

    assert build_listing_info([contract], transaction=transaction) is None


def test_listing_info_completeness_reports_partial():
    transaction = SimpleNamespace(intake_data={})
    listing_document = SimpleNamespace(
        template_slug='listing-agreement',
        field_data={
            'list_price': '335000',
            'listing_start_date': '2026-08-08',
            'total_commission': '6',
            'buyer_agent_percent': '3',
        },
    )

    info = build_listing_info([listing_document], transaction=transaction)

    assert info['_completeness']['filled'] > 0
    assert info['_completeness']['filled'] < info['_completeness']['total']
    assert info['_completeness']['total'] == 10
    assert 'listing_end_date' in info['_completeness']['missing']
    assert 'list_price' not in info['_completeness']['missing']


def test_listing_info_sources_mark_intake_and_override():
    transaction = SimpleNamespace(intake_data={'has_hoa': True})
    listing_document = SimpleNamespace(
        template_slug='listing-agreement',
        field_data={
            'list_price': '300000',
            'listing_start_date': '2026-08-01',
        },
    )

    info = build_listing_info(
        [listing_document],
        {'list_price': '310000'},
        transaction=transaction,
    )

    assert info['_sources']['has_hoa'] == 'intake'
    assert info['_sources']['list_price'] == 'override'
    assert info['_sources']['listing_start_date'] == 'listing_agreement'


def test_listing_info_hybrid_5a1b_flat_plus_buyer_percent():
    """5A(1)(b) free text like '$8,000 + 2% to Buyer\'s Broker' must show listing $."""
    transaction = SimpleNamespace(intake_data={})
    listing_document = SimpleNamespace(
        template_slug='listing-agreement',
        field_data={
            'list_price': '485000',
            'listing_start_date': '2026-01-18',
            'listing_end_date': '2026-07-31',
            'broker_fee_section': '5a',
            'broker_fee_5a_choice': 'other',
            'broker_fee_raw_text': "$8,000 + 2% to a Buyer's Broker",
            'total_commission': None,
            'total_commission_display': '$8,000 + 2%',
            'listing_side_flat': '8000',
            'listing_side_percent': None,
            'buyer_agent_percent': '2',
            'buyer_agent_flat': None,
            'protection_period_days': '180',
        },
    )

    info = build_listing_info([listing_document], transaction=transaction)

    assert info['listing_side_commission'] == '$8,000'
    assert info['buyer_commission'] == '2%'
    assert info['total_commission'] == '$8,000 + 2%'
    assert info['commission_type'] == '5a'


def test_listing_info_prefers_explicit_listing_side_percent():
    transaction = SimpleNamespace(intake_data={})
    listing_document = SimpleNamespace(
        template_slug='listing-agreement',
        field_data={
            'total_commission': '6',
            'listing_side_percent': '3.5',
            'buyer_agent_percent': '2.5',
        },
    )

    info = build_listing_info([listing_document], transaction=transaction)

    assert info['total_commission'] == '6%'
    assert info['listing_side_commission'] == '3.5%'
    assert info['buyer_commission'] == '2.5%'


def test_sync_seller_commission_terms_from_hybrid_listing(app, seed):
    from decimal import Decimal

    from models import SellerCommissionTerms, Transaction, db
    from services.transaction_helpers import sync_seller_commission_terms_from_listing

    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        terms = sync_seller_commission_terms_from_listing(
            transaction=tx,
            field_data={
                'broker_fee_raw_text': "$8,000 + 2% to a Buyer's Broker",
                'listing_side_flat': '8000',
                'buyer_agent_percent': '2',
                'total_commission_display': '$8,000 + 2%',
            },
            user_id=seed['owner_a'],
            org_id=seed['org_a'],
        )
        db.session.flush()
        assert terms is not None
        assert terms.listing_commission_flat == Decimal('8000')
        assert terms.coop_compensation_percent == Decimal('2')
        assert terms.listing_commission_percent is None
        assert terms.source == 'listing_agreement_extraction'
        assert '8,000' in (terms.notes or '')
        SellerCommissionTerms.query.filter_by(id=terms.id).delete()
        db.session.commit()
