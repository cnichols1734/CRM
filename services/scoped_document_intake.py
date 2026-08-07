"""Attach uploaded TransactionDocuments to an explicit lifecycle scope.

Scopes reuse legacy-named offer/contract join tables
(``SellerOfferDocument``, ``SellerContractDocument``) with side enforced in
this service. Generic uploads do not invent offers unless the caller
explicitly requests ``create_new_offer``, or extraction auto-files a
high-confidence seller inbound purchase contract via
``maybe_auto_file_seller_inbound_offer``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy.orm.attributes import flag_modified

from models import (
    AuditEvent,
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    Transaction,
    TransactionDocument,
    db,
)
from services.controlling_contracts import (
    get_active_primary_contract,
    link_supporting_contract_document,
)
from services.document_identity import (
    KIND_ADDENDUM,
    KIND_DISCLOSURE,
    KIND_PROOF_OF_FUNDS,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
    identity_for_slug,
)
from services.offer_side import opening_direction_for_side, side_for_transaction
from services.seller_workflow import create_offer_activity, get_offer_document_type

logger = logging.getLogger(__name__)

_LISTING_PACKAGE_SLUGS = frozenset({
    'listing-agreement',
    'seller-net-proceeds',
    'iabs',
    'wire-fraud-warning',
    't47-affidavit',
    'sellers-disclosure',
    'special-tax-district-notice',
    'lead-paint',
})

_OFFER_SUPPORT_KINDS = frozenset({
    KIND_ADDENDUM,
    KIND_DISCLOSURE,
    KIND_PROOF_OF_FUNDS,
})

_OFFER_SUPPORT_CLASSIFICATIONS = frozenset({
    'financing_addendum',
    'hoa_addendum',
    'commission_document',
    'addendum',
    'disclosure',
    'proof_of_funds',
    'pre_approval',
})

SCOPE_LISTING = 'listing'
SCOPE_OFFER = 'offer'
SCOPE_CONTRACT = 'contract'
SCOPE_AMENDMENT = 'amendment'
SCOPE_OTHER = 'other'

VALID_SCOPES = frozenset({
    SCOPE_LISTING,
    SCOPE_OFFER,
    SCOPE_CONTRACT,
    SCOPE_AMENDMENT,
    SCOPE_OTHER,
})

# Canonical template slugs accepted for human confirmation / expected slots.
CANONICAL_TEMPLATE_SLUGS = frozenset({
    'listing-agreement',
    'one-to-four-family-contract',
    'condominium-contract',
    'new-home-completed-construction-contract',
    'new-home-incomplete-construction-contract',
    'farm-and-ranch-contract',
    'unimproved-property-contract',
    'purchase-contract',
    'seller-offer-contract',
    'seller-accepted-contract',
    'amendment',
    'third-party-financing-addendum',
    'appraisal-termination-addendum',
    'broker-compensation-agreement',
    'hoa-addendum',
    'seller-backup-addendum',
    'sellers-disclosure',
    'lead-paint',
    'pre-approval-or-proof-of-funds',
    'completed',
    'external',
    'custom',
})

_PURCHASE_PRIMARY_SLUGS = frozenset({
    'one-to-four-family-contract',
    'condominium-contract',
    'new-home-completed-construction-contract',
    'new-home-incomplete-construction-contract',
    'farm-and-ranch-contract',
    'unimproved-property-contract',
    'purchase-contract',
    'seller-offer-contract',
    'seller-accepted-contract',
})


class ScopedIntakeError(ValueError):
    """Validation failure for scoped intake (safe message for callers)."""

    def __init__(self, message: str, *, code: str = 'invalid_scope'):
        super().__init__(message)
        self.code = code


def is_canonical_slug(slug: str | None) -> bool:
    text = (slug or '').strip().lower()
    if not text:
        return False
    if text in CANONICAL_TEMPLATE_SLUGS:
        return True
    if text.startswith('custom-') or text.startswith('custom_'):
        return True
    return identity_for_slug(text) is not None


def _assert_tx_document(
    transaction: Transaction,
    document: TransactionDocument,
) -> None:
    if (
        document.organization_id != transaction.organization_id
        or document.transaction_id != transaction.id
    ):
        raise ScopedIntakeError(
            'Document not found on this transaction.',
            code='document_not_found',
        )


def _retag_document(
    document: TransactionDocument,
    *,
    template_slug: str | None,
    template_name: str | None = None,
) -> None:
    if template_slug and is_canonical_slug(template_slug):
        document.template_slug = template_slug.strip().lower()
    if template_name:
        document.template_name = str(template_name)[:200]


def _is_primary_purchase_slug(slug: str | None) -> bool:
    text = (slug or '').strip().lower()
    return text in _PURCHASE_PRIMARY_SLUGS or (
        'contract' in text and 'addendum' not in text and text != 'amendment'
    )


def attach_to_listing(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    actor_id: int,
    template_slug: str | None = None,
    template_name: str | None = None,
) -> dict[str, Any]:
    """Keep document at transaction level with a listing-package slug."""
    _assert_tx_document(transaction, document)
    slug = (template_slug or document.template_slug or 'listing-agreement').strip().lower()
    if slug not in CANONICAL_TEMPLATE_SLUGS and not is_canonical_slug(slug):
        raise ScopedIntakeError('Invalid listing template slug.', code='invalid_slug')
    _retag_document(
        document,
        template_slug=slug,
        template_name=template_name or document.template_name,
    )
    AuditEvent.log(
        event_type='document_scoped_listing',
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        document_id=document.id,
        actor_id=actor_id,
        description='Document attached to listing package',
        event_data={'template_slug': document.template_slug},
        source='scoped_document_intake',
    )
    db.session.flush()
    return {
        'scope': SCOPE_LISTING,
        'document_id': document.id,
        'template_slug': document.template_slug,
        'offer_id': None,
        'accepted_contract_id': None,
        'created': False,
    }


def attach_to_offer(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    actor_id: int,
    offer_id: int | None = None,
    create_new_offer: bool = False,
    template_slug: str | None = None,
    template_name: str | None = None,
    terms: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach document to an existing offer or explicitly create a new offer thread."""
    _assert_tx_document(transaction, document)
    side = side_for_transaction(transaction)
    if side not in ('buyer', 'seller'):
        raise ScopedIntakeError(
            'Offers are only supported on buyer or seller transactions.',
            code='side_unsupported',
        )

    if template_slug:
        if not is_canonical_slug(template_slug):
            raise ScopedIntakeError('Invalid offer template slug.', code='invalid_slug')
        _retag_document(document, template_slug=template_slug, template_name=template_name)

    existing_link = SellerOfferDocument.query.filter_by(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        transaction_document_id=document.id,
    ).first()
    if existing_link:
        return {
            'scope': SCOPE_OFFER,
            'document_id': document.id,
            'template_slug': document.template_slug,
            'offer_id': existing_link.offer_id,
            'accepted_contract_id': None,
            'created': False,
            'idempotent': True,
        }

    offer = None
    created = False
    if offer_id is not None:
        offer = SellerOffer.query.filter_by(
            id=int(offer_id),
            transaction_id=transaction.id,
            organization_id=transaction.organization_id,
        ).first()
        if not offer:
            raise ScopedIntakeError(
                'Offer not found on this transaction.',
                code='offer_not_found',
            )
    elif create_new_offer:
        offer = _create_offer_thread(
            transaction=transaction,
            document=document,
            actor_id=actor_id,
            side=side,
            terms=terms or {},
        )
        created = True
    else:
        raise ScopedIntakeError(
            'Select an existing offer or confirm create_new_offer.',
            code='offer_unconfirmed',
        )

    is_primary = _is_primary_purchase_slug(document.template_slug)
    version = None
    if is_primary:
        version = _ensure_offer_version(
            offer=offer,
            document=document,
            actor_id=actor_id,
            side=side,
            terms=terms or {},
        )

    link = SellerOfferDocument(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        offer_id=offer.id,
        transaction_document_id=document.id,
        offer_version_id=version.id if version else None,
        created_by_id=int(actor_id),
        document_type='buyer_offer' if is_primary else (document.template_slug or 'supporting'),
        display_name=document.template_name or 'Offer Document',
        is_primary_terms_document=bool(is_primary),
        extraction_summary={},
    )
    db.session.add(link)
    db.session.flush()

    create_offer_activity(
        offer,
        'document_uploaded',
        'Document attached to offer package',
        actor_id=actor_id,
        version_id=version.id if version else None,
        document_id=document.id,
        event_data={'scope': SCOPE_OFFER, 'created_offer': created},
    )
    db.session.flush()
    return {
        'scope': SCOPE_OFFER,
        'document_id': document.id,
        'template_slug': document.template_slug,
        'offer_id': offer.id,
        'accepted_contract_id': None,
        'created': created,
    }


