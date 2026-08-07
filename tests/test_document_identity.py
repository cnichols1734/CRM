"""Unit tests for deterministic document identity (no OpenAI)."""

from services.document_identity import (
    EXEC_DRAFT,
    EXEC_EXECUTED,
    EXEC_PARTY_SIGNED,
    EXEC_UNKNOWN,
    HIGH_CONFIDENCE,
    KIND_ADDENDUM,
    KIND_AMENDMENT,
    KIND_DISCLOSURE,
    KIND_LISTING_AGREEMENT,
    KIND_OTHER,
    KIND_PROOF_OF_FUNDS,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
    apply_ai_package_authority,
    identify_from_text,
    persist_identity_on_field_data,
    refresh_execution_state,
    resolve_upload_identity_for_extraction,
    should_content_classify_slug,
)


TXR_1101_TEXT = """
TEXAS REALTORS
RESIDENTIAL REAL ESTATE LISTING AGREEMENT EXCLUSIVE RIGHT TO SELL
TXR-1101
1. PARTIES: The parties to this listing are:
Seller: Jamie Harrington
"""

TREC_20_TEXT = """
PROMULGATED BY THE TEXAS REAL ESTATE COMMISSION (TREC)
ONE TO FOUR FAMILY RESIDENTIAL CONTRACT (RESALE)
TREC NO. 20-18
1. PARTIES: The parties to this contract are
"""

TREC_20_PACKAGE_TEXT = """
PROMULGATED BY THE TEXAS REAL ESTATE COMMISSION (TREC)
ONE TO FOUR FAMILY RESIDENTIAL CONTRACT (RESALE)
TREC NO. 20-18
1. PARTIES: The parties to this contract are

THIRD PARTY FINANCING ADDENDUM
TREC NO. 40-9

ADDENDUM FOR PROPERTY SUBJECT TO MANDATORY MEMBERSHIP
IN A PROPERTY OWNERS ASSOCIATION
TREC NO. 36-9
"""

TREC_39_TEXT = """
PROMULGATED BY THE TEXAS REAL ESTATE COMMISSION (TREC)
AMENDMENT TO CONTRACT
TREC NO. 39-9
"""

TREC_40_TEXT = """
THIRD PARTY FINANCING ADDENDUM
TREC NO. 40-9
"""


def test_identifies_txr_1101_listing_agreement():
    identity = identify_from_text(TXR_1101_TEXT, filename='listing.pdf')
    assert identity.kind == KIND_LISTING_AGREEMENT
    assert identity.form_number == 'TXR-1101'
    assert identity.template_slug == 'listing-agreement'
    assert identity.is_high_confidence
    assert 'listing' in identity.possible_scopes


def test_listing_addenda_checklist_is_not_embedded_package():
    """Section 19 names lead paint / disclosure — that is not a multi-doc package."""
    text = TXR_1101_TEXT + """
19. ADDENDA AND OTHER DOCUMENTS:
X A. Information About Brokerage Services;
❑ B. Seller Disclosure Notice (§5.008, Texas Property Code);
❑ C. Addendum for Seller's Disclosure of Information on Lead-Based Paint
   and Lead-Based Paint Hazards (required if Property was built before 1978);
❑ K. Information about Special Flood Hazard Areas;
21. ADDITIONAL NOTICES:
H. If the Property was built before 1978, Federal law requires the Seller to
provide the federally approved pamphlet on lead poisoning prevention.
"""
    identity = identify_from_text(text, filename='TXR-1101-listing.pdf')
    assert identity.kind == KIND_LISTING_AGREEMENT
    assert identity.extras.get('embedded_components') == []


def test_identifies_trec_20_purchase_contract():
    identity = identify_from_text(TREC_20_TEXT, filename='offer.pdf')
    assert identity.kind == KIND_PURCHASE_CONTRACT
    assert identity.form_number == 'TREC 20'
    assert identity.purchase_contract_type == 'resale_one_to_four'
    assert identity.confidence >= HIGH_CONFIDENCE
    assert identity.ambiguous is False


