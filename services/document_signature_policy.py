"""Who Bob should expect to have signed, given document type and filing purpose.

Listing-stage paperwork (listing agreement, seller's disclosure, IABS, etc.)
is executed by the seller. Buyer signature / acknowledgement lines on those
forms stay blank until the document is delivered with an offer or contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models import SellerContractDocument, SellerOfferDocument
from services.document_classification_policy import DISCLOSURE_SLUGS, PURCHASE_SLUGS
from services.listing_packet import LISTING_PACKET_FILING
from services.offer_side import side_for_transaction

PURPOSE_LISTING = 'listing'
PURPOSE_OFFER = 'offer'
PURPOSE_CONTRACT = 'contract'
PURPOSE_OTHER = 'other'

LISTING_PURPOSE_SLUGS = frozenset(
    {slug for slug, _name in LISTING_PACKET_FILING.values()}
    | {
        'listing-agreement',
        'sellers-disclosure',
        'seller-disclosure',
        'iabs',
        'information-about-brokerage-services',
        'lead-paint',
        'lead-based-paint',
        'hoa-addendum',
        'wire-fraud-warning',
        'seller-net-proceeds',
        'flood-hazard',
        't47-affidavit',
        'special-tax-district-notice',
        'sewer-facility',
        'referral-agreement',
        'supporting-document',
    }
    | {str(slug).replace('_', '-') for slug in DISCLOSURE_SLUGS}
)

PURCHASE_PURPOSE_SLUGS = frozenset(PURCHASE_SLUGS) | {
    'accepted-contract',
    'one-to-four-family-contract',
    'purchase-contract',
    'seller-offer-contract',
    'seller-accepted-contract',
}


@dataclass(frozen=True)
class SignatureExpectations:
    purpose: str
    expect_buyer: bool
    expect_seller: bool


def _norm_slug(value: Any) -> str:
    return str(value or '').strip().lower().replace('_', '-')


def _linked_to_offer(transaction, document) -> bool:
    doc_id = getattr(document, 'id', None)
    if not doc_id or transaction is None:
        return False
    try:
        return SellerOfferDocument.query.filter_by(
            organization_id=transaction.organization_id,
            transaction_id=transaction.id,
            transaction_document_id=doc_id,
        ).first() is not None
    except Exception:
        return False


def _linked_to_contract(transaction, document) -> bool:
    doc_id = getattr(document, 'id', None)
    if not doc_id or transaction is None:
        return False
    try:
        return SellerContractDocument.query.filter_by(
            organization_id=transaction.organization_id,
            transaction_id=transaction.id,
            transaction_document_id=doc_id,
        ).first() is not None
    except Exception:
        return False


def resolve_document_purpose(transaction, document) -> str:
    """Return listing / offer / contract / other for signature review."""
    if _linked_to_offer(transaction, document):
        return PURPOSE_OFFER
    if _linked_to_contract(transaction, document):
        return PURPOSE_CONTRACT

    slug = _norm_slug(getattr(document, 'template_slug', None))
    if slug in PURCHASE_PURPOSE_SLUGS or (
        'contract' in slug and 'listing' not in slug
    ):
        return PURPOSE_OFFER
    if slug in LISTING_PURPOSE_SLUGS:
        return PURPOSE_LISTING

    side = side_for_transaction(transaction)
    if side == 'seller':
        return PURPOSE_LISTING
    if side == 'buyer':
        return PURPOSE_OFFER
    return PURPOSE_OTHER


def signature_expectations(transaction, document) -> SignatureExpectations:
    """Buyer signatures are only required on offer/contract filings."""
    purpose = resolve_document_purpose(transaction, document)
    if purpose == PURPOSE_LISTING:
        return SignatureExpectations(
            purpose=purpose,
            expect_buyer=False,
            expect_seller=True,
        )
    if purpose == PURPOSE_OFFER:
        side = side_for_transaction(transaction)
        return SignatureExpectations(
            purpose=purpose,
            expect_buyer=True,
            expect_seller=side != 'seller',
        )
    if purpose == PURPOSE_CONTRACT:
        return SignatureExpectations(
            purpose=purpose,
            expect_buyer=True,
            expect_seller=True,
        )
    return SignatureExpectations(
        purpose=purpose,
        expect_buyer=True,
        expect_seller=True,
    )


def extraction_review_context(transaction, document) -> str:
    """Prompt addendum so extraction flags match the filing purpose."""
    expectations = signature_expectations(transaction, document)
    slug = _norm_slug(getattr(document, 'template_slug', None))
    slot = slug.replace('-', ' ') if slug else 'uploaded document'
    if expectations.purpose == PURPOSE_LISTING:
        return (
            f'TRANSACTION CONTEXT: This PDF was uploaded as LISTING-STAGE paperwork '
            f'(slot: {slot}). There is no buyer on this file yet. '
            'Do not flag a missing buyer signature or buyer acknowledgement. '
            "Buyer receipt lines on a Seller's Disclosure are expected to be blank "
            'until this document is delivered with an offer or contract. '
            'buyer_signature_detected must be null, not false. '
            'Do flag a missing seller signature if the seller signature area is '
            'visibly blank.'
        )
    if expectations.purpose in (PURPOSE_OFFER, PURPOSE_CONTRACT):
        parts = [
            f'TRANSACTION CONTEXT: This PDF was uploaded as {expectations.purpose.upper()} '
            f'paperwork (slot: {slot}). Check buyer signatures or acknowledgements '
            'where the form has a buyer signature block.',
        ]
        if not expectations.expect_seller:
            parts.append(
                'Blank seller signature lines are expected on inbound buyer offers '
                '— do not flag them. Buyer names will not match listing CRM parties.'
            )
        return ' '.join(parts)
    return ''