def _create_offer_thread(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    actor_id: int,
    side: str,
    terms: dict[str, Any],
) -> SellerOffer:
    direction = opening_direction_for_side(side)
    raw_price = terms.get('_offer_price_decimal') or terms.get('offer_price') or terms.get('sales_price')
    offer_price = raw_price if isinstance(raw_price, Decimal) else _decimal_money(raw_price)
    offer = SellerOffer(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        created_by_id=int(actor_id),
        received_at=datetime.utcnow(),
        creation_source='scoped_intake',
        status='needs_review',
        offer_price=offer_price,
        proposed_close_date=None,
        financing_type=terms.get('financing_type'),
        earnest_money=_decimal_money(terms.get('earnest_money')),
        option_fee=_decimal_money(terms.get('option_fee')),
        option_period_days=terms.get('option_period_days'),
        buyer_names=terms.get('buyer_name') or terms.get('buyer_names'),
    )
    db.session.add(offer)
    db.session.flush()
    # Direction is stored on the version, not the offer row.
    _ = direction
    return offer


def _ensure_offer_version(
    *,
    offer: SellerOffer,
    document: TransactionDocument,
    actor_id: int,
    side: str,
    terms: dict[str, Any],
) -> SellerOfferVersion:
    existing = SellerOfferVersion.query.filter_by(
        organization_id=offer.organization_id,
        transaction_id=offer.transaction_id,
        transaction_document_id=document.id,
    ).first()
    if existing:
        return existing

    doc_config = get_offer_document_type('buyer_offer')
    version = SellerOfferVersion(
        organization_id=offer.organization_id,
        transaction_id=offer.transaction_id,
        offer_id=offer.id,
        created_by_id=int(actor_id),
        transaction_document_id=document.id,
        version_number=1,
        direction=doc_config.get('direction') or opening_direction_for_side(side),
        status='submitted',
        submitted_at=datetime.utcnow(),
        terms_data=dict(terms),
    )
    db.session.add(version)
    db.session.flush()
    offer.current_version_id = version.id
    return version


