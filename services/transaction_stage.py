"""Whole-transaction stage: where the deal is, and what matters now.

Derives a stage from signals the detail view already has — status, listing
agreement presence, open offers, and an active primary contract. No stage
column and no extra queries when callers pass what they already loaded.

Distinct from ``TransactionRequirement.phase_key``, which is per-requirement
inside a deadline pack.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Sequence, Union

# Closing is under_contract plus a closing date this many days out (or overdue).
CLOSING_WINDOW_DAYS = 14

# Active offer statuses — mirrors routes/transactions/crud.py active_seller_offers.
OPEN_OFFER_STATUSES = frozenset({'new', 'reviewing', 'needs_review', 'countered'})

SURFACE_PRIMARY = 'primary'
SURFACE_SECONDARY = 'secondary'
SURFACE_HIDDEN = 'hidden'
SURFACE_LEVELS = frozenset({SURFACE_PRIMARY, SURFACE_SECONDARY, SURFACE_HIDDEN})

# Ordered progress keys (cancelled is terminal and sits outside the bar).
SELLER_STAGE_KEYS: tuple[str, ...] = (
    'prelisting',
    'listed',
    'offers',
    'under_contract',
    'closing',
    'closed',
)
BUYER_STAGE_KEYS: tuple[str, ...] = (
    'searching',
    'offers',
    'under_contract',
    'closing',
    'closed',
)

# Landlord / tenant / referral: deliberately simple status mirrors — these sides
# have no offer/contract pipeline, so we do not force them into the seller model.
_LEASE_LANDLORD_KEYS: tuple[str, ...] = (
    'preparing',
    'listed',
    'lease_pending',
    'leased',
)
_LEASE_TENANT_KEYS: tuple[str, ...] = (
    'searching',
    'application',
    'leased',
)
_REFERRAL_KEYS: tuple[str, ...] = (
    'new',
    'referred',
    'in_progress',
    'closed',
)

_LABELS: dict[str, str] = {
    'prelisting': 'Pre-listing',
    'listed': 'Listed',
    'offers': 'Offers',
    'under_contract': 'Under contract',
    'closing': 'Closing',
    'closed': 'Closed',
    'cancelled': 'Cancelled',
    'searching': 'Searching',
    'preparing': 'Preparing',
    'lease_pending': 'Lease pending',
    'leased': 'Leased',
    'application': 'Application',
    'new': 'New',
    'referred': 'Referred',
    'in_progress': 'In progress',
}

_SUMMARIES: dict[str, dict[str, str]] = {
    'seller': {
        'prelisting': 'Get the listing agreement signed and listing details locked before you go live.',
        'listed': 'Keep showings moving and the listing package complete while you wait on offers.',
        'offers': 'Compare terms, deadlines, and net — then accept, counter, or decline.',
        'under_contract': 'Track option, financing, and title deadlines against the executed contract.',
        'closing': 'Close is inside two weeks — clear the remaining checklist items and docs.',
        'closed': 'File is closed. Keep records handy for the audit trail.',
        'cancelled': 'This listing was cancelled. Archive what you need and move on.',
    },
    'buyer': {
        'searching': 'Log showings and be ready to submit when the right property hits.',
        'offers': 'Watch response deadlines and counters on every offer you have out.',
        'under_contract': 'Option period, financing, and inspection dates drive the week.',
        'closing': 'Closing is inside two weeks — finish buyer checklist items and docs.',
        'closed': 'Purchase closed. Keep the file for your records.',
        'cancelled': 'This buyer file was cancelled.',
    },
    'landlord': {
        'preparing': 'Finish the lease listing package before you market the unit.',
        'listed': 'Keep the rental marketed and ready for applications.',
        'lease_pending': 'Push the lease and screening items across the finish line.',
        'leased': 'Unit is leased. Keep the file for records.',
        'cancelled': 'This landlord file was cancelled.',
    },
    'tenant': {
        'searching': 'Track rentals under consideration and application deadlines.',
        'application': 'Work the application and lease documents to signature.',
        'leased': 'Lease is signed. Keep the file for records.',
        'cancelled': 'This tenant file was cancelled.',
    },
    'referral': {
        'new': 'Capture the referral details and handoff contacts.',
        'referred': 'Stay in touch until the receiving agent confirms progress.',
        'in_progress': 'Track the referred deal until it closes or pays.',
        'closed': 'Referral closed. Confirm payment and file it away.',
        'cancelled': 'This referral was cancelled.',
    },
}

# Per-stage surface relevance. Callers should still keep a surface visible when
# real data already exists — this map only encodes stage intent.
_SURFACES_BY_STAGE: dict[str, dict[str, str]] = {
    'prelisting': {
        'listing_workspace': SURFACE_PRIMARY,
        'listing_info': SURFACE_PRIMARY,
        'questionnaire': SURFACE_PRIMARY,
        'property_details': SURFACE_PRIMARY,
        'checklist': SURFACE_SECONDARY,
        'documents': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract': SURFACE_HIDDEN,
        'contract_terms': SURFACE_HIDDEN,
        'amendments': SURFACE_HIDDEN,
    },
    'listed': {
        'listing_workspace': SURFACE_PRIMARY,
        'listing_info': SURFACE_PRIMARY,
        'property_details': SURFACE_PRIMARY,
        'checklist': SURFACE_SECONDARY,
        'documents': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract': SURFACE_HIDDEN,
        'contract_terms': SURFACE_HIDDEN,
        'amendments': SURFACE_HIDDEN,
    },
    'searching': {
        'property_details': SURFACE_PRIMARY,
        'property_intelligence': SURFACE_PRIMARY,
        'checklist': SURFACE_SECONDARY,
        'documents': SURFACE_SECONDARY,
        'offers': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'contract': SURFACE_HIDDEN,
        'contract_terms': SURFACE_HIDDEN,
        'amendments': SURFACE_HIDDEN,
    },
    'offers': {
        'offers': SURFACE_PRIMARY,
        'checklist': SURFACE_SECONDARY,
        'documents': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        # No executed contract at this stage — empty Contract tab stays hidden.
        'contract': SURFACE_HIDDEN,
        'contract_terms': SURFACE_HIDDEN,
        'amendments': SURFACE_HIDDEN,
    },
    'under_contract': {
        'contract': SURFACE_PRIMARY,
        'contract_terms': SURFACE_PRIMARY,
        'checklist': SURFACE_PRIMARY,
        'documents': SURFACE_SECONDARY,
        'amendments': SURFACE_SECONDARY,
        'offers': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
    },
    'closing': {
        'checklist': SURFACE_PRIMARY,
        'documents': SURFACE_PRIMARY,
        'contract': SURFACE_PRIMARY,
        'contract_terms': SURFACE_PRIMARY,
        'amendments': SURFACE_SECONDARY,
        'offers': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        # Marketing-oriented surfaces drop once closing is the job.
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
    },
    'closed': {
        'activity': SURFACE_PRIMARY,
        'documents': SURFACE_SECONDARY,
        'checklist': SURFACE_SECONDARY,
        'contract': SURFACE_SECONDARY,
        'contract_terms': SURFACE_SECONDARY,
        'amendments': SURFACE_SECONDARY,
        'offers': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
    },
    'cancelled': {
        'activity': SURFACE_PRIMARY,
        'documents': SURFACE_SECONDARY,
        'checklist': SURFACE_SECONDARY,
        'contract': SURFACE_SECONDARY,
        'contract_terms': SURFACE_SECONDARY,
        'amendments': SURFACE_SECONDARY,
        'offers': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
    },
    # Simple sides reuse a quiet default map keyed below via aliases.
    'preparing': {
        'listing_workspace': SURFACE_PRIMARY,
        'listing_info': SURFACE_PRIMARY,
        'questionnaire': SURFACE_PRIMARY,
        'checklist': SURFACE_SECONDARY,
        'documents': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract': SURFACE_HIDDEN,
        'contract_terms': SURFACE_HIDDEN,
        'amendments': SURFACE_HIDDEN,
    },
    'lease_pending': {
        'checklist': SURFACE_PRIMARY,
        'documents': SURFACE_PRIMARY,
        'contract': SURFACE_PRIMARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract_terms': SURFACE_SECONDARY,
        'amendments': SURFACE_SECONDARY,
    },
    'leased': {
        'documents': SURFACE_SECONDARY,
        'checklist': SURFACE_SECONDARY,
        'contract': SURFACE_SECONDARY,
        'activity': SURFACE_PRIMARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract_terms': SURFACE_SECONDARY,
        'amendments': SURFACE_SECONDARY,
    },
    'application': {
        'checklist': SURFACE_PRIMARY,
        'documents': SURFACE_PRIMARY,
        'contract': SURFACE_PRIMARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract_terms': SURFACE_SECONDARY,
        'amendments': SURFACE_SECONDARY,
    },
    'new': {
        'participants': SURFACE_PRIMARY,
        'questionnaire': SURFACE_PRIMARY,
        'checklist': SURFACE_SECONDARY,
        'documents': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'activity': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract': SURFACE_HIDDEN,
        'contract_terms': SURFACE_HIDDEN,
        'amendments': SURFACE_HIDDEN,
    },
    'referred': {
        'participants': SURFACE_PRIMARY,
        'activity': SURFACE_PRIMARY,
        'checklist': SURFACE_SECONDARY,
        'documents': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract': SURFACE_HIDDEN,
        'contract_terms': SURFACE_HIDDEN,
        'amendments': SURFACE_HIDDEN,
    },
    'in_progress': {
        'activity': SURFACE_PRIMARY,
        'checklist': SURFACE_SECONDARY,
        'documents': SURFACE_SECONDARY,
        'participants': SURFACE_SECONDARY,
        'tasks': SURFACE_SECONDARY,
        'bob_review': SURFACE_SECONDARY,
        'client_portal': SURFACE_SECONDARY,
        'questionnaire': SURFACE_SECONDARY,
        'property_details': SURFACE_SECONDARY,
        'property_intelligence': SURFACE_SECONDARY,
        'listing_workspace': SURFACE_SECONDARY,
        'listing_info': SURFACE_SECONDARY,
        'offers': SURFACE_HIDDEN,
        'contract': SURFACE_SECONDARY,
        'contract_terms': SURFACE_SECONDARY,
        'amendments': SURFACE_SECONDARY,
    },
}


@dataclass(frozen=True)
class TransactionStage:
    """Derived whole-transaction stage for progress UI and surface relevance."""

    key: str
    label: str
    index: int
    total: int
    summary: str
    is_terminal: bool
    side: str = 'seller'


def stage_keys_for_side(side: str) -> tuple[str, ...]:
    """Ordered non-cancelled progress keys for a transaction side."""
    side_norm = (side or 'seller').strip().lower()
    if side_norm == 'buyer':
        return BUYER_STAGE_KEYS
    if side_norm == 'landlord':
        return _LEASE_LANDLORD_KEYS
    if side_norm == 'tenant':
        return _LEASE_TENANT_KEYS
    if side_norm == 'referral':
        return _REFERRAL_KEYS
    return SELLER_STAGE_KEYS


def side_for_stage(transaction) -> str:
    """Return transaction type name (seller/buyer/landlord/tenant/referral)."""
    tx_type = getattr(transaction, 'transaction_type', None)
    name = (getattr(tx_type, 'name', None) or '').strip().lower()
    if name in ('seller', 'buyer', 'landlord', 'tenant', 'referral'):
        return name
    return 'seller'


def stage_for_transaction(
    transaction,
    *,
    has_listing_agreement: Optional[bool] = None,
    open_offers: Union[int, Sequence[Any], None] = None,
    primary_contract: Any = None,
    today: Optional[date] = None,
) -> TransactionStage:
    """Derive the whole-transaction stage from existing signals.

    ``has_listing_agreement`` is accepted as an optional argument (not computed
    here). In ``view_transaction`` it is already ``True`` when any loaded
    document has ``template_slug == 'listing-agreement'``. Re-deriving that
    would mean another documents query or re-scanning a list this module should
    not own — callers pass the bool they already have.

    ``open_offers`` may be a count or a sequence of offer rows (filtered or not).
    ``primary_contract`` should be the active primary ``SellerAcceptedContract``
    when the caller already loaded it (the model backs buyer and seller despite
    the name). When omitted, this function assumes no contract / no offers —
    it does not query. Detail-page worst case: **0 extra queries**.
    """
    side = side_for_stage(transaction)
    status = (getattr(transaction, 'status', None) or '').strip().lower()
    as_of = today or date.today()
    keys = stage_keys_for_side(side)

    if status == 'cancelled':
        return _build_stage(side, 'cancelled', keys, is_terminal=True)

    # Lease / referral: status-only mapping — no offer/contract outranking.
    # Handled before the closed early-return because these sides name their
    # terminal stage differently ("leased"), and a key outside their list would
    # fall back to index 0 and render as stage 1 of N.
    if side in ('landlord', 'tenant', 'referral'):
        return _simple_side_stage(side, status, keys)

    if status == 'closed':
        return _build_stage(side, 'closed', keys, is_terminal=True)

    has_contract = _is_active_primary_contract(primary_contract)
    # Active primary contract outranks a stale status string (same rule as
    # DeadlineRulesService.resolve_pack_for_transaction).
    effectively_under_contract = has_contract or status in (
        'under_contract',
        'pending',
    )

    if effectively_under_contract:
        closing_date = _resolve_closing_date(transaction, primary_contract)
        if _in_closing_window(closing_date, as_of):
            return _build_stage(side, 'closing', keys)
        return _build_stage(side, 'under_contract', keys)

    offer_count = _count_open_offers(open_offers)
    if offer_count > 0:
        return _build_stage(side, 'offers', keys)

    if side == 'buyer':
        return _build_stage(side, 'searching', keys)

    # Seller prelisting vs listed: listing agreement on file wins over status
    # "active" with nothing signed yet.
    if has_listing_agreement:
        return _build_stage(side, 'listed', keys)
    return _build_stage(side, 'prelisting', keys)


def relevant_surfaces(stage: TransactionStage) -> dict[str, str]:
    """Map surface keys to primary / secondary / hidden for this stage."""
    mapping = _SURFACES_BY_STAGE.get(stage.key) or {}
    return dict(mapping)


def surface_visibility(stage: TransactionStage, key: str) -> str:
    """Visibility for one surface; unknown keys default to secondary."""
    level = relevant_surfaces(stage).get(key, SURFACE_SECONDARY)
    if level not in SURFACE_LEVELS:
        return SURFACE_SECONDARY
    return level


def _build_stage(
    side: str,
    key: str,
    keys: Sequence[str],
    *,
    is_terminal: Optional[bool] = None,
) -> TransactionStage:
    total = len(keys)
    if key == 'cancelled':
        index = 0
        terminal = True
    elif key in keys:
        index = keys.index(key)
        terminal = bool(is_terminal) if is_terminal is not None else key == 'closed'
    else:
        index = 0
        terminal = bool(is_terminal)
    label = _LABELS.get(key, key.replace('_', ' ').title())
    summary = (_SUMMARIES.get(side) or {}).get(key) or _fallback_summary(key)
    return TransactionStage(
        key=key,
        label=label,
        index=index,
        total=total,
        summary=summary,
        is_terminal=terminal,
        side=side,
    )


def _simple_side_stage(side: str, status: str, keys: Sequence[str]) -> TransactionStage:
    """Map landlord/tenant/referral status onto a short stage list."""
    if side == 'landlord':
        status_map = {
            'preparing_to_list': 'preparing',
            'active': 'listed',
            'under_contract': 'lease_pending',
            'pending': 'lease_pending',
            'closed': 'leased',
        }
    elif side == 'tenant':
        status_map = {
            'showing': 'searching',
            'preparing_to_list': 'searching',
            'active': 'searching',
            'under_contract': 'application',
            'pending': 'application',
            'closed': 'leased',
        }
    else:  # referral
        status_map = {
            'preparing_to_list': 'new',
            'active': 'referred',
            'under_contract': 'in_progress',
            'pending': 'in_progress',
            'closed': 'closed',
        }
    key = status_map.get(status) or keys[0]
    return _build_stage(side, key, keys, is_terminal=(key in ('leased', 'closed')))


def _is_active_primary_contract(contract: Any) -> bool:
    if contract is None:
        return False
    position = (getattr(contract, 'position', None) or '').strip().lower()
    status = (getattr(contract, 'status', None) or '').strip().lower()
    return position == 'primary' and status == 'active'


def _count_open_offers(open_offers: Union[int, Sequence[Any], None]) -> int:
    if open_offers is None:
        return 0
    if isinstance(open_offers, int):
        return max(0, open_offers)
    count = 0
    for offer in open_offers:
        status = (getattr(offer, 'status', None) or '').strip().lower()
        if status in OPEN_OFFER_STATUSES:
            count += 1
    return count


def _resolve_closing_date(transaction, primary_contract) -> Optional[date]:
    for candidate in (
        getattr(primary_contract, 'closing_date', None) if primary_contract else None,
        getattr(transaction, 'expected_close_date', None),
    ):
        if isinstance(candidate, date):
            return candidate
    return None


def _in_closing_window(closing_date: Optional[date], today: date) -> bool:
    if closing_date is None:
        return False
    # Within CLOSING_WINDOW_DAYS ahead, or already past the date (still closing).
    return (closing_date - today).days <= CLOSING_WINDOW_DAYS


def _fallback_summary(key: str) -> str:
    return f'Focus on what this {key.replace("_", " ")} file needs next.'
