"""Unit coverage for side-aware offer vocabulary."""

from types import SimpleNamespace

from services.offer_side import (
    labels_for_side,
    side_for_transaction,
    status_label,
)


def test_side_for_transaction_buyer_seller_and_other():
    buyer = SimpleNamespace(transaction_type=SimpleNamespace(name='buyer'))
    seller = SimpleNamespace(transaction_type=SimpleNamespace(name='seller'))
    landlord = SimpleNamespace(transaction_type=SimpleNamespace(name='landlord'))
    missing = SimpleNamespace(transaction_type=None)

    assert side_for_transaction(buyer) == 'buyer'
    assert side_for_transaction(seller) == 'seller'
    assert side_for_transaction(landlord) is None
    assert side_for_transaction(missing) is None
    assert side_for_transaction(None) is None


def test_labels_for_side_counterparty_agent():
    assert labels_for_side('buyer')['counterparty_agent_label'] == 'Listing agent'
    assert labels_for_side('seller')['counterparty_agent_label'] == 'Buyer agent'


def test_status_label_side_aware_and_fallback():
    assert status_label('buyer', 'countered') == 'Seller countered'
    assert status_label('seller', 'countered') == 'Countered'
    assert status_label('buyer', 'new') == 'Submitted'
    assert status_label('seller', 'new') == 'New'
    assert status_label('seller', 'needs_review') == 'Needs Review'
    assert status_label('buyer', 'accepted_primary') == 'Accepted Primary'


def test_inbound_outbound_directions_are_inverses():
    seller = labels_for_side('seller')
    buyer = labels_for_side('buyer')
    assert seller['inbound_direction'] == buyer['outbound_direction']
    assert seller['outbound_direction'] == buyer['inbound_direction']
