"""Expected-document descriptor service tests."""

from services.document_identity import (
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
)
from services.expected_documents import (
    APPLICABLE,
    LEAD_PAINT_SLUG,
    NOT_APPLICABLE,
    OPTIONAL,
    POST_EXECUTION,
    UNKNOWN,
    expected_documents_for_context,
    merge_listing_package_terms,
)


def test_listing_scope_hoa_and_lead_key_presence():
    docs = expected_documents_for_context(
        scope='listing',
        terms={'has_hoa': 'yes', 'built_before_1978': False},
    )
    by_key = {d.key: d for d in docs}
    assert by_key['hoa_addendum'].applicability == APPLICABLE
    # Explicit False must not be lost by `or` chaining.
    assert by_key['lead_based_paint'].applicability == NOT_APPLICABLE
    assert by_key['lead_based_paint'].template_slug == LEAD_PAINT_SLUG
    assert LEAD_PAINT_SLUG == 'lead-paint'


def test_seller_disclosure_not_universally_applicable():
    docs = expected_documents_for_context(scope='listing', terms={})
    disclosure = next(d for d in docs if d.key == 'sellers_disclosure')
    assert disclosure.applicability == UNKNOWN

    docs_req = expected_documents_for_context(
        scope='listing',
        terms={'seller_disclosure_required': True},
    )
    assert next(d for d in docs_req if d.key == 'sellers_disclosure').applicability == APPLICABLE


def test_merge_intake_resolves_texas_listing_applicability():
    """Questionnaire answers must drive lead paint + conventional disclosure."""
    terms = merge_listing_package_terms(
        listing_field_data={'has_hoa': True},
        intake_data={
            'built_before_1978': False,
            'has_hoa': True,
            'flood_hazard': True,
            'has_survey': 'yes',
        },
        ownership_status='conventional',
    )
    assert terms['built_before_1978'] is False
    assert terms['seller_disclosure_required'] is True

    docs = expected_documents_for_context(scope='listing', terms=terms)
    by_key = {d.key: d for d in docs}
    assert by_key['sellers_disclosure'].applicability == APPLICABLE
    assert by_key['lead_based_paint'].applicability == NOT_APPLICABLE
    assert by_key['hoa_addendum'].applicability == APPLICABLE


def test_reo_intake_does_not_force_seller_disclosure():
    terms = merge_listing_package_terms(
        intake_data={'built_before_1978': True, 'has_hoa': False},
        ownership_status='reo',
    )
    assert 'seller_disclosure_required' not in terms
    docs = expected_documents_for_context(scope='listing', terms=terms)
    disclosure = next(d for d in docs if d.key == 'sellers_disclosure')
    assert disclosure.applicability == UNKNOWN


def test_listing_hoa_not_applicable_when_explicitly_no():
    docs = expected_documents_for_context(
        scope='listing',
        terms={'has_hoa': 'no'},
    )
    hoa = next(d for d in docs if d.key == 'hoa_addendum')
    assert hoa.applicability == NOT_APPLICABLE


def test_offer_scope_financing_and_backup():
    docs = expected_documents_for_context(
        scope='offer',
        terms={
            'financing_type': 'conventional',
            'hoa_applicable': True,
            'is_backup': True,
        },
        has_controlling_contract=False,
    )
    by_key = {d.key: d for d in docs}
    assert by_key['third_party_financing'].applicability == APPLICABLE
    assert by_key['hoa_addendum'].applicability == APPLICABLE
    assert by_key['backup_addendum'].applicability == APPLICABLE
    assert by_key['amendment'].applicability == NOT_APPLICABLE
    assert by_key['pre_approval_or_pof'].applicability == OPTIONAL


def test_cash_offer_keeps_pof_optional():
    docs = expected_documents_for_context(
        scope='offer',
        terms={'financing_type': 'cash'},
        has_controlling_contract=False,
    )
    by_key = {d.key: d for d in docs}
    assert by_key['third_party_financing'].applicability == NOT_APPLICABLE
    assert by_key['pre_approval_or_pof'].applicability == OPTIONAL
    assert 'proof of funds' in by_key['pre_approval_or_pof'].reason.lower()


def test_purchase_form_number_from_identity_not_hardcoded_trec_20():
    condo = DocumentIdentity(
        kind=KIND_PURCHASE_CONTRACT,
        template_slug='condominium-contract',
        form_number='TREC 30',
        label='Residential Condominium Contract',
        confidence=0.92,
    )
    docs = expected_documents_for_context(
        scope='contract',
        terms={},
        identities=[condo],
        has_controlling_contract=True,
    )
    purchase = next(d for d in docs if d.key == 'purchase_contract')
    assert purchase.form_number == 'TREC 30'
    assert purchase.template_slug == 'condominium-contract'
    assert next(d for d in docs if d.key == 'amendment').applicability == POST_EXECUTION


def test_explicit_false_hoa_not_overwritten_by_missing_has_hoa():
    docs = expected_documents_for_context(
        scope='contract',
        terms={'hoa_applicable': False, 'has_hoa': 'yes'},
    )
    # hoa_applicable is first-present key and wins.
    hoa = next(d for d in docs if d.key == 'hoa_addendum')
    assert hoa.applicability == NOT_APPLICABLE


def test_unknown_when_terms_missing():
    docs = expected_documents_for_context(scope='offer', terms={})
    by_key = {d.key: d for d in docs}
    assert by_key['hoa_addendum'].applicability == UNKNOWN
    assert 'not shown' in by_key['hoa_addendum'].reason.lower() or 'unknown' in by_key['hoa_addendum'].reason.lower()
