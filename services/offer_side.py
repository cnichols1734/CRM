"""Side-aware vocabulary and rules for transaction offer threads.

SellerOffer rows attach to either seller or buyer transactions. This module
is the pure source of truth for labels and direction mapping — no DB or Flask.
"""

from __future__ import annotations

OFFER_SIDES = frozenset({'seller', 'buyer'})

_LABELS = {
    'seller': {
        'panel_title': 'Offers',
        'panel_subtitle': 'Offers received on this listing.',
        'empty_state': 'No offers yet.',
        'add_label': 'Log an offer',
        'counterparty_label': 'Buyer',
        'counterparty_agent_label': 'Buyer agent',
        'price_label': 'Offer price',
        'accept_primary_label': 'Accept as primary',
        'accept_backup_label': 'Accept as backup',
        'inbound_direction': 'buyer_offer',
        'outbound_direction': 'seller_counter',
    },
    'buyer': {
        'panel_title': 'Offers submitted',
        'panel_subtitle': 'Offers you submitted for this buyer.',
        'empty_state': 'No offers submitted yet.',
        'add_label': 'Log an offer you submitted',
        'counterparty_label': 'Seller',
        'counterparty_agent_label': 'Listing agent',
        'price_label': 'Offer price',
        'accept_primary_label': 'Mark accepted',
        'accept_backup_label': 'Mark backup accepted',
        'inbound_direction': 'seller_counter',
        'outbound_direction': 'buyer_offer',
    },
}

_STATUS_LABELS = {
    'seller': {
        'new': 'New',
        'countered': 'Countered',
    },
    'buyer': {
        'new': 'Submitted',
        'countered': 'Seller countered',
    },
}


def side_for_transaction(transaction) -> str | None:
    """Return 'seller' or 'buyer' from the transaction type, else None."""
    if transaction is None:
        return None
    tx_type = getattr(transaction, 'transaction_type', None)
    if tx_type is None:
        return None
    name = getattr(tx_type, 'name', None)
    if name in OFFER_SIDES:
        return name
    return None


def supports_offers(transaction) -> bool:
    """True when the transaction type can host offer threads."""
    return side_for_transaction(transaction) is not None


def labels_for_side(side: str) -> dict:
    """Return UI copy and direction keys for a supported offer side."""
    if side not in OFFER_SIDES:
        raise ValueError(f'Unsupported offer side: {side!r}')
    return dict(_LABELS[side])


def status_label(side: str, status: str) -> str:
    """Humanize an offer status for the given side."""
    key = (status or '').strip()
    mapped = _STATUS_LABELS.get(side, {}).get(key)
    if mapped:
        return mapped
    if not key:
        return ''
    return key.replace('_', ' ').title()


def opening_direction_for_side(side: str) -> str:
    """Direction for the first version when logging a new offer thread.

    Seller receives a buyer offer (inbound); buyer logs an offer they submitted
    (outbound). Both resolve to ``buyer_offer`` today, but callers should use
    this helper rather than hardcoding the string.
    """
    labels = labels_for_side(side)
    if side == 'seller':
        return labels['inbound_direction']
    return labels['outbound_direction']
