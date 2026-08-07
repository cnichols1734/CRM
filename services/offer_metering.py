"""Advisory offer overage metering (no billing side effects).

Pricing includes the first five offer threads per transaction; each additional
thread is reported at $25. This module only counts and reports — it never
writes invoices or calls Stripe.
"""

from __future__ import annotations

from decimal import Decimal

from models import SellerOffer

OFFER_INCLUDED_COUNT = 5
OFFER_OVERAGE_FEE = Decimal('25.00')

_EXCLUDED_STATUSES = frozenset({'replaced', 'withdrawn'})


def count_offers(transaction_id: int, organization_id: int) -> int:
    """Count billable offer threads for a transaction (org-scoped)."""
    query = SellerOffer.query.filter(
        SellerOffer.transaction_id == transaction_id,
        SellerOffer.organization_id == organization_id,
        ~SellerOffer.status.in_(tuple(_EXCLUDED_STATUSES)),
    )
    return query.count()


def metering_for_transaction(transaction_id: int, organization_id: int) -> dict:
    """Return advisory overage metrics for a transaction's offer threads."""
    offer_count = count_offers(transaction_id, organization_id)
    overage_count = max(0, offer_count - OFFER_INCLUDED_COUNT)
    overage_total = OFFER_OVERAGE_FEE * Decimal(overage_count)
    return {
        'offer_count': offer_count,
        'included': OFFER_INCLUDED_COUNT,
        'overage_count': overage_count,
        'overage_fee_each': OFFER_OVERAGE_FEE,
        'overage_total': overage_total,
        'over_limit': overage_count > 0,
    }