def attach_to_contract(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    actor_id: int,
    template_slug: str | None = None,
    template_name: str | None = None,
    as_primary: bool = False,
) -> dict[str, Any]:
    """Attach document to the active controlling contract package.

    Does **not** create a controlling baseline or apply legal/financial terms.
    Classification confirmation alone must leave proposal/review pending when
    terms are not separately approved.
    """
    _assert_tx_document(transaction, document)
    contract = get_active_primary_contract(
        transaction.id, transaction.organization_id,
    )
    if not contract:
        raise ScopedIntakeError(
            'No active controlling contract on this transaction.',
            code='no_controlling_contract',
        )

    if template_slug:
        if not is_canonical_slug(template_slug):
            raise ScopedIntakeError('Invalid contract template slug.', code='invalid_slug')
        _retag_document(document, template_slug=template_slug, template_name=template_name)

    existing = None
    from models import SellerContractDocument
    existing = SellerContractDocument.query.filter_by(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        transaction_document_id=document.id,
    ).first()
    if existing:
        return {
            'scope': SCOPE_CONTRACT,
            'document_id': document.id,
            'template_slug': document.template_slug,
            'offer_id': contract.offer_id,
            'accepted_contract_id': contract.id,
            'created': False,
            'idempotent': True,
        }

    if as_primary and _is_primary_purchase_slug(document.template_slug):
        from services.controlling_contracts import link_primary_contract_document
        link = link_primary_contract_document(
            contract=contract,
            document=document,
            actor_id=int(actor_id),
        )
    else:
        link = link_supporting_contract_document(
            transaction=transaction,
            document=document,
            actor_id=int(actor_id),
            document_type=document.template_slug or 'supporting',
            display_name=document.template_name,
        )
    AuditEvent.log(
        event_type='document_scoped_contract',
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        document_id=document.id,
        actor_id=actor_id,
        description='Document attached to controlling contract package',
        event_data={
            'accepted_contract_id': contract.id,
            'seller_contract_document_id': getattr(link, 'id', None),
            'as_primary': bool(as_primary),
        },
        source='scoped_document_intake',
    )
    db.session.flush()
    return {
        'scope': SCOPE_CONTRACT,
        'document_id': document.id,
        'template_slug': document.template_slug,
        'offer_id': contract.offer_id,
        'accepted_contract_id': contract.id,
        'created': False,
    }