def test_contract_package_with_addenda_not_ambiguous():
    """TREC 20 + embedded TREC 36/40 is still a purchase-contract package."""
    identity = identify_from_text(TREC_20_PACKAGE_TEXT, filename='offer-package.pdf')
    assert identity.kind == KIND_PURCHASE_CONTRACT
    assert identity.form_number == 'TREC 20'
    assert identity.ambiguous is False
    assert identity.is_high_confidence
    embedded = identity.extras.get('embedded_components') or []
    kinds = {c['kind'] for c in embedded}
    slugs = {c.get('template_slug') for c in embedded}
    assert KIND_ADDENDUM in kinds
    assert 'third-party-financing-addendum' in slugs
    assert 'hoa-addendum' in slugs
    forms = {c.get('form_number') for c in embedded}
    assert 'TREC 40' in forms
    assert 'TREC 36' in forms


def test_identifies_amendment_and_addenda():
    amendment = identify_from_text(TREC_39_TEXT)
    assert amendment.kind == KIND_AMENDMENT
    assert amendment.template_slug == 'amendment'

    tpf = identify_from_text(TREC_40_TEXT)
    assert tpf.kind == KIND_ADDENDUM
    assert tpf.addendum_key == 'third_party_financing'

    disclosure = identify_from_text("Seller's Disclosure Notice TXR-1406")
    assert disclosure.kind == KIND_DISCLOSURE

    pof = identify_from_text('Mortgage Pre-Approval Letter', filename='preapproval.pdf')
    assert pof.kind == KIND_PROOF_OF_FUNDS


def test_listing_support_docs_are_not_offer_contracts():
    """Wire fraud / IABS / T-47 / net sheets must never become Offer Contract."""
    cases = [
        (
            'INFORMATION ABOUT BROKERAGE SERVICES\nTXR-2501\nIABS',
            'IABS.pdf',
            'iabs',
            KIND_DISCLOSURE,
        ),
        (
            'WIRE FRAUD WARNING\nTXR-2517\nDo not wire funds without calling',
            'Wire-Fraud-Warning.pdf',
            'wire-fraud-warning',
            KIND_DISCLOSURE,
        ),
        (
            'RESIDENTIAL REAL PROPERTY AFFIDAVIT\nT-47.1\nDeclaration in Lieu of Affidavit',
            'T-47.pdf',
            't47-affidavit',
            KIND_DISCLOSURE,
        ),
        (
            "Seller's Estimated Net Proceeds\nTXR-2001\nEstimated net proceeds to seller",
            'Seller-Net-Proceeds.pdf',
            'seller-net-proceeds',
            KIND_OTHER,
        ),
    ]
    for text, filename, slug, kind in cases:
        identity = identify_from_text(text, filename=filename)
        assert identity.kind == kind, filename
        assert identity.template_slug == slug, filename
        assert identity.kind != KIND_PURCHASE_CONTRACT, filename
        assert 'offer' not in (identity.label or '').lower(), filename
        assert not any(
            s.startswith('offer_classifier:buyer_offer')
            for s in (identity.matched_signals or ())
        ), filename


def test_generic_pdf_without_contract_text_is_not_buyer_offer():
    identity = identify_from_text(
        'This notice concerns wiring instructions for closing.',
        filename='random-notice.pdf',
    )
    assert identity.kind != KIND_PURCHASE_CONTRACT
    assert identity.offer_document_type != 'buyer_offer'


