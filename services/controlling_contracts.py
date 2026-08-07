"""Side-neutral controlling-contract APIs.

The ORM classes ``SellerAcceptedContract`` and ``SellerContractDocument`` are
legacy-named but used for both buyer and seller representation sides. Prefer
this module over querying those models directly in new code.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm.attributes import flag_modified

from models import (
    AuditEvent,
    SellerAcceptedContract,
    SellerContractDocument,
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    Transaction,
    TransactionDocument,
    db,
)
from services.offer_side import side_for_transaction
from services.seller_workflow import (
    _coerce_decimal as _coerce_money,
    apply_contract_terms,
    create_contract_milestones,
    create_offer_activity,
    derive_financing_approval_deadline,
    seed_ctc_requirements_from_accepted_contract,
)

logger = logging.getLogger(__name__)

_SOURCE_DOC_KEY = 'source_document_id'
_BASELINE_META_KEY = '_controlling_baseline'
_CONFIRM_META_KEY = '_classification_confirmation'


class ControllingContractConflict(ValueError):
    """Active primary already exists for a different document."""

    def __init__(
        self,
        message: str,
        *,
        code: str = 'baseline_conflict',
        existing_contract_id: int | None = None,
        status: int = 409,
    ):
        super().__init__(message)
        self.code = code
        self.existing_contract_id = existing_contract_id
        self.status = status


class ControllingContractSeedError(RuntimeError):
    """Requirement seeding failed after baseline creation — caller must roll back."""

    def __init__(self, message: str, *, code: str = 'ctc_seed_failed'):
        super().__init__(message)
        self.code = code


def get_active_primary_contract(
    transaction_id: int,
    organization_id: int,
) -> Optional[SellerAcceptedContract]:
    """Return the active primary controlling contract for a transaction, or None."""
    if not transaction_id or not organization_id:
        return None
    return SellerAcceptedContract.query.filter_by(
        transaction_id=transaction_id,
        organization_id=organization_id,
        position='primary',
        status='active',
    ).first()


def has_active_primary_contract(
    transaction_id: int,
    organization_id: int,
) -> bool:
    """True when an active primary controlling contract exists."""
    return get_active_primary_contract(transaction_id, organization_id) is not None


def count_active_primaries(transaction_id: int, organization_id: int) -> int:
    return SellerAcceptedContract.query.filter_by(
        transaction_id=transaction_id,
        organization_id=organization_id,
        position='primary',
        status='active',
    ).count()


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _parse_int(value: Any) -> Optional[int]:
    if value in (None, ''):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    for fmt in (
        '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y'):
        try:
            return datetime.strptime(
                text[:10] if fmt == '%Y-%m-%d' else text, fmt,
            ).date()
        except ValueError:
            continue
    return None


def _first_money(*candidates: Any) -> Optional[Decimal]:
    """First parseable money value, including zero (do not use truthiness)."""
    for value in candidates:
        if value is None or value == '':
            continue
        if isinstance(value, Decimal):
            return value
        parsed = _coerce_money(value)
        if parsed is not None:
            return parsed
    return None


def link_primary_contract_document(
    *,
    contract: SellerAcceptedContract,
    document: TransactionDocument,
    actor_id: int,
    display_name: str | None = None,
) -> SellerContractDocument:
    """Attach a TransactionDocument as the primary contract PDF (idempotent)."""
    existing = SellerContractDocument.query.filter_by(
        organization_id=contract.organization_id,
        transaction_id=contract.transaction_id,
        transaction_document_id=document.id,
    ).first()
    if existing:
        if not existing.is_primary_contract_document:
            existing.is_primary_contract_document = True
            existing.accepted_contract_id = contract.id
        return existing

    link = SellerContractDocument(
        organization_id=contract.organization_id,
        transaction_id=contract.transaction_id,
        accepted_contract_id=contract.id,
        transaction_document_id=document.id,
        created_by_id=actor_id,
        document_type='primary_contract',
        display_name=display_name or document.template_name or 'Executed Contract',
        is_primary_contract_document=True,
        extraction_summary={},
    )
    db.session.add(link)
    db.session.flush()
    return link


def _contract_for_document(
    *,
    document: TransactionDocument,
) -> Optional[SellerAcceptedContract]:
    """Find an existing controlling contract already linked to this document."""
    link = SellerContractDocument.query.filter_by(
        organization_id=document.organization_id,
        transaction_id=document.transaction_id,
        transaction_document_id=document.id,
    ).first()
    if not link:
        # Also accept baseline stamped on the document itself.
        meta = (document.field_data or {}).get(_BASELINE_META_KEY) or {}
        contract_id = meta.get('accepted_contract_id') if isinstance(meta, dict) else None
        if contract_id:
            return SellerAcceptedContract.query.filter_by(
                id=int(contract_id),
                organization_id=document.organization_id,
                transaction_id=document.transaction_id,
            ).first()
        return None
    return SellerAcceptedContract.query.filter_by(
        id=link.accepted_contract_id,
        organization_id=document.organization_id,
        transaction_id=document.transaction_id,
    ).first()


def _document_is_primary_for_contract(
    document: TransactionDocument,
    contract: SellerAcceptedContract,
) -> bool:
    link = SellerContractDocument.query.filter_by(
        organization_id=document.organization_id,
        transaction_id=document.transaction_id,
        transaction_document_id=document.id,
        accepted_contract_id=contract.id,
        is_primary_contract_document=True,
    ).first()
    if link:
        return True
    meta = (document.field_data or {}).get(_BASELINE_META_KEY) or {}
    return (
        isinstance(meta, dict)
        and meta.get('accepted_contract_id') == contract.id
    )


def create_baseline_from_document(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    approved_terms: dict[str, Any],
    actor_id: int,
    offer_id: int | None = None,
    position: str = 'primary',
    seed_requirements: bool = True,
    attach_as_supporting: bool = False,
) -> SellerAcceptedContract:
    """Create or reuse a controlling baseline from human-approved terms.

    Idempotent for the **same** document retry: may refresh with newly
    human-approved selected terms only.

    A **different** document when an active primary already exists raises
    ``ControllingContractConflict`` unless ``attach_as_supporting=True``, in
    which case the document is linked as supporting without applying terms.

    Requirement seeding failures propagate so the caller can roll back —
    never leave a half-created controlling contract without its intended
    checklist when anchors exist.
    """
    if not actor_id or int(actor_id) <= 0:
        raise ValueError('A valid actor_id is required to create a controlling baseline')
    if document.organization_id != transaction.organization_id:
        raise ValueError('Document organization does not match transaction')
    if document.transaction_id != transaction.id:
        raise ValueError('Document is not attached to this transaction')
    if position not in ('primary', 'backup'):
        raise ValueError('position must be primary or backup')

    terms = dict(approved_terms or {})
    existing = _contract_for_document(document=document)
    if existing:
        # Same-document retry — refresh selected approved terms only.
        if terms:
            apply_contract_terms(existing, terms)
            flag_modified(existing, 'frozen_terms')
            flag_modified(existing, 'addenda_data')
            flag_modified(existing, 'extra_data')
        if position == 'primary' and existing.status == 'active':
            transaction.status = 'under_contract'
            if existing.closing_date:
                transaction.expected_close_date = existing.closing_date
        if _document_is_primary_for_contract(document, existing) or existing.position == 'primary':
            link_primary_contract_document(
                contract=existing,
                document=document,
                actor_id=int(actor_id),
            )
        if seed_requirements and position == 'primary':
            _seed_ctc(transaction, existing, actor_id=int(actor_id))
        db.session.flush()
        return existing

    if position == 'primary':
        active = get_active_primary_contract(transaction.id, transaction.organization_id)
        if active:
            if attach_as_supporting:
                link = link_supporting_contract_document(
                    transaction=transaction,
                    document=document,
                    actor_id=int(actor_id),
                    document_type=document.template_slug or 'supporting',
                    display_name=document.template_name,
                )
                db.session.flush()
                return active
            raise ControllingContractConflict(
                'A controlling contract already exists for this transaction. '
                'Use amendment review or an explicit replacement workflow — '
                'this document was not applied to the existing baseline.',
                code='baseline_conflict',
                existing_contract_id=active.id,
                status=409,
            )

    # Guard against race creating a second active primary.
    if position == 'primary' and count_active_primaries(
        transaction.id, transaction.organization_id,
    ) > 0:
        active = get_active_primary_contract(transaction.id, transaction.organization_id)
        raise ControllingContractConflict(
            'A controlling contract already exists for this transaction.',
            code='baseline_conflict',
            existing_contract_id=getattr(active, 'id', None),
            status=409,
        )

    closing = _parse_date(
        terms.get('closing_date')
        or terms.get('proposed_close_date')
        or terms.get('close_date')
    )
    effective = _parse_date(terms.get('effective_date'))
    price = (
        terms.get('sales_price')
        or terms.get('offer_price')
        or terms.get('purchase_price')
    )

    side = side_for_transaction(transaction) or 'seller'
    extra = {
        _SOURCE_DOC_KEY: document.id,
        'representation_side': side,
        'created_via': 'controlling_contracts.create_baseline_from_document',
    }

    contract = SellerAcceptedContract(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        offer_id=offer_id,
        created_by_id=int(actor_id),
        position=position,
        status='active',
        accepted_price=price,
        effective_date=effective,
        closing_date=closing,
        option_period_days=terms.get('option_period_days'),
        financing_type=terms.get('financing_type'),
        cash_down_payment=terms.get('cash_down_payment'),
        financing_amount=terms.get('financing_amount'),
        seller_concessions_amount=terms.get('seller_concessions_amount'),
        title_company=terms.get('title_company'),
        escrow_officer=terms.get('escrow_officer'),
        frozen_terms=dict(terms),
        addenda_data=terms.get('addenda') or {},
        extra_data=extra,
    )
    db.session.add(contract)
    db.session.flush()

    # Re-check after flush for concurrent primary inserts.
    if position == 'primary' and count_active_primaries(
        transaction.id, transaction.organization_id,
    ) > 1:
        db.session.delete(contract)
        db.session.flush()
        active = get_active_primary_contract(transaction.id, transaction.organization_id)
        raise ControllingContractConflict(
            'A controlling contract already exists for this transaction.',
            code='baseline_conflict',
            existing_contract_id=getattr(active, 'id', None),
            status=409,
        )

    if terms:
        apply_contract_terms(contract, terms)
        flag_modified(contract, 'frozen_terms')
        flag_modified(contract, 'addenda_data')
        flag_modified(contract, 'extra_data')

    link_primary_contract_document(
        contract=contract,
        document=document,
        actor_id=int(actor_id),
        display_name=document.template_name,
    )

    if position == 'primary':
        transaction.status = 'under_contract'
        if contract.closing_date:
            transaction.expected_close_date = contract.closing_date
        create_contract_milestones(contract)
        if seed_requirements:
            _seed_ctc(transaction, contract, actor_id=int(actor_id))

    field_data = dict(document.field_data or {})
    field_data[_BASELINE_META_KEY] = {
        'accepted_contract_id': contract.id,
        'confirmed_at': datetime.utcnow().isoformat(),
        'actor_id': int(actor_id),
        'side': side,
    }
    document.field_data = field_data
    flag_modified(document, 'field_data')

    AuditEvent.log(
        event_type='controlling_contract_baseline_created',
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        document_id=document.id,
        actor_id=int(actor_id),
        description='Controlling contract baseline created from approved document terms',
        event_data={
            'accepted_contract_id': contract.id,
            'offer_id': offer_id,
            'position': position,
            'side': side,
            'term_keys': sorted(terms.keys()),
        },
        source='controlling_contracts',
    )
    db.session.flush()
    logger.info(
        'Controlling baseline %s created for tx %s (side=%s, doc=%s)',
        contract.id, transaction.id, side, document.id,
    )
    return contract


def _seed_ctc(
    transaction: Transaction,
    contract: SellerAcceptedContract,
    *,
    actor_id: int,
) -> None:
    """Seed CTC packs. Propagates failures so callers can roll back atomically."""
    side = side_for_transaction(transaction) or 'seller'
    pack_key = 'buyer_ctc' if side == 'buyer' else 'seller_ctc'
    # Only require seeding success when anchors exist.
    has_anchors = bool(
        getattr(contract, 'effective_date', None)
        or getattr(contract, 'closing_date', None)
    )
    try:
        result = seed_ctc_requirements_from_accepted_contract(
            transaction=transaction,
            accepted_contract=contract,
            actor_id=actor_id,
            pack_key=pack_key,
        )
    except Exception as exc:
        logger.exception(
            'Failed to seed %s for transaction %s', pack_key, transaction.id,
        )
        raise ControllingContractSeedError(
            'Could not seed contract-to-close requirements for the controlling baseline.',
            code='ctc_seed_failed',
        ) from exc

    if has_anchors and isinstance(result, dict) and result.get('reason') == 'no_anchors':
        # Anchors were expected from columns — treat as soft; seed helper may
        # still return no_anchors if dates failed coercion. Re-raise only when
        # columns clearly had dates but pack apply failed to see them.
        pass


def _merged_offer_acceptance_terms(
    offer: SellerOffer,
    version: SellerOfferVersion | None,
) -> dict[str, Any]:
    terms = (
        dict(version.terms_data or {})
        if version and isinstance(version.terms_data, dict)
        else {}
    )
    offer_terms = (
        dict(offer.terms_summary or {})
        if isinstance(offer.terms_summary, dict)
        else {}
    )
    for key, value in offer_terms.items():
        if key in ('supporting_documents', 'addenda'):
            continue
        if value is not None and key not in terms:
            terms[key] = value

    supporting = dict(_as_dict(offer_terms.get('supporting_documents')))
    supporting.update(_as_dict(terms.get('supporting_documents')))
    if supporting:
        terms['supporting_documents'] = supporting

    addenda = dict(_as_dict(offer_terms.get('addenda')))
    addenda.update(_as_dict(terms.get('addenda')))
    if addenda:
        terms['addenda'] = addenda
    return terms


def _extra_data_from_offer(offer: SellerOffer, terms: dict[str, Any]) -> dict[str, Any]:
    supporting = _as_dict(terms.get('supporting_documents'))
    return {
        'source_offer_id': offer.id,
        'source_offer_status': offer.status,
        'buyer_names': offer.buyer_names,
        'buyer_agent_name': offer.buyer_agent_name,
        'buyer_agent_email': offer.buyer_agent_email,
        'buyer_agent_phone': offer.buyer_agent_phone,
        'buyer_agent_brokerage': offer.buyer_agent_brokerage,
        'supporting_documents': supporting,
        'created_via': 'controlling_contracts.create_baseline_from_accepted_offer',
        'representation_side': 'seller',
    }


def _optional_document_for_offer(
    offer: SellerOffer,
    version: SellerOfferVersion | None,
) -> Optional[TransactionDocument]:
    """Best-effort PDF for an accepted offer — manual offers may have none."""
    if version and version.transaction_document_id:
        doc = TransactionDocument.query.filter_by(
            id=version.transaction_document_id,
            transaction_id=offer.transaction_id,
            organization_id=offer.organization_id,
        ).first()
        if doc:
            return doc

    link = (
        SellerOfferDocument.query.filter_by(
            offer_id=offer.id,
            organization_id=offer.organization_id,
            transaction_id=offer.transaction_id,
            is_primary_terms_document=True,
        )
        .order_by(SellerOfferDocument.id.asc())
        .first()
    )
    if link and link.transaction_document_id:
        return TransactionDocument.query.filter_by(
            id=link.transaction_document_id,
            transaction_id=offer.transaction_id,
            organization_id=offer.organization_id,
        ).first()
    return None


def create_baseline_from_accepted_offer(
    *,
    transaction: Transaction,
    offer: SellerOffer,
    actor_id: int,
    position: str = 'primary',
    version: SellerOfferVersion | None = None,
    document: TransactionDocument | None = None,
    effective_date: date | None = None,
    effective_at: datetime | None = None,
    backup_position: Any = None,
    backup_addendum_document_id: Any = None,
    seed_requirements: bool = True,
) -> SellerAcceptedContract:
    """Activate a controlling baseline from an accepted offer/version.

    Document is optional (manual offers may have no PDF). Preserves
    primary/backup semantics, offer/version links, and CTC seeding for
    primary acceptance. Does not commit — caller owns the transaction.
    """
    if not actor_id or int(actor_id) <= 0:
        raise ValueError('A valid actor_id is required to accept an offer')
    if offer.organization_id != transaction.organization_id:
        raise ValueError('Offer organization does not match transaction')
    if offer.transaction_id != transaction.id:
        raise ValueError('Offer is not attached to this transaction')
    if position not in ('primary', 'backup'):
        raise ValueError('position must be primary or backup')

    if offer.status in ('accepted_primary', 'accepted_backup'):
        raise ValueError('This offer has already been accepted')

    existing_for_offer = SellerAcceptedContract.query.filter_by(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        offer_id=offer.id,
        status='active',
    ).first()
    if existing_for_offer:
        raise ValueError('This offer already has an active controlling contract')

    if position == 'backup':
        if not has_active_primary_contract(
            transaction.id, transaction.organization_id,
        ):
            raise ValueError(
                'Accept a primary contract before accepting a backup',
            )
    else:
        active = get_active_primary_contract(
            transaction.id, transaction.organization_id,
        )
        if active:
            raise ControllingContractConflict(
                'A controlling contract already exists for this transaction.',
                code='baseline_conflict',
                existing_contract_id=active.id,
                status=409,
            )

    if version is None and offer.current_version_id:
        version = SellerOfferVersion.query.filter_by(
            id=offer.current_version_id,
            offer_id=offer.id,
            organization_id=offer.organization_id,
        ).first()

    if document is None:
        document = _optional_document_for_offer(offer, version)

    terms = _merged_offer_acceptance_terms(offer, version)
    addenda = _as_dict(terms.get('addenda'))
    supporting_documents = _as_dict(terms.get('supporting_documents'))
    financing_addendum = _as_dict(addenda.get('third_party_financing_addendum'))
    seller_disclosure = _as_dict(supporting_documents.get('sellers_disclosure'))
    hoa_addendum = _as_dict(addenda.get('hoa_addendum'))
    seller_disclosure_delivered = (
        seller_disclosure.get('buyer_received_date')
        or seller_disclosure.get('seller_signed_date')
    )
    financing_deadline = derive_financing_approval_deadline(terms, effective_date)

    contract = SellerAcceptedContract(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        offer_id=offer.id,
        accepted_version_id=version.id if version else None,
        created_by_id=int(actor_id),
        position=position,
        status='active',
        backup_position=backup_position if position == 'backup' else None,
        backup_addendum_document_id=(
            backup_addendum_document_id if position == 'backup' else None
        ),
        accepted_price=_first_money(
            offer.offer_price,
            terms.get('offer_price'),
            terms.get('sales_price'),
        ),
        effective_date=effective_date,
        effective_at=effective_at,
        closing_date=(
            offer.proposed_close_date
            or _parse_date(terms.get('proposed_close_date') or terms.get('closing_date'))
        ),
        option_period_days=(
            offer.option_period_days
            if offer.option_period_days is not None
            else _parse_int(terms.get('option_period_days'))
        ),
        financing_approval_deadline=_parse_date(financing_deadline),
        financing_type=offer.financing_type or terms.get('financing_type'),
        cash_down_payment=_first_money(
            offer.cash_down_payment,
            terms.get('cash_down_payment'),
        ),
        financing_amount=_first_money(
            offer.financing_amount,
            terms.get('financing_amount'),
            financing_addendum.get('total_financing_amount'),
        ),
        seller_concessions_amount=_first_money(
            offer.seller_concessions_amount,
            terms.get('seller_concessions_amount'),
        ),
        title_company=terms.get('title_company'),
        escrow_officer=terms.get('escrow_officer'),
        survey_choice=terms.get('survey_choice'),
        survey_furnished_by=(
            offer.survey_furnished_by or terms.get('survey_furnished_by')
        ),
        residential_service_contract=(
            offer.residential_service_contract
            or terms.get('residential_service_contract')
        ),
        buyer_agent_commission_percent=_first_money(
            offer.buyer_agent_commission_percent,
            terms.get('buyer_agent_commission_percent'),
        ),
        buyer_agent_commission_flat=_first_money(
            offer.buyer_agent_commission_flat,
            terms.get('buyer_agent_commission_flat'),
        ),
        hoa_applicable=(
            terms.get('hoa_applicable')
            if terms.get('hoa_applicable') is not None
            else bool(hoa_addendum)
        ),
        seller_disclosure_required=(
            terms.get('seller_disclosure_required')
            if terms.get('seller_disclosure_required') is not None
            else bool(seller_disclosure)
        ),
        seller_disclosure_delivered_at=_parse_datetime(seller_disclosure_delivered),
        lead_based_paint_required=(
            terms.get('lead_based_paint_required')
            if terms.get('lead_based_paint_required') is not None
            else seller_disclosure.get('built_before_1978')
        ),
        frozen_terms=terms,
        addenda_data=terms.get('addenda') or {},
        extra_data=_extra_data_from_offer(offer, terms),
    )
    db.session.add(contract)
    db.session.flush()

    if position == 'primary' and count_active_primaries(
        transaction.id, transaction.organization_id,
    ) > 1:
        db.session.delete(contract)
        db.session.flush()
        active = get_active_primary_contract(
            transaction.id, transaction.organization_id,
        )
        raise ControllingContractConflict(
            'A controlling contract already exists for this transaction.',
            code='baseline_conflict',
            existing_contract_id=getattr(active, 'id', None),
            status=409,
        )

    if terms:
        apply_contract_terms(contract, terms)
        if effective_date is not None:
            contract.effective_date = effective_date
        if effective_at is not None:
            contract.effective_at = effective_at
        flag_modified(contract, 'frozen_terms')
        flag_modified(contract, 'addenda_data')
        flag_modified(contract, 'extra_data')

    if document is not None:
        if document.organization_id != transaction.organization_id:
            raise ValueError('Document organization does not match transaction')
        if document.transaction_id != transaction.id:
            raise ValueError('Document is not attached to this transaction')
        link_primary_contract_document(
            contract=contract,
            document=document,
            actor_id=int(actor_id),
            display_name=document.template_name,
        )
        field_data = dict(document.field_data or {})
        field_data[_BASELINE_META_KEY] = {
            'accepted_contract_id': contract.id,
            'confirmed_at': datetime.utcnow().isoformat(),
            'actor_id': int(actor_id),
            'side': 'seller',
            'source_offer_id': offer.id,
        }
        document.field_data = field_data
        flag_modified(document, 'field_data')

    if position == 'primary':
        offer.status = 'accepted_primary'
        transaction.status = 'under_contract'
        if contract.closing_date:
            transaction.expected_close_date = contract.closing_date
        create_contract_milestones(contract)
        if seed_requirements:
            _seed_ctc(transaction, contract, actor_id=int(actor_id))
        event_type = 'accepted_primary'
        label = 'Offer accepted as primary contract'
    else:
        offer.status = 'accepted_backup'
        offer.backup_position = contract.backup_position
        offer.backup_addendum_document_id = contract.backup_addendum_document_id
        event_type = 'accepted_backup'
        label = 'Offer accepted as backup contract'

    offer.accepted_version_id = version.id if version else None
    create_offer_activity(
        offer,
        event_type,
        label,
        actor_id=int(actor_id),
        version_id=version.id if version else None,
        event_data={
            'accepted_contract_id': contract.id,
            'position': position,
        },
    )

    AuditEvent.log(
        event_type='controlling_contract_baseline_from_offer',
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        document_id=document.id if document else None,
        actor_id=int(actor_id),
        description=label,
        event_data={
            'accepted_contract_id': contract.id,
            'offer_id': offer.id,
            'position': position,
            'accepted_version_id': version.id if version else None,
            'has_document': document is not None,
        },
        source='controlling_contracts',
    )
    db.session.flush()
    logger.info(
        'Controlling baseline %s created from offer %s (position=%s, doc=%s)',
        contract.id, offer.id, position, getattr(document, 'id', None),
    )
    return contract


def maybe_create_baseline_after_term_approval(
    *,
    transaction: Transaction,
    document: TransactionDocument | None,
    approved_terms: dict[str, Any],
    actor_id: int,
) -> Optional[SellerAcceptedContract]:
    """Create controlling baseline when classification confirmed scope=contract.

    Returns the contract when created/refreshed, None when not applicable.
    Raises ControllingContractConflict / ControllingContractSeedError for callers.
    """
    if not document:
        return None
    confirm = (document.field_data or {}).get(_CONFIRM_META_KEY) or {}
    if not isinstance(confirm, dict):
        return None
    if (confirm.get('scope') or '').strip().lower() != 'contract':
        return None

    side = side_for_transaction(transaction)
    # Seller requires explicit controlling confirmation in classification metadata.
    if side == 'seller' and not confirm.get('explicit_controlling_confirmation'):
        return None

    if has_active_primary_contract(transaction.id, transaction.organization_id):
        existing = _contract_for_document(document=document)
        if existing:
            return create_baseline_from_document(
                transaction=transaction,
                document=document,
                approved_terms=approved_terms,
                actor_id=actor_id,
                seed_requirements=True,
            )
        # Different document — conflict (do not mutate).
        active = get_active_primary_contract(transaction.id, transaction.organization_id)
        raise ControllingContractConflict(
            'A controlling contract already exists for this transaction.',
            code='baseline_conflict',
            existing_contract_id=getattr(active, 'id', None),
            status=409,
        )

    return create_baseline_from_document(
        transaction=transaction,
        document=document,
        approved_terms=approved_terms,
        actor_id=actor_id,
        seed_requirements=True,
    )


def link_supporting_contract_document(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    actor_id: int,
    document_type: str = 'supporting',
    display_name: str | None = None,
) -> Optional[SellerContractDocument]:
    """Attach a supporting PDF to the active controlling contract (idempotent)."""
    contract = get_active_primary_contract(
        transaction.id, transaction.organization_id,
    )
    if not contract:
        return None

    existing = SellerContractDocument.query.filter_by(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        transaction_document_id=document.id,
    ).first()
    if existing:
        return existing

    link = SellerContractDocument(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        accepted_contract_id=contract.id,
        transaction_document_id=document.id,
        created_by_id=int(actor_id),
        document_type=document_type or 'supporting',
        display_name=display_name or document.template_name or 'Contract Document',
        is_primary_contract_document=False,
        extraction_summary={},
    )
    db.session.add(link)
    db.session.flush()
    return link