def attach_as_other(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    actor_id: int,
    template_slug: str | None = None,
    template_name: str | None = None,
) -> dict[str, Any]:
    """Leave document at transaction level pending further classification."""
    _assert_tx_document(transaction, document)
    slug = (template_slug or document.template_slug or 'completed').strip().lower()
    if slug and not is_canonical_slug(slug):
        raise ScopedIntakeError('Invalid template slug.', code='invalid_slug')
    if slug:
        _retag_document(document, template_slug=slug, template_name=template_name)
    field_data = dict(document.field_data or {})
    field_data['_scope_attachment'] = {
        'scope': SCOPE_OTHER,
        'actor_id': int(actor_id),
        'attached_at': datetime.utcnow().isoformat(),
    }
    document.field_data = field_data
    flag_modified(document, 'field_data')
    db.session.flush()
    return {
        'scope': SCOPE_OTHER,
        'document_id': document.id,
        'template_slug': document.template_slug,
        'offer_id': None,
        'accepted_contract_id': None,
        'created': False,
    }


def _decimal_money(value: Any) -> Decimal | None:
    if value in (None, ''):
        return None
    try:
        cleaned = str(value).replace(',', '').replace('$', '').strip()
        if not cleaned:
            return None
        return Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _normalize_offer_price(value: Any, *, list_price: Any = None) -> Decimal | None:
    """Coerce extracted offer price; fix common cents-as-dollars OCR blowups."""
    price = _decimal_money(value)
    if price is None or price <= 0:
        return None
    list_amt = _decimal_money(list_price)
    # "$440,000.00" sometimes becomes 44000000 when decimals are stripped poorly.
    if price >= Decimal('10000000') or (
        list_amt and list_amt > 0 and price > list_amt * Decimal('5')
    ):
        candidate = (price / Decimal('100')).quantize(Decimal('0.01'))
        if candidate >= Decimal('1000') and (
            not list_amt
            or candidate <= list_amt * Decimal('3')
        ):
            return candidate
    return price.quantize(Decimal('0.01'))