def test_execution_state_from_signature_hints_only():
    executed = identify_from_text(
        TREC_20_TEXT,
        field_hints={
            'buyer_signature_present': True,
            'seller_signature_present': True,
        },
    )
    assert executed.execution_state == EXEC_EXECUTED

    party = identify_from_text(
        TREC_20_TEXT,
        field_hints={
            'buyer_signature_present': True,
            'seller_signature_present': False,
        },
    )
    assert party.execution_state == EXEC_PARTY_SIGNED

    draft = identify_from_text(
        TREC_20_TEXT,
        field_hints={
            'buyer_signature_present': False,
            'seller_signature_present': False,
        },
    )
    assert draft.execution_state == EXEC_DRAFT

    # Boilerplate "executed" / "effective date" language must not imply execution.
    blankish = identify_from_text(
        TREC_20_TEXT + '\nEffective Date of this contract\nfully executed\n',
        field_hints={},
    )
    assert blankish.execution_state == EXEC_UNKNOWN


def test_refresh_execution_preserves_content_identity():
    base = identify_from_text(TREC_20_PACKAGE_TEXT)
    assert base.kind == KIND_PURCHASE_CONTRACT
    assert base.ambiguous is False
    refreshed = refresh_execution_state(
        base,
        field_hints={
            'buyer_signature_present': True,
            'seller_signature_present': True,
        },
    )
    assert refreshed.kind == KIND_PURCHASE_CONTRACT
    assert refreshed.form_number == 'TREC 20'
    assert refreshed.ambiguous is False
    assert refreshed.execution_state == EXEC_EXECUTED
    assert refreshed.extras.get('embedded_components')


def test_filename_txr_1101_helps_when_text_thin():
    identity = identify_from_text(
        '',
        filename='TXR-1101-Filled-Test-Listing-Harrington.pdf',
    )
    assert identity.kind == KIND_LISTING_AGREEMENT
    assert identity.confidence >= 0.65


def test_generic_slug_retag_listing_on_seller():
    assert should_content_classify_slug('completed')
    assert should_content_classify_slug('custom-abc123-def')
    assert should_content_classify_slug('custom_abc123')
    assert not should_content_classify_slug('listing-agreement')

    slug, identity, retagged = resolve_upload_identity_for_extraction(
        template_slug='custom-550e8400-e29b-41d4-a716-446655440000',
        file_bytes=b'%PDF-1.4',
        filename='TXR-1101-listing.pdf',
        transaction_side='seller',
        is_offer_scoped=False,
    )
    assert identity.kind == KIND_LISTING_AGREEMENT
    if identity.is_high_confidence:
        assert slug == 'listing-agreement'
        assert retagged is True


def test_offer_scoped_upload_does_not_retag_away_from_offer():
    slug, _identity, retagged = resolve_upload_identity_for_extraction(
        template_slug='seller-offer-contract',
        file_bytes=b'%PDF-1.4',
        filename='TREC-20-offer.pdf',
        transaction_side='seller',
        is_offer_scoped=True,
    )
    assert slug == 'seller-offer-contract'
    assert retagged is False


def test_offer_scoped_generic_slug_uses_content_schema():
    """Generic completed uploads on an offer still need TREC/TPF schemas."""
    from pathlib import Path

    offer_docs = Path(__file__).resolve().parents[1] / 'offer_docs'
    contract = offer_docs / (
        'One to Four Family Residential Contract (Resale) (TXR 1601  TREC 20-18).pdf'
    )
    tpf = offer_docs / (
        'Third Party Financing Addendum For Credit Approval (TXR 1901  TREC 40-11).pdf'
    )
    comp = offer_docs / 'Compensation Agreement Between Brokers - Buy  Sell (TXR 2402).pdf'
    if not contract.exists():
        return

    slug, identity, retagged = resolve_upload_identity_for_extraction(
        template_slug='completed',
        file_bytes=contract.read_bytes(),
        filename=contract.name,
        transaction_side='seller',
        is_offer_scoped=True,
    )
    assert identity.kind == 'purchase_contract'
    assert slug == 'one-to-four-family-contract'
    assert retagged is True

    if tpf.exists():
        slug, identity, retagged = resolve_upload_identity_for_extraction(
            template_slug='completed',
            file_bytes=tpf.read_bytes(),
            filename=tpf.name,
            transaction_side='seller',
            is_offer_scoped=True,
        )
        assert identity.template_slug == 'third-party-financing-addendum'
        assert slug == 'third-party-financing-addendum'
        assert retagged is True

    if comp.exists():
        slug, identity, retagged = resolve_upload_identity_for_extraction(
            template_slug='completed',
            file_bytes=comp.read_bytes(),
            filename=comp.name,
            transaction_side='seller',
            is_offer_scoped=True,
        )
        assert identity.template_slug == 'broker-compensation-agreement'
        assert slug == 'broker-compensation-agreement'
        assert retagged is True


