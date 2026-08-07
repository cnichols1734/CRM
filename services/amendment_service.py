"""
Contract amendment lifecycle (legacy ORM names).

``SellerContractAmendment*`` tables are legacy-named but side-neutral. Prefer
helpers here and ``services.controlling_contracts`` over seller-only call sites.
Creates amendments from extracted documents, diffs proposed terms against the
controlling baseline, and applies/rejects with deadline recompute via
``services.deadline_recompute``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy.orm.attributes import flag_modified

from models import (
    AuditEvent,
    SellerAcceptedContract,
    SellerContractAmendment,
    SellerContractAmendmentVersion,
    Transaction,
    TransactionDocument,
    db,
)
from services.controlling_contracts import get_active_primary_contract
from services.deadline_recompute import parse_date, recompute_from_changes
from services.document_extractor import visible_field_data
from services.offer_side import side_for_transaction
from services.seller_workflow import apply_contract_terms

logger = logging.getLogger(__name__)

AMENDMENT_TERM_KEYS = frozenset({
    'closing_date',
    'effective_date',
    'option_period_days',
    'option_fee',
    'sales_price',
    'offer_price',
    'earnest_money',
    'additional_earnest_money',
    'seller_concessions_amount',
    'financing_type',
    'financing_amount',
    'cash_down_payment',
    'possession_type',
    'leaseback_days',
    'title_company',
    'escrow_officer',
    'residential_service_contract',
})

AMENDMENT_FIELD_LABELS = {
    'closing_date': 'Closing date',
    'effective_date': 'Effective date',
    'option_period_days': 'Option period (days)',
    'option_fee': 'Option fee',
    'sales_price': 'Sales price',
    'offer_price': 'Offer price',
    'earnest_money': 'Earnest money',
    'additional_earnest_money': 'Additional earnest money',
    'seller_concessions_amount': 'Seller concessions',
    'financing_type': 'Financing type',
    'financing_amount': 'Financing amount',
    'cash_down_payment': 'Cash down payment',
    'possession_type': 'Possession type',
    'leaseback_days': 'Leaseback days',
    'title_company': 'Title company',
    'escrow_officer': 'Escrow officer',
    'residential_service_contract': 'Residential service contract',
}

# Canonical SellerAcceptedContract columns for keys that have one.
_CONTRACT_COLUMN_FOR_KEY = {
    'sales_price': 'accepted_price',
    'offer_price': 'accepted_price',
    'effective_date': 'effective_date',
    'closing_date': 'closing_date',
    'option_period_days': 'option_period_days',
    'financing_type': 'financing_type',
    'cash_down_payment': 'cash_down_payment',
    'financing_amount': 'financing_amount',
    'seller_concessions_amount': 'seller_concessions_amount',
    'title_company': 'title_company',
    'escrow_officer': 'escrow_officer',
    'residential_service_contract': 'residential_service_contract',
}

# The amendment extraction schema names revised terms `new_*` to distinguish them
# from the referenced original contract values that also appear on the page.
# An explicit `new_*` value therefore wins over the plain universal-field value.
_TERM_ALIASES = {
    'new_closing_date': 'closing_date',
    'new_sales_price': 'sales_price',
    'new_option_period_days': 'option_period_days',
    'new_option_fee': 'option_fee',
    'new_earnest_money': 'earnest_money',
}

_CLOSING_DATE_KEYS = frozenset({'closing_date'})


def _primary_active_contract(
    transaction_id: int,
    organization_id: int,
) -> Optional[SellerAcceptedContract]:
    return get_active_primary_contract(transaction_id, organization_id)


_AMENDMENT_DIRECTION_LABELS = {
    'buyer_amendment': 'Buyer amendment',
    'seller_amendment': 'Seller amendment',
    'seller_counter_amendment': 'Seller counter-amendment',
    'buyer_counter_amendment': 'Buyer counter-amendment',
    'accepted_amendment': 'Accepted amendment',
}


def amendment_direction_label(direction: str | None) -> str:
    """Humanize an amendment version direction for UI copy."""
    key = (direction or '').strip().lower()
    if key in _AMENDMENT_DIRECTION_LABELS:
        return _AMENDMENT_DIRECTION_LABELS[key]
    if not key:
        return ''
    return key.replace('_', ' ').title()


def opening_amendment_direction_for_side(side: str | None) -> str:
    """Opening version direction for an amendment filed on a representation side.

    Seller representation: inbound from the buyer → ``buyer_amendment``.
    Buyer representation: inbound from the seller/counterpart →
    ``seller_amendment``.
    """
    if (side or '').strip().lower() == 'buyer':
        return 'seller_amendment'
    return 'buyer_amendment'


def _amendment_type_from_classification(classification: Any) -> str:
    if classification is None:
        return 'other'
    text = str(classification).strip().lower()
    if not text:
        return 'other'
    if 'amendment' in text:
        return text[:100]
    return 'other'


def _terms_from_document(document: TransactionDocument) -> Dict[str, Any]:
    visible = visible_field_data(document.field_data)
    terms = {
        key: value
        for key, value in visible.items()
        if key in AMENDMENT_TERM_KEYS and value is not None
    }
    for alias, canonical in _TERM_ALIASES.items():
        value = visible.get(alias)
        if value is not None and value != '':
            terms[canonical] = value
    return terms


def _current_term_value(contract: SellerAcceptedContract, key: str) -> Any:
    column = _CONTRACT_COLUMN_FOR_KEY.get(key)
    if column is not None:
        value = getattr(contract, column, None)
        if value is not None:
            return value
    frozen = contract.frozen_terms or {}
    return frozen.get(key)


def _as_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(',', '').replace('$', '')
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _values_equal(current: Any, proposed: Any) -> bool:
    if current is None and proposed is None:
        return True
    if current is None or proposed is None:
        return False

    current_date = parse_date(current)
    proposed_date = parse_date(proposed)
    if current_date is not None and proposed_date is not None:
        return current_date == proposed_date

    current_num = _as_decimal(current)
    proposed_num = _as_decimal(proposed)
    if current_num is not None and proposed_num is not None:
        return current_num == proposed_num

    return str(current).strip().lower() == str(proposed).strip().lower()


def _resolve_selected_keys(
    amendment: SellerContractAmendment,
    version: SellerContractAmendmentVersion,
    selected_keys: Optional[Iterable[str]],
) -> Set[str]:
    version_keys = {
        key for key, value in (version.terms_data or {}).items()
        if key in AMENDMENT_TERM_KEYS and value is not None
    }
    if selected_keys is None:
        return {
            entry['key']
            for entry in diff_against_contract(amendment)
            if entry.get('changed')
        }
    return set(selected_keys) & AMENDMENT_TERM_KEYS & version_keys


def create_from_document(
    document: TransactionDocument,
    *,
    actor_id: int,
) -> Optional[SellerContractAmendment]:
    """Create an amendment + v1 version from an extracted transaction document.

    Idempotent for retries: if a version already references this document,
    return its amendment instead of creating a duplicate.
    """
    if not actor_id or int(actor_id) <= 0:
        logger.warning(
            'Refusing amendment create for doc %s: invalid actor_id=%r',
            getattr(document, 'id', None),
            actor_id,
        )
        return None

    existing_version = SellerContractAmendmentVersion.query.filter_by(
        organization_id=document.organization_id,
        transaction_id=document.transaction_id,
        transaction_document_id=document.id,
    ).first()
    if existing_version:
        return SellerContractAmendment.query.filter_by(
            id=existing_version.amendment_id,
            organization_id=document.organization_id,
            transaction_id=document.transaction_id,
        ).first()

    contract = _primary_active_contract(
        document.transaction_id,
        document.organization_id,
    )
    if not contract:
        logger.info(
            'No active primary contract for transaction %s; skipping amendment',
            document.transaction_id,
        )
        return None

    visible = visible_field_data(document.field_data)
    transaction = Transaction.query.filter_by(
        id=document.transaction_id,
        organization_id=document.organization_id,
    ).first()
    side = side_for_transaction(transaction) if transaction else None
    direction = opening_amendment_direction_for_side(side)

    amendment = SellerContractAmendment(
        organization_id=document.organization_id,
        transaction_id=document.transaction_id,
        accepted_contract_id=contract.id,
        created_by_id=int(actor_id),
        status='received',
        amendment_type=_amendment_type_from_classification(
            visible.get('document_classification'),
        ),
        summary=visible.get('document_summary'),
        extra_data={'representation_side': side},
    )
    db.session.add(amendment)
    db.session.flush()

    version = SellerContractAmendmentVersion(
        organization_id=document.organization_id,
        transaction_id=document.transaction_id,
        amendment_id=amendment.id,
        created_by_id=actor_id,
        transaction_document_id=document.id,
        version_number=1,
        direction=direction,
        status='submitted',
        submitted_at=datetime.utcnow(),
        terms_data=_terms_from_document(document),
    )
    db.session.add(version)
    db.session.flush()

    amendment.current_version_id = version.id
    db.session.flush()

    AuditEvent.log(
        event_type='amendment_created',
        organization_id=document.organization_id,
        transaction_id=document.transaction_id,
        document_id=document.id,
        actor_id=actor_id,
        description='Amendment created from document',
        event_data={
            'amendment_id': amendment.id,
            'version_id': version.id,
            'accepted_contract_id': contract.id,
            'term_keys': sorted((version.terms_data or {}).keys()),
        },
        source='system',
    )
    db.session.flush()
    return amendment


def current_version(
    amendment: SellerContractAmendment,
) -> Optional[SellerContractAmendmentVersion]:
    """Resolve the current amendment version, preferring current_version_id."""
    if amendment.current_version_id:
        version = SellerContractAmendmentVersion.query.filter_by(
            id=amendment.current_version_id,
            amendment_id=amendment.id,
            organization_id=amendment.organization_id,
        ).first()
        if version:
            return version

    return (
        SellerContractAmendmentVersion.query.filter_by(
            amendment_id=amendment.id,
            organization_id=amendment.organization_id,
        )
        .order_by(SellerContractAmendmentVersion.version_number.desc())
        .first()
    )


def diff_against_contract(amendment: SellerContractAmendment) -> List[Dict[str, Any]]:
    """Diff current version terms against the accepted contract."""
    version = current_version(amendment)
    if not version:
        return []

    contract = SellerAcceptedContract.query.filter_by(
        id=amendment.accepted_contract_id,
        organization_id=amendment.organization_id,
        transaction_id=amendment.transaction_id,
    ).first()
    if not contract:
        return []

    entries: List[Dict[str, Any]] = []
    for key, proposed in (version.terms_data or {}).items():
        if key not in AMENDMENT_TERM_KEYS:
            continue
        current = _current_term_value(contract, key)
        entries.append({
            'key': key,
            'label': AMENDMENT_FIELD_LABELS.get(key, key),
            'current': current,
            'proposed': proposed,
            'changed': not _values_equal(current, proposed),
        })

    entries.sort(key=lambda item: (not item['changed'], item['label'].lower()))
    return entries


def accept(
    amendment: SellerContractAmendment,
    *,
    actor_id: int,
    selected_keys: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Accept selected amendment terms and recompute impacted deadlines."""
    if amendment.status == 'accepted':
        raise ValueError('Amendment is already accepted')

    version = current_version(amendment)
    if not version:
        raise ValueError('Amendment has no current version')

    contract = SellerAcceptedContract.query.filter_by(
        id=amendment.accepted_contract_id,
        organization_id=amendment.organization_id,
        transaction_id=amendment.transaction_id,
    ).first()
    if not contract:
        raise ValueError('Accepted contract not found for amendment')

    transaction = Transaction.query.filter_by(
        id=amendment.transaction_id,
        organization_id=amendment.organization_id,
    ).first()
    if not transaction:
        raise ValueError('Transaction not found for amendment')

    keys = _resolve_selected_keys(amendment, version, selected_keys)
    applied_terms = {
        key: (version.terms_data or {})[key]
        for key in keys
        if key in (version.terms_data or {}) and (version.terms_data or {}).get(key) is not None
    }

    if applied_terms:
        terms = dict(contract.frozen_terms or {})
        for key, value in applied_terms.items():
            terms[key] = value
        apply_contract_terms(contract, terms)
        flag_modified(contract, 'frozen_terms')
        flag_modified(contract, 'addenda_data')
        flag_modified(contract, 'extra_data')

        if _CLOSING_DATE_KEYS & set(applied_terms):
            close_date = parse_date(applied_terms.get('closing_date'))
            if close_date:
                transaction.expected_close_date = close_date

    amendment.status = 'accepted'
    amendment.accepted_version_id = version.id
    version.status = 'accepted'
    version.reviewed_at = datetime.utcnow()
    version.reviewed_by_id = actor_id

    recompute = recompute_from_changes(
        transaction,
        applied_terms,
        actor_id=actor_id,
        source='amendment_apply',
    )

    audit_event = AuditEvent.log(
        event_type='amendment_accepted',
        organization_id=amendment.organization_id,
        transaction_id=amendment.transaction_id,
        actor_id=actor_id,
        description='Amendment accepted',
        event_data={
            'amendment_id': amendment.id,
            'applied_keys': sorted(applied_terms.keys()),
            'recompute': recompute.as_dict(),
        },
        source='system',
    )
    db.session.flush()

    return {
        'amendment_id': amendment.id,
        'applied_keys': sorted(applied_terms.keys()),
        'recompute': recompute.as_dict(),
        'audit_event_id': audit_event.id,
    }


