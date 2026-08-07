"""Pure routing policy for uploaded transaction documents.

Consumes document identity + representation side + optional transaction
context. Returns an explicit route decision.

Representation may come from the agent or from deterministic document
signals (listing agreement → seller; Broker Information block match). AI
hints never invent a side when the form is silent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

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
    KIND_PROOF_OF_FUNDS,
    KIND_PURCHASE_CONTRACT,
    KIND_UNKNOWN,
    SCOPE_AMENDMENT,
    SCOPE_CONTRACT,
    SCOPE_LISTING,
    SCOPE_OFFER,
    DocumentIdentity,
)

ACTION_CREATE_LISTING = 'create_or_match_listing'
ACTION_ATTACH_LISTING_DOC = 'attach_listing_document'
ACTION_CREATE_INBOUND_OFFER = 'create_or_attach_inbound_offer'
ACTION_CREATE_BUYER_OFFER = 'create_or_attach_buyer_offer'
ACTION_ATTACH_CONTROLLING_CONTRACT = 'attach_controlling_contract'
ACTION_CREATE_AMENDMENT = 'create_amendment_review'
ACTION_ATTACH_SUPPORTING = 'attach_supporting_document'
ACTION_NEEDS_CONFIRMATION = 'needs_confirmation'
ACTION_INVALID = 'invalid'

VALID_SIDES = frozenset({'buyer', 'seller', 'landlord', 'tenant'})

# Addenda that belong to an offer/contract package, not the listing shell.
_CONTRACT_PACKAGE_ADDENDA = frozenset({
    'third_party_financing',
    'hoa',
    'backup',
    'pre_approval',
})


@dataclass(frozen=True)
class TransactionContext:
    """Minimal transaction facts for routing (no ORM dependency)."""

    transaction_id: Optional[int] = None
    side: Optional[str] = None
    status: Optional[str] = None
    has_primary_contract: bool = False
    has_listing_agreement: bool = False
    offer_id: Optional[int] = None
    is_offer_scoped_upload: bool = False
    active_offer_ids: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['active_offer_ids'] = list(self.active_offer_ids)
        return data


@dataclass(frozen=True)
class RouteDecision:
    """Explicit route for an uploaded document."""

    action: str
    destination_scope: Optional[str] = None
    template_slug: Optional[str] = None
    template_name: Optional[str] = None
    transaction_status: Optional[str] = None
    seed_pack_key: Optional[str] = None
    reason: str = ''
    needs_confirmation: bool = False
    confirmation_options: tuple[str, ...] = ()
    error_code: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.action != ACTION_INVALID

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['confirmation_options'] = list(self.confirmation_options)
        data['ok'] = self.ok
        return data


def _normalize_side(side: str | None) -> str:
    return (side or '').strip().lower()


def decide_route(
    *,
    identity: DocumentIdentity,
    representation_side: str | None,
    side_confirmed: bool = True,
    transaction: TransactionContext | None = None,
    destination_choice: str | None = None,
) -> RouteDecision:
    """Return the route for identity + confirmed side + optional tx context.

    Scenarios A–E are encoded explicitly. Ambiguity and side mismatch return
    needs_confirmation or invalid — never a silent guess.
    """
    side = _normalize_side(representation_side)
    tx = transaction or TransactionContext()
    choice = (destination_choice or '').strip().lower() or None

    # Deterministic form-type side inference. Some Texas forms only exist for
    # one side of the deal (TXR-1101 listing → seller). Purchase contracts are
    # two-sided; their side is resolved upstream from the Broker Information
    # block before decide_route is called.
    if (
        (not side_confirmed or side not in VALID_SIDES)
        and identity.kind == KIND_LISTING_AGREEMENT
        and not identity.ambiguous
        and identity.confidence >= HIGH_CONFIDENCE
    ):
        side = 'seller'
        side_confirmed = True

    if not side_confirmed or side not in VALID_SIDES:
        return RouteDecision(
            action=ACTION_NEEDS_CONFIRMATION,
            reason=(
                'This document does not clearly show which side you represent. '
                'Confirm buyer or seller before filing.'
            ),
            needs_confirmation=True,
            confirmation_options=('buyer', 'seller'),
            error_code='side_unconfirmed',
        )

    if identity.ambiguous or (
        identity.kind == KIND_UNKNOWN and identity.confidence < HIGH_CONFIDENCE
    ):
        return RouteDecision(
            action=ACTION_NEEDS_CONFIRMATION,
            reason='Document type is ambiguous. Confirm what this PDF is before applying it.',
            needs_confirmation=True,
            confirmation_options=(
                KIND_LISTING_AGREEMENT,
                KIND_PURCHASE_CONTRACT,
                KIND_ADDENDUM,
                KIND_AMENDMENT,
                KIND_DISCLOSURE,
                'other',
            ),
            error_code='identity_ambiguous',
            metadata={'identity': identity.to_dict()},
        )

    # --- A. Seller listing agreement ---
    if identity.kind == KIND_LISTING_AGREEMENT:
        if side != 'seller':
            return RouteDecision(
                action=ACTION_INVALID,
                reason=(
                    'A listing agreement belongs on a seller listing. '
                    'You indicated buyer representation — this document will not be applied.'
                ),
                error_code='side_mismatch_listing',
                metadata={'expected_side': 'seller', 'confirmed_side': side},
            )
        return RouteDecision(
            action=(
                ACTION_ATTACH_LISTING_DOC
                if tx.transaction_id
                else ACTION_CREATE_LISTING
            ),
            destination_scope=SCOPE_LISTING,
            template_slug=identity.template_slug or 'listing-agreement',
            template_name=identity.label or 'Listing Agreement',
            transaction_status='preparing_to_list',
            seed_pack_key='listing',
            reason='Seller listing agreement routes to a listing transaction (not under contract).',
            metadata={'form_number': identity.form_number},
        )

    # --- E. Amendment (post-execution) ---
    if identity.kind == KIND_AMENDMENT:
        if not identity.is_high_confidence and identity.confidence < HIGH_CONFIDENCE:
            return RouteDecision(
                action=ACTION_NEEDS_CONFIRMATION,
                reason='Confirm this is a contract amendment before opening amendment review.',
                needs_confirmation=True,
                confirmation_options=(KIND_AMENDMENT, KIND_ADDENDUM, 'other'),
                error_code='amendment_low_confidence',
            )
        # Amendments require a matched transaction AND an active controlling contract.
        if not tx.transaction_id or not tx.has_primary_contract:
            return RouteDecision(
                action=ACTION_NEEDS_CONFIRMATION,
                reason=(
                    'Amendments require a matched transaction with an active controlling '
                    'contract. Match the file to the deal first, or attach it as supporting.'
                ),
                needs_confirmation=True,
                confirmation_options=('attach_supporting', 'cancel'),
                error_code='amendment_requires_controlling_contract',
                destination_scope=SCOPE_AMENDMENT,
                template_slug=identity.template_slug or 'amendment',
                template_name=identity.label or 'Amendment',
            )
        # Buyer and seller both use the side-neutral controlling-contract
        # amendment path once a primary baseline exists.
        return RouteDecision(
            action=ACTION_CREATE_AMENDMENT,
            destination_scope=SCOPE_AMENDMENT,
            template_slug=identity.template_slug or 'amendment',
            template_name=identity.label or 'Amendment',
            reason=(
                'High-confidence amendment opens side-by-side amendment review '
                f'on the {side} controlling contract.'
            ),
            metadata={'representation_side': side},
        )

    # --- B/C. Purchase contract ---
    if identity.kind == KIND_PURCHASE_CONTRACT:
        slug = identity.template_slug or (
            'seller-offer-contract' if side == 'seller' else 'purchase-contract'
        )
        label = identity.label or 'Purchase Contract'

        if side == 'seller':
            # C: inbound offer under listing — never silently controlling.
            if tx.is_offer_scoped_upload or tx.offer_id:
                return RouteDecision(
                    action=ACTION_CREATE_INBOUND_OFFER,
                    destination_scope=SCOPE_OFFER,
                    template_slug='seller-offer-contract',
                    template_name=label,
                    reason='Offer-scoped purchase contract stays on the offer thread.',
                    metadata={'offer_id': tx.offer_id},
                )
            return RouteDecision(
                action=ACTION_CREATE_INBOUND_OFFER,
                destination_scope=SCOPE_OFFER,
                template_slug='seller-offer-contract',
                template_name=label,
                transaction_status=None,  # do not force under_contract
                reason=(
                    'Seller-side purchase contract becomes an inbound offer thread; '
                    'it does not overwrite the listing agreement or become controlling '
                    'until the offer is accepted.'
                ),
            )

        if side == 'buyer':
            executed = identity.execution_state == EXEC_EXECUTED
            party_signed = identity.execution_state == EXEC_PARTY_SIGNED
            draft = identity.execution_state in (EXEC_DRAFT, EXEC_UNKNOWN)

            if choice == 'controlling_contract' or (executed and choice is None):
                if draft and choice is None:
                    pass  # fall through
                else:
                    return RouteDecision(
                        action=ACTION_ATTACH_CONTROLLING_CONTRACT,
                        destination_scope=SCOPE_CONTRACT,
                        template_slug=slug if 'contract' in (slug or '') else 'purchase-contract',
                        template_name=label,
                        transaction_status='under_contract',
                        seed_pack_key='buyer_ctc',
                        reason='Buyer executed contract becomes the controlling contract-to-close baseline.',
                        metadata={'execution_state': identity.execution_state},
                    )

            if choice == 'offer_thread' or party_signed or draft:
                if choice is None and identity.execution_state == EXEC_UNKNOWN:
                    return RouteDecision(
                        action=ACTION_NEEDS_CONFIRMATION,
                        destination_scope=SCOPE_OFFER,
                        template_slug=slug,
                        template_name=label,
                        reason=(
                            'Buyer purchase contract execution is unclear. '
                            'Choose offer thread or controlling contract.'
                        ),
                        needs_confirmation=True,
                        confirmation_options=('offer_thread', 'controlling_contract'),
                        error_code='buyer_destination_unconfirmed',
                        metadata={'execution_state': identity.execution_state},
                    )
                return RouteDecision(
                    action=ACTION_CREATE_BUYER_OFFER,
                    destination_scope=SCOPE_OFFER,
                    template_slug=slug,
                    template_name=label,
                    transaction_status='showing',
                    reason='Buyer proposed / partially signed contract attaches to an offer thread.',
                    metadata={'execution_state': identity.execution_state},
                )

            return RouteDecision(
                action=ACTION_ATTACH_CONTROLLING_CONTRACT,
                destination_scope=SCOPE_CONTRACT,
                template_slug=slug,
                template_name=label,
                transaction_status='under_contract',
                seed_pack_key='buyer_ctc',
                reason='Buyer purchase contract treated as controlling baseline.',
            )

    # --- D. Addenda / disclosures / POF ---
    if identity.kind in (KIND_ADDENDUM, KIND_DISCLOSURE, KIND_PROOF_OF_FUNDS):
        scopes = tuple(identity.possible_scopes or (SCOPE_OFFER, SCOPE_CONTRACT, SCOPE_LISTING))
        addendum_key = (identity.addendum_key or '').strip().lower()

        if tx.is_offer_scoped_upload or tx.offer_id:
            return RouteDecision(
                action=ACTION_ATTACH_SUPPORTING,
                destination_scope=SCOPE_OFFER,
                template_slug=identity.template_slug,
                template_name=identity.label,
                reason='Supporting document linked to the selected offer.',
                metadata={'addendum_key': identity.addendum_key, 'offer_id': tx.offer_id},
            )

        if tx.has_primary_contract or (tx.status or '') == 'under_contract':
            return RouteDecision(
                action=ACTION_ATTACH_SUPPORTING,
                destination_scope=SCOPE_CONTRACT,
                template_slug=identity.template_slug,
                template_name=identity.label,
                reason='Supporting document linked to the controlling contract.',
                metadata={'addendum_key': identity.addendum_key},
            )

        # Contract-package addenda (TPF, HOA, backup) on a listing without a
        # selected offer are not uniquely destinationed — do not invent an offer.
        contract_package_form = (
            addendum_key in _CONTRACT_PACKAGE_ADDENDA
            or SCOPE_LISTING not in scopes
        )
        if side == 'seller' and contract_package_form and tx.transaction_id:
            options: list[str] = []
            if tx.active_offer_ids:
                options.extend(f'offer:{oid}' for oid in tx.active_offer_ids)
            options.append('new_offer')
            if SCOPE_LISTING in scopes:
                options.append(SCOPE_LISTING)
            options.append('cancel')
            return RouteDecision(
                action=ACTION_NEEDS_CONFIRMATION,
                reason=(
                    'This supporting form belongs to an offer or contract package. '
                    'Choose which active offer it belongs to, or start a new offer thread.'
                ),
                needs_confirmation=True,
                confirmation_options=tuple(options),
                error_code='supporting_offer_unconfirmed',
                template_slug=identity.template_slug,
                template_name=identity.label,
                metadata={
                    'addendum_key': identity.addendum_key,
                    'active_offer_ids': list(tx.active_offer_ids),
                },
            )

        if side == 'seller' and SCOPE_LISTING in scopes and (
            tx.has_listing_agreement
            or (tx.status or '') in ('preparing_to_list', 'active', 'showing', '')
            or not tx.transaction_id
        ):
            return RouteDecision(
                action=ACTION_ATTACH_SUPPORTING,
                destination_scope=SCOPE_LISTING,
                template_slug=identity.template_slug,
                template_name=identity.label,
                reason='Supporting document linked to the listing package.',
                metadata={'addendum_key': identity.addendum_key},
            )

        if len(scopes) > 1:
            return RouteDecision(
                action=ACTION_NEEDS_CONFIRMATION,
                reason='Confirm whether this supporting form belongs to a listing, offer, or contract.',
                needs_confirmation=True,
                confirmation_options=scopes,
                error_code='supporting_scope_unconfirmed',
                template_slug=identity.template_slug,
                template_name=identity.label,
            )

        return RouteDecision(
            action=ACTION_ATTACH_SUPPORTING,
            destination_scope=scopes[0],
            template_slug=identity.template_slug,
            template_name=identity.label,
            reason=f'Supporting document linked to {scopes[0]} scope.',
            metadata={'addendum_key': identity.addendum_key},
        )

    return RouteDecision(
        action=ACTION_NEEDS_CONFIRMATION,
        reason='Could not determine a safe destination for this document.',
        needs_confirmation=True,
        confirmation_options=(
            KIND_LISTING_AGREEMENT,
            KIND_PURCHASE_CONTRACT,
            KIND_AMENDMENT,
            'other',
        ),
        error_code='unroutable',
        metadata={'identity': identity.to_dict()},
    )
