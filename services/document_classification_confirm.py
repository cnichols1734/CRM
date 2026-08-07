"""Confirm or correct extracted document identity and lifecycle destination.

Persists human confirmation under reserved field_data metadata, retags the
document to a canonical slug, attaches via scoped intake, and never applies
legal/financial terms from classification confirmation alone.

All kind/slug/scope/side compatibility is validated **before** mutating the
document so failed confirms leave no in-memory retag residue in service tests.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from flask import url_for
from sqlalchemy.orm.attributes import flag_modified

from models import (
    AuditEvent,
    Transaction,
    TransactionDocument,
    db,
)
from services.controlling_contracts import (
    get_active_primary_contract,
    has_active_primary_contract,
)
from services.document_classification_policy import (
    VALID_KINDS,
    ClassificationPolicyError,
    parse_strict_bool,
    validate_kind_slug_scope,
)
from services.document_identity import identity_for_slug
from services.offer_side import side_for_transaction
from services.requirement_evidence import auto_attach_for_document
from services.scoped_document_intake import (
    SCOPE_AMENDMENT,
    SCOPE_CONTRACT,
    SCOPE_OFFER,
    VALID_SCOPES,
    ScopedIntakeError,
    attach_document_to_scope,
    is_canonical_slug,
)

logger = logging.getLogger(__name__)

_CONFIRM_META_KEY = '_classification_confirmation'
_MODEL_IDENTITY_KEY = '_document_identity'


class ClassificationConfirmError(ValueError):
    def __init__(self, message: str, *, code: str = 'invalid_confirmation', status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def build_routing_context_payload(transaction: Transaction) -> dict[str, Any]:
    """Expose real transaction facts for classification / review workspace UI."""
    from models import SellerOffer, TransactionDocument as TxDoc

    side = side_for_transaction(transaction)
    has_primary = has_active_primary_contract(
        transaction.id, transaction.organization_id,
    )
    has_listing = TxDoc.query.filter_by(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
        template_slug='listing-agreement',
    ).first() is not None
    active_offers = (
        SellerOffer.query.filter_by(
            transaction_id=transaction.id,
            organization_id=transaction.organization_id,
        )
        .filter(SellerOffer.status.notin_(('withdrawn', 'expired', 'rejected')))
        .order_by(SellerOffer.id.desc())
        .limit(50)
        .all()
    )
    return {
        'transaction_id': transaction.id,
        'side': side,
        'status': transaction.status,
        'has_primary_contract': has_primary,
        'has_listing_agreement': has_listing,
        'active_offers': [
            {
                'id': o.id,
                'status': o.status,
                'buyer_names': o.buyer_names,
                'offer_price': float(o.offer_price) if o.offer_price is not None else None,
            }
            for o in active_offers
        ],
        'active_offer_ids': [o.id for o in active_offers],
    }


def _persist_confirmation_metadata(
    document: TransactionDocument,
    *,
    actor_id: int,
    kind: str,
    template_slug: str,
    scope: str,
    offer_id: int | None,
    create_new_offer: bool,
    explicit_controlling: bool,
) -> None:
    field_data = dict(document.field_data or {})
    # Never overwrite model observation — confirmation is stored separately.
    field_data[_CONFIRM_META_KEY] = {
        'confirmed_at': datetime.utcnow().isoformat(),
        'confirmed_by_id': int(actor_id),
        'kind': kind,
        'template_slug': template_slug,
        'scope': scope,
        'offer_id': offer_id,
        'create_new_offer': bool(create_new_offer),
        'explicit_controlling_confirmation': bool(explicit_controlling),
        'model_identity_snapshot': (
            field_data.get(_MODEL_IDENTITY_KEY)
            if isinstance(field_data.get(_MODEL_IDENTITY_KEY), dict)
            else None
        ),
    }
    document.field_data = field_data
    flag_modified(document, 'field_data')


def mark_auto_filed_offer_confirmation(
    document: TransactionDocument,
    *,
    actor_id: int,
    offer_id: int,
    template_slug: str | None = None,
    kind: str | None = None,
) -> None:
    """Record offer filing after inbound autofile so review UI skips Confirm filing."""
    existing = (document.field_data or {}).get(_CONFIRM_META_KEY) or {}
    if isinstance(existing, dict) and existing.get('scope') and existing.get('offer_id'):
        return
    identity = (document.field_data or {}).get(_MODEL_IDENTITY_KEY) or {}
    slug = (
        template_slug
        or (identity.get('template_slug') if isinstance(identity, dict) else None)
        or document.template_slug
        or 'seller-offer-contract'
    )
    resolved_kind = (
        kind
        or (identity.get('kind') if isinstance(identity, dict) else None)
        or 'purchase_contract'
    )
    _persist_confirmation_metadata(
        document,
        actor_id=int(actor_id),
        kind=str(resolved_kind),
        template_slug=str(slug),
        scope=SCOPE_OFFER,
        offer_id=int(offer_id),
        create_new_offer=False,
        explicit_controlling=False,
    )


def confirm_document_classification(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    actor_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Confirm identity + destination and attach to scope.

    Classification confirmation does **not** establish a controlling baseline
    from extracted terms. If scope is contract and no baseline exists, the
    document is retagged and left pending human term approval.
    """
    if (
        document.organization_id != transaction.organization_id
        or document.transaction_id != transaction.id
    ):
        raise ClassificationConfirmError(
            'Document not found.', code='not_found', status=404,
        )

    kind = str(payload.get('kind') or payload.get('document_kind') or '').strip().lower()
    template_slug = str(
        payload.get('template_slug') or payload.get('slug') or ''
    ).strip().lower()
    scope = str(payload.get('scope') or '').strip().lower()
    offer_id_raw = payload.get('offer_id')

    try:
        create_new_offer = parse_strict_bool(
            payload.get('create_new_offer'), field_name='create_new_offer',
        )
        explicit_controlling = parse_strict_bool(
            payload.get('explicit_controlling_confirmation'),
            field_name='explicit_controlling_confirmation',
        )
    except ClassificationPolicyError as exc:
        raise ClassificationConfirmError(
            str(exc), code=exc.code, status=exc.status,
        ) from exc

    if not template_slug or not is_canonical_slug(template_slug):
        raise ClassificationConfirmError(
            'Choose a supported document type.',
            code='invalid_slug',
        )
    if not kind:
        # Infer kind from slug when omitted — still validated for consistency.
        from services.document_classification_policy import kind_for_slug
        kind = kind_for_slug(template_slug) or 'other'
    if kind not in VALID_KINDS:
        raise ClassificationConfirmError(
            'Invalid document kind.',
            code='invalid_kind',
        )
    if scope not in VALID_SCOPES:
        raise ClassificationConfirmError(
            'Invalid scope. Choose listing, offer, contract, amendment, or other.',
            code='invalid_scope',
        )

    offer_id = None
    if offer_id_raw not in (None, '', 0, '0'):
        try:
            offer_id = int(offer_id_raw)
        except (TypeError, ValueError):
            raise ClassificationConfirmError(
                'Invalid offer.', code='invalid_offer',
            ) from None

    has_primary = has_active_primary_contract(
        transaction.id, transaction.organization_id,
    )

    # Full compatibility check BEFORE any mutation.
    try:
        validate_kind_slug_scope(
            kind=kind,
            template_slug=template_slug,
            scope=scope,
            transaction=transaction,
            has_primary_contract=has_primary,
            explicit_controlling_confirmation=explicit_controlling,
        )
    except ClassificationPolicyError as exc:
        raise ClassificationConfirmError(
            str(exc), code=exc.code, status=exc.status,
        ) from exc

    if scope == SCOPE_OFFER and not offer_id and not create_new_offer:
        raise ClassificationConfirmError(
            'Select an existing offer or confirm create_new_offer.',
            code='offer_unconfirmed',
        )

    # Idempotent retry: same confirmation already applied — no new links/audits.
    prior = (document.field_data or {}).get(_CONFIRM_META_KEY) or {}
    if (
        isinstance(prior, dict)
        and prior.get('template_slug') == template_slug
        and prior.get('scope') == scope
        and bool(prior.get('create_new_offer')) == create_new_offer
        and prior.get('confirmed_by_id')
        and (
            prior.get('offer_id') == offer_id
            or (create_new_offer and prior.get('offer_id') and offer_id is None)
        )
    ):
        resolved_offer_id = prior.get('offer_id') or offer_id
        if resolved_offer_id is None and scope == SCOPE_OFFER:
            from models import SellerOfferDocument
            link = SellerOfferDocument.query.filter_by(
                organization_id=transaction.organization_id,
                transaction_id=transaction.id,
                transaction_document_id=document.id,
            ).first()
            if link:
                resolved_offer_id = link.offer_id
        return _success_payload(
            transaction=transaction,
            document=document,
            scope=scope,
            attachment={
                'idempotent': True,
                'scope': scope,
                'offer_id': resolved_offer_id,
                'accepted_contract_id': (
                    get_active_primary_contract(
                        transaction.id, transaction.organization_id,
                    ).id
                    if scope == SCOPE_CONTRACT and has_primary
                    else None
                ),
            },
            amendment_id=None,
        )

    previous_slug = document.template_slug
    document.template_slug = template_slug
    if payload.get('template_name'):
        document.template_name = str(payload.get('template_name'))[:200]
    elif not document.template_name:
        inferred = identity_for_slug(template_slug)
        if inferred and inferred.label:
            document.template_name = inferred.label

    _persist_confirmation_metadata(
        document,
        actor_id=actor_id,
        kind=kind,
        template_slug=template_slug,
        scope=scope,
        offer_id=offer_id,
        create_new_offer=create_new_offer,
        explicit_controlling=explicit_controlling,
    )

    try:
        attachment = attach_document_to_scope(
            transaction=transaction,
            document=document,
            actor_id=actor_id,
            scope=scope,
            offer_id=offer_id,
            create_new_offer=create_new_offer,
            template_slug=template_slug,
            template_name=document.template_name,
            as_primary_contract_doc=False,  # never promote primary via classify alone
        )
    except ScopedIntakeError as exc:
        if scope == SCOPE_CONTRACT and exc.code == 'no_controlling_contract':
            attachment = {
                'scope': SCOPE_CONTRACT,
                'document_id': document.id,
                'template_slug': document.template_slug,
                'offer_id': None,
                'accepted_contract_id': None,
                'created': False,
                'baseline_pending_term_approval': True,
            }
        else:
            raise ClassificationConfirmError(
                str(exc), code=exc.code, status=400,
            ) from exc

    if attachment.get('offer_id') and isinstance(document.field_data, dict):
        confirm_meta = dict(document.field_data.get(_CONFIRM_META_KEY) or {})
        confirm_meta['offer_id'] = attachment.get('offer_id')
        field_data = dict(document.field_data)
        field_data[_CONFIRM_META_KEY] = confirm_meta
        document.field_data = field_data
        flag_modified(document, 'field_data')

    amendment_id = None
    if scope == SCOPE_AMENDMENT:
        from services.amendment_service import create_from_document
        visible_keys = [
            k for k in (document.field_data or {})
            if not str(k).startswith('_')
        ]
        if visible_keys and get_active_primary_contract(
            transaction.id, transaction.organization_id,
        ):
            amendment = create_from_document(document, actor_id=actor_id)
            if amendment:
                amendment_id = amendment.id
                attachment['amendment_id'] = amendment_id

    if previous_slug != document.template_slug:
        try:
            auto_attach_for_document(document)
        except Exception:
            logger.exception(
                'Evidence re-attach failed after classification confirm doc=%s',
                document.id,
            )

    try:
        from services.checklist_service import absorb_matching_placeholder
        absorb_matching_placeholder(document, actor_id=actor_id)
    except Exception:
        logger.exception(
            'Placeholder absorb failed after classification confirm doc=%s',
            document.id,
        )

    # Skip duplicate audit on pure idempotent path (handled above).
    AuditEvent.log(
        event_type='document_classification_confirmed',
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        document_id=document.id,
        actor_id=actor_id,
        description='Document classification confirmed',
        event_data={
            'kind': kind,
            'template_slug': template_slug,
            'scope': scope,
            'offer_id': attachment.get('offer_id'),
            'previous_slug': previous_slug,
            'amendment_id': amendment_id,
            'baseline_pending_term_approval': attachment.get(
                'baseline_pending_term_approval', False,
            ),
        },
        source='document_classification_confirm',
    )
    db.session.flush()

    return _success_payload(
        transaction=transaction,
        document=document,
        scope=scope,
        attachment=attachment,
        amendment_id=amendment_id,
    )


def _success_payload(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    scope: str,
    attachment: dict[str, Any],
    amendment_id: int | None,
) -> dict[str, Any]:
    next_url = url_for(
        'transactions.document_review_workspace',
        id=transaction.id,
        doc_id=document.id,
    )
    if amendment_id:
        next_url = url_for(
            'transactions.amendment_review',
            id=transaction.id,
            amendment_id=amendment_id,
        )
    elif scope == SCOPE_OFFER and attachment.get('offer_id'):
        next_url = url_for(
            'transactions.view_transaction',
            id=transaction.id,
        ) + f'#offer-{attachment["offer_id"]}'

    return {
        'success': True,
        'document_id': document.id,
        'template_slug': document.template_slug,
        'scope': scope,
        'offer_id': attachment.get('offer_id'),
        'accepted_contract_id': attachment.get('accepted_contract_id'),
        'amendment_id': amendment_id,
        'baseline_pending_term_approval': bool(
            attachment.get('baseline_pending_term_approval')
        ),
        'idempotent': bool(attachment.get('idempotent')),
        'next_url': next_url,
        'routing_context': build_routing_context_payload(transaction),
    }
