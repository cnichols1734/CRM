"""Contract terms merge behavior for transaction workspaces."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from services.transaction_helpers import build_contract_terms


def test_build_contract_terms_returns_none_without_data():
    transaction = SimpleNamespace(extra_data={})
    assert build_contract_terms(transaction, documents=[]) is None


def test_build_contract_terms_reads_bootstrapped_contract_document():
    transaction = SimpleNamespace(extra_data={})
    contract_doc = SimpleNamespace(
        template_slug='one-to-four-family-contract',
        field_data={
            'sales_price': '510000',
            'effective_date': '2026-08-01',
            'proposed_close_date': '2026-09-15',
            'option_fee': '250',
            'option_period_days': 10,
            'earnest_money': '5000',
            'financing_type': 'Conventional',
            'hoa_applicable': True,
        },
    )

    terms = build_contract_terms(transaction, documents=[contract_doc])

    assert terms is not None
    assert terms['sales_price'] == '$510,000'
    assert terms['effective_date'] == 'August 01, 2026'
    assert terms['closing_date'] == 'September 15, 2026'
    assert terms['option_fee'] == '$250'
    assert terms['option_period_days'] == '10'
    assert terms['earnest_money'] == '$5,000'
    assert terms['financing_type'] == 'Conventional'
    assert terms['has_hoa'] == 'Yes'
    assert terms['_sources']['sales_price'] == 'contract_document'
    assert terms['_completeness']['filled'] >= 7


def test_accepted_contract_takes_precedence_over_document():
    transaction = SimpleNamespace(extra_data={})
    contract_doc = SimpleNamespace(
        template_slug='seller-accepted-contract',
        field_data={
            'sales_price': '500000',
            'closing_date': '2026-09-01',
            'financing_type': 'FHA',
        },
    )
    accepted = SimpleNamespace(
        accepted_price=Decimal('525000.00'),
        effective_date=date(2026, 8, 5),
        closing_date=date(2026, 9, 30),
        option_period_days=7,
        financing_type='Conventional',
        title_company='Title Pros',
        escrow_officer='Jane Escrow',
        survey_choice='Seller existing survey',
        survey_furnished_by=None,
        hoa_applicable=False,
        buyer_agent_commission_percent=Decimal('2.5'),
        buyer_agent_commission_flat=None,
        frozen_terms={},
        extra_data={},
    )

    terms = build_contract_terms(
        transaction,
        accepted_contract=accepted,
        documents=[contract_doc],
    )

    assert terms['sales_price'] == '$525,000'
    assert terms['closing_date'] == 'September 30, 2026'
    assert terms['financing_type'] == 'Conventional'
    assert terms['has_hoa'] == 'No'
    assert terms['buyer_commission'] == '2.5%'
    assert terms['_sources']['sales_price'] == 'accepted_contract'
    assert terms['_sources']['financing_type'] == 'accepted_contract'


def test_overrides_take_precedence_over_both():
    transaction = SimpleNamespace(extra_data={
        'contract_terms_overrides': {
            'sales_price': '540000',
            'title_company': 'Override Title',
        },
    })
    contract_doc = SimpleNamespace(
        template_slug='purchase-contract',
        field_data={'sales_price': '500000', 'title_company': 'Doc Title'},
    )
    accepted = SimpleNamespace(
        accepted_price=Decimal('525000.00'),
        effective_date=None,
        closing_date=None,
        option_period_days=None,
        financing_type=None,
        title_company='Accepted Title',
        escrow_officer=None,
        survey_choice=None,
        survey_furnished_by=None,
        hoa_applicable=None,
        buyer_agent_commission_percent=None,
        buyer_agent_commission_flat=None,
        frozen_terms={},
        extra_data={},
    )

    terms = build_contract_terms(
        transaction,
        accepted_contract=accepted,
        documents=[contract_doc],
    )

    assert terms['sales_price'] == '$540,000'
    assert terms['title_company'] == 'Override Title'
    assert terms['_sources']['sales_price'] == 'override'
    assert terms['_sources']['title_company'] == 'override'


def test_contract_terms_completeness_counts_correctly():
    transaction = SimpleNamespace(extra_data={})
    contract_doc = SimpleNamespace(
        template_slug='purchase-contract',
        field_data={
            'sales_price': '400000',
            'effective_date': '2026-07-01',
            'closing_date': '2026-08-01',
        },
    )

    terms = build_contract_terms(transaction, documents=[contract_doc])

    assert terms['_completeness']['filled'] == 3
    assert terms['_completeness']['total'] == 12
    assert 'option_fee' in terms['_completeness']['missing']
    assert 'sales_price' not in terms['_completeness']['missing']