def test_buyer_listing_agreement_not_retagged_to_listing():
    _slug, identity, retagged = resolve_upload_identity_for_extraction(
        template_slug='completed',
        file_bytes=b'%PDF-1.4',
        filename='TXR-1101-listing.pdf',
        transaction_side='buyer',
        is_offer_scoped=False,
    )
    if identity.kind == KIND_LISTING_AGREEMENT and identity.is_high_confidence:
        assert retagged is False


def test_ai_detected_documents_override_regex_package_embeds():
    """Model package map wins — Section 19 keyword hits must not survive."""
    regex_identity = DocumentIdentity(
        kind=KIND_LISTING_AGREEMENT,
        template_slug='listing-agreement',
        form_number='TXR-1101',
        label='Residential Real Estate Listing Agreement',
        confidence=0.95,
        matched_signals=('form:txr-1101',),
        extras={
            'embedded_components': [
                {
                    'kind': KIND_DISCLOSURE,
                    'template_slug': 'lead-paint',
                    'label': 'Lead-Based Paint Disclosure',
                    'addendum_key': 'lead_based_paint',
                    'confidence': 0.7,
                },
            ],
        },
    )
    field_data = {
        'detected_documents': [
            {
                'document_type': 'listing_agreement',
                'start_page': 1,
                'end_page': 10,
                'title': 'Listing Agreement',
            },
        ],
    }
    authoritative = apply_ai_package_authority(regex_identity, field_data)
    assert authoritative.extras.get('package_authority') == 'ai_detected_documents'
    assert authoritative.extras.get('embedded_components') == []
    assert authoritative.kind == KIND_LISTING_AGREEMENT

    persisted = persist_identity_on_field_data(field_data, regex_identity)
    assert persisted['_document_identity']['extras']['embedded_components'] == []
    assert persisted['_document_identity']['extras']['package_authority'] == (
        'ai_detected_documents'
    )


def test_ai_detected_supporting_forms_become_embeds():
    base = DocumentIdentity(
        kind=KIND_LISTING_AGREEMENT,
        template_slug='listing-agreement',
        confidence=0.9,
    )
    field_data = {
        'detected_documents': [
            {'document_type': 'listing_agreement', 'start_page': 1, 'end_page': 10},
            {'document_type': 'iabs', 'start_page': 11, 'end_page': 12},
            {'document_type': 'sellers_disclosure', 'start_page': 13, 'end_page': 18},
        ],
    }
    authoritative = apply_ai_package_authority(base, field_data)
    embeds = authoritative.extras.get('embedded_components') or []
    slugs = {c.get('template_slug') for c in embeds}
    assert slugs == {'iabs', 'sellers-disclosure'}
    assert all(c.get('source') == 'ai_detected_documents' for c in embeds)


def test_ai_empty_detected_documents_clears_package():
    base = DocumentIdentity(
        kind=KIND_LISTING_AGREEMENT,
        template_slug='listing-agreement',
        confidence=0.95,
        extras={
            'embedded_components': [
                {'template_slug': 'lead-paint', 'kind': KIND_DISCLOSURE},
            ],
        },
    )
    authoritative = apply_ai_package_authority(
        base,
        {'detected_documents': []},
    )
    assert authoritative.extras.get('embedded_components') == []
    assert authoritative.extras.get('package_authority') == 'ai_detected_documents'