def reject(
    amendment: SellerContractAmendment,
    *,
    actor_id: int,
    reason: Optional[str] = None,
) -> None:
    """Reject an amendment and mark the current version declined."""
    if amendment.status == 'accepted':
        raise ValueError('Cannot reject an amendment that is already accepted')

    version = current_version(amendment)
    amendment.status = 'rejected'
    if version:
        version.status = 'declined'
        version.reviewed_at = datetime.utcnow()
        version.reviewed_by_id = actor_id

    AuditEvent.log(
        event_type='amendment_rejected',
        organization_id=amendment.organization_id,
        transaction_id=amendment.transaction_id,
        actor_id=actor_id,
        description='Amendment rejected',
        event_data={
            'amendment_id': amendment.id,
            'reason': reason,
        },
        source='system',
    )
    db.session.flush()


def list_for_transaction(
    transaction_id: int,
    organization_id: int,
) -> List[SellerContractAmendment]:
    """List amendments for a transaction, newest first."""
    return (
        SellerContractAmendment.query.filter_by(
            transaction_id=transaction_id,
            organization_id=organization_id,
        )
        .order_by(SellerContractAmendment.created_at.desc())
        .all()
    )


def pending_count(transaction_id: int, organization_id: int) -> int:
    """Count amendments still in a pending review state."""
    return SellerContractAmendment.query.filter(
        SellerContractAmendment.transaction_id == transaction_id,
        SellerContractAmendment.organization_id == organization_id,
        SellerContractAmendment.status.in_(('received', 'reviewing', 'countered')),
    ).count()