def _buyer_name_key(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        text = ' '.join(str(v) for v in value if v)
    else:
        text = str(value)
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()


def terms_from_document_field_data(
    field_data: dict[str, Any] | None,
    *,
    list_price: Any = None,
) -> dict[str, Any]:
    data = field_data if isinstance(field_data, dict) else {}
    raw_price = (
        data.get('sales_price')
        or data.get('offer_price')
        or data.get('purchase_price')
    )
    price = _normalize_offer_price(raw_price, list_price=list_price)
    buyers = data.get('buyer_names') or data.get('buyer_name')
    if isinstance(buyers, list):
        buyers = ', '.join(str(b).strip() for b in buyers if str(b).strip())
    terms: dict[str, Any] = {}
    if price is not None:
        # JSON-safe strings for terms_data; Decimal kept only on the offer row.
        terms['sales_price'] = format(price, 'f')
        terms['offer_price'] = format(price, 'f')
        terms['_offer_price_decimal'] = price
    if buyers:
        terms['buyer_names'] = str(buyers)[:500]
        terms['buyer_name'] = str(buyers)[:500]
    for key in (
        'financing_type',
        'financing_amount',
        'total_financing_amount',
        'cash_down_payment',
        'earnest_money',
        'option_fee',
        'option_period_days',
        'seller_concessions_amount',
        'effective_date',
        'closing_date',
        'proposed_close_date',
        'title_policy_payer',
        'survey_payer',
        'residential_service_contract',
        'buyer_agent_commission_percent',
        'buyer_agent_commission_flat',
        'buyer_agent_name',
        'buyer_agent_brokerage',
    ):
        if data.get(key) not in (None, ''):
            terms[key] = data.get(key)
    if terms.get('proposed_close_date') in (None, '') and terms.get('closing_date'):
        terms['proposed_close_date'] = terms['closing_date']
    if terms.get('financing_amount') in (None, '') and terms.get('total_financing_amount'):
        terms['financing_amount'] = terms['total_financing_amount']
    return terms


def _identity_from_document(
    document: TransactionDocument,
    identity: DocumentIdentity | None = None,
) -> DocumentIdentity:
    if identity is not None:
        return identity
    data = document.field_data if isinstance(document.field_data, dict) else {}
    return DocumentIdentity.from_dict(data.get('_document_identity'))


def _is_offer_support_document(document: TransactionDocument) -> bool:
    if getattr(document, 'document_source', None) == 'placeholder':
        return False
    slug = (document.template_slug or '').strip().lower()
    if slug in _LISTING_PACKAGE_SLUGS or _is_primary_purchase_slug(slug):
        return False
    identity = _identity_from_document(document)
    if identity.kind in _OFFER_SUPPORT_KINDS:
        return True
    if 'offer' in (identity.possible_scopes or ()):
        return True
    data = document.field_data if isinstance(document.field_data, dict) else {}
    classification = str(data.get('document_classification') or '').strip().lower()
    if classification in _OFFER_SUPPORT_CLASSIFICATIONS:
        return True
    if slug in {
        'third-party-financing-addendum',
        'appraisal-termination-addendum',
        'hoa-addendum',
        'seller-backup-addendum',
        'pre-approval-or-proof-of-funds',
        'completed',
    }:
        # Generic completed + buyer names still qualifies via caller matching.
        return slug != 'completed' or bool(
            _buyer_name_key(data.get('buyer_names') or data.get('buyer_name'))
        )
    return False


def _attach_matching_offer_support_documents(
    *,
    transaction: Transaction,
    offer: SellerOffer,
    primary: TransactionDocument,
    actor_id: int,
) -> list[int]:
    """Attach orphan addenda that belong with this inbound offer package."""
    primary_buyers = _buyer_name_key(
        (primary.field_data or {}).get('buyer_names')
        or (primary.field_data or {}).get('buyer_name')
        or offer.buyer_names
    )
    window_start = (primary.created_at or datetime.utcnow()) - timedelta(minutes=15)
    window_end = (primary.created_at or datetime.utcnow()) + timedelta(minutes=15)

    linked_ids = {
        row.transaction_document_id
        for row in SellerOfferDocument.query.filter_by(
            organization_id=transaction.organization_id,
            transaction_id=transaction.id,
        ).all()
    }
    candidates = TransactionDocument.query.filter_by(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
    ).all()

    attached: list[int] = []
    for doc in candidates:
        if doc.id == primary.id or doc.id in linked_ids:
            continue
        if not _is_offer_support_document(doc):
            continue
        data = doc.field_data if isinstance(doc.field_data, dict) else {}
        buyers = _buyer_name_key(data.get('buyer_names') or data.get('buyer_name'))
        time_match = bool(
            doc.created_at and window_start <= doc.created_at <= window_end
        )
        buyer_match = bool(
            primary_buyers and buyers and (
                primary_buyers in buyers or buyers in primary_buyers
            )
        )
        # When the contract names buyers, require a buyer match so listing-package
        # addenda uploaded minutes earlier are not swept into the offer.
        if primary_buyers:
            if not buyer_match:
                continue
        elif not time_match:
            continue
        try:
            attach_to_offer(
                transaction=transaction,
                document=doc,
                actor_id=actor_id,
                offer_id=offer.id,
                template_slug=(
                    None
                    if (doc.template_slug or '') in ('completed', 'external', 'custom')
                    else None
                ),
            )
            attached.append(doc.id)
        except ScopedIntakeError:
            logger.info(
                'Skipped support attach for doc %s onto offer %s',
                doc.id,
                offer.id,
                exc_info=False,
            )
    return attached


def maybe_auto_file_seller_inbound_offer(
    *,
    document: TransactionDocument,
    actor_id: int | None,
    identity: DocumentIdentity | None = None,
) -> dict[str, Any] | None:
    """File a high-confidence seller purchase contract as an inbound offer.

    Idempotent. Also gathers matching orphan addenda into the same offer thread.
    Returns None when the document is not eligible.
    """
    if not actor_id or not document.transaction_id:
        return None

    identity = _identity_from_document(document, identity)
    if identity.kind != KIND_PURCHASE_CONTRACT or not identity.is_high_confidence:
        return None

    transaction = Transaction.query.filter_by(
        id=document.transaction_id,
        organization_id=document.organization_id,
    ).first()
    if not transaction:
        return None

    side = side_for_transaction(transaction)
    if side != 'seller':
        return None

    existing = SellerOfferDocument.query.filter_by(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        transaction_document_id=document.id,
    ).first()
    if existing:
        offer = SellerOffer.query.get(existing.offer_id)
        support_ids = []
        if offer:
            from services.seller_workflow import sync_offer_thread_from_extraction
            sync_offer_thread_from_extraction(document.id)
            support_ids = _attach_matching_offer_support_documents(
                transaction=transaction,
                offer=offer,
                primary=document,
                actor_id=int(actor_id),
            )
            _finalize_inbound_offer_filing(
                document=document,
                offer_id=int(existing.offer_id),
                actor_id=int(actor_id),
                support_ids=support_ids,
                identity=identity,
            )
        return {
            'scope': SCOPE_OFFER,
            'document_id': document.id,
            'offer_id': existing.offer_id,
            'created': False,
            'idempotent': True,
            'support_document_ids': support_ids,
        }

    extra = transaction.extra_data if isinstance(transaction.extra_data, dict) else {}
    terms = terms_from_document_field_data(
        document.field_data,
        list_price=extra.get('list_price'),
    )
    slug = identity.template_slug or 'seller-offer-contract'
    if slug not in _PURCHASE_PRIMARY_SLUGS:
        slug = 'seller-offer-contract'
    label = identity.label or document.template_name or 'Purchase Contract'

    offer_price_decimal = terms.pop('_offer_price_decimal', None)
    result = attach_to_offer(
        transaction=transaction,
        document=document,
        actor_id=int(actor_id),
        create_new_offer=True,
        template_slug=slug,
        template_name=label,
        terms=terms,
    )
    offer = SellerOffer.query.get(result['offer_id'])
    support_ids: list[int] = []
    if offer:
        if offer_price_decimal is not None:
            offer.offer_price = offer_price_decimal
        if terms.get('buyer_names'):
            offer.buyer_names = terms['buyer_names']
        from services.seller_workflow import sync_offer_thread_from_extraction
        sync_offer_thread_from_extraction(document.id)
        support_ids = _attach_matching_offer_support_documents(
            transaction=transaction,
            offer=offer,
            primary=document,
            actor_id=int(actor_id),
        )
        for support_id in support_ids:
            sync_offer_thread_from_extraction(support_id)
        _finalize_inbound_offer_filing(
            document=document,
            offer_id=int(result['offer_id']),
            actor_id=int(actor_id),
            support_ids=support_ids,
            identity=identity,
        )

    AuditEvent.log(
        event_type='document_routed_inbound_offer',
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        document_id=document.id,
        actor_id=int(actor_id),
        description='High-confidence purchase contract filed as inbound offer',
        event_data={
            'offer_id': result.get('offer_id'),
            'created': result.get('created'),
            'support_document_ids': support_ids,
            'identity': identity.to_dict(),
        },
        source='document_extraction',
    )
    result['support_document_ids'] = support_ids
    return result


def _finalize_inbound_offer_filing(
    *,
    document: TransactionDocument,
    offer_id: int,
    actor_id: int,
    support_ids: list[int],
    identity: DocumentIdentity | None = None,
) -> None:
    """Mark filing confirmed + quiet stale review noise after offer link."""
    from services.document_classification_confirm import mark_auto_filed_offer_confirmation
    from services.document_review import refresh_document_review_findings

    mark_auto_filed_offer_confirmation(
        document,
        actor_id=actor_id,
        offer_id=offer_id,
        template_slug=(identity.template_slug if identity else None) or document.template_slug,
        kind=(identity.kind if identity else None),
    )
    refresh_document_review_findings(document.id, org_id=document.organization_id)

    for support_id in support_ids or []:
        support = TransactionDocument.query.get(support_id)
        if not support:
            continue
        mark_auto_filed_offer_confirmation(
            support,
            actor_id=actor_id,
            offer_id=offer_id,
            template_slug=support.template_slug,
        )
        refresh_document_review_findings(support.id, org_id=support.organization_id)

    # Also refresh any support already linked earlier in a race with extraction.
    for link in SellerOfferDocument.query.filter_by(
        organization_id=document.organization_id,
        offer_id=offer_id,
    ).all():
        if link.transaction_document_id == document.id:
            continue
        if link.transaction_document_id in (support_ids or []):
            continue
        linked = TransactionDocument.query.get(link.transaction_document_id)
        if not linked:
            continue
        mark_auto_filed_offer_confirmation(
            linked,
            actor_id=actor_id,
            offer_id=offer_id,
            template_slug=linked.template_slug,
        )
        refresh_document_review_findings(linked.id, org_id=linked.organization_id)


def attach_document_to_scope(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    actor_id: int,
    scope: str,
    offer_id: int | None = None,
    create_new_offer: bool = False,
    template_slug: str | None = None,
    template_name: str | None = None,
    terms: dict[str, Any] | None = None,
    as_primary_contract_doc: bool = False,
) -> dict[str, Any]:
    """Dispatch attach by scope. Amendment scope only validates contract presence."""
    scope_norm = (scope or '').strip().lower()
    if scope_norm not in VALID_SCOPES:
        raise ScopedIntakeError('Invalid document scope.', code='invalid_scope')

    if scope_norm == SCOPE_LISTING:
        return attach_to_listing(
            transaction=transaction,
            document=document,
            actor_id=actor_id,
            template_slug=template_slug,
            template_name=template_name,
        )
    if scope_norm == SCOPE_OFFER:
        return attach_to_offer(
            transaction=transaction,
            document=document,
            actor_id=actor_id,
            offer_id=offer_id,
            create_new_offer=create_new_offer,
            template_slug=template_slug,
            template_name=template_name,
            terms=terms,
        )
    if scope_norm == SCOPE_CONTRACT:
        return attach_to_contract(
            transaction=transaction,
            document=document,
            actor_id=actor_id,
            template_slug=template_slug,
            template_name=template_name,
            as_primary=as_primary_contract_doc,
        )
    if scope_norm == SCOPE_AMENDMENT:
        contract = get_active_primary_contract(
            transaction.id, transaction.organization_id,
        )
        if not contract:
            raise ScopedIntakeError(
                'Amendments require an active controlling contract.',
                code='no_controlling_contract',
            )
        if template_slug:
            if not is_canonical_slug(template_slug):
                raise ScopedIntakeError('Invalid amendment slug.', code='invalid_slug')
            _retag_document(
                document,
                template_slug=template_slug or 'amendment',
                template_name=template_name,
            )
        else:
            _retag_document(document, template_slug='amendment', template_name=template_name)
        return {
            'scope': SCOPE_AMENDMENT,
            'document_id': document.id,
            'template_slug': document.template_slug,
            'offer_id': None,
            'accepted_contract_id': contract.id,
            'created': False,
            'amendment_pending_extraction': True,
        }
    return attach_as_other(
        transaction=transaction,
        document=document,
        actor_id=actor_id,
        template_slug=template_slug,
        template_name=template_name,
    )
