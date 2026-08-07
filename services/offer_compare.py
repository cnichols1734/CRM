"""
Offer Compare Assist - Phase 2 (E2-4)

Read-only side-by-side comparison of SellerOffer / SellerOfferVersion terms.
Never auto-accepts or writes CRM fields.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from models import SellerOffer, SellerOfferVersion, Transaction

# Fields compared across offers (SellerOffer columns + common terms_data keys).
COMPARE_FIELDS = (
    ('offer_price', 'Offer price'),
    ('financing_type', 'Financing'),
    ('cash_down_payment', 'Down payment'),
    ('financing_amount', 'Financed amount'),
    ('earnest_money', 'Earnest money'),
    ('additional_earnest_money', 'Additional earnest'),
    ('option_fee', 'Option fee'),
    ('option_period_days', 'Option period (days)'),
    ('seller_concessions_amount', 'Seller concessions'),
    ('proposed_close_date', 'Proposed close'),
    ('possession_type', 'Possession'),
    ('leaseback_days', 'Leaseback (days)'),
    ('appraisal_contingency', 'Appraisal contingency'),
    ('financing_contingency', 'Financing contingency'),
    ('sale_of_other_property_contingency', 'Sale-of-other-property contingency'),
    ('net_to_seller_estimate', 'Est. net to seller'),
)

# Prefer these keys when pulling from version.terms_data.
TERMS_DATA_ALIASES = {
    'offer_price': ('offer_price', 'sales_price', 'purchase_price'),
    'earnest_money': ('earnest_money',),
    'option_fee': ('option_fee',),
    'proposed_close_date': ('proposed_close_date', 'closing_date', 'close_date'),
    'financing_type': ('financing_type', 'loan_type'),
}


class OfferCompareService:
    """Build a read-only comparison of competing offers on a transaction."""

    @staticmethod
    def compare_offers(
        transaction: Transaction,
        *,
        offer_ids: Optional[Sequence[int]] = None,
        include_terminal: bool = False,
    ) -> Dict[str, Any]:
        """
        Compare offers on ``transaction``.

        Returns a summary dict suitable for BOB tools / UI. No writes.
        """
        query = SellerOffer.query.filter_by(
            organization_id=transaction.organization_id,
            transaction_id=transaction.id,
        )
        if offer_ids:
            query = query.filter(SellerOffer.id.in_(list(offer_ids)))
        offers = query.order_by(SellerOffer.received_at.desc()).all()

        terminal = {'accepted', 'declined', 'withdrawn', 'expired', 'replaced'}
        if not include_terminal:
            offers = [o for o in offers if (o.status or '').lower() not in terminal]

        columns = []
        for offer in offers:
            version = OfferCompareService._current_version(offer)
            columns.append(OfferCompareService._column_for_offer(offer, version))

        rows = []
        for field_key, label in COMPARE_FIELDS:
            values = [col['terms'].get(field_key) for col in columns]
            rows.append({
                'field': field_key,
                'label': label,
                'values': values,
                'differs': OfferCompareService._differs(values),
            })

        differing = [r for r in rows if r['differs']]
        best_price = OfferCompareService._best_numeric(
            columns, 'offer_price', higher=True,
        )
        soonest_close = OfferCompareService._best_date(columns, 'proposed_close_date')

        summary_lines = []
        if len(columns) < 2:
            summary_lines.append(
                f'{len(columns)} offer(s) available — need at least two to compare.'
            )
        else:
            summary_lines.append(f'Comparing {len(columns)} offers.')
            if differing:
                summary_lines.append(
                    f'{len(differing)} term(s) differ: '
                    + ', '.join(r['label'] for r in differing[:8])
                    + ('.' if len(differing) <= 8 else '…')
                )
            if best_price:
                summary_lines.append(
                    f'Highest price: {best_price["display"]} '
                    f'({best_price["buyer_label"]}).'
                )
            if soonest_close:
                summary_lines.append(
                    f'Soonest close: {soonest_close["display"]} '
                    f'({soonest_close["buyer_label"]}).'
                )

        return {
            'transaction_id': transaction.id,
            'offer_count': len(columns),
            'offers': columns,
            'rows': rows,
            'differing_fields': [r['field'] for r in differing],
            'highlights': {
                'highest_price': best_price,
                'soonest_close': soonest_close,
            },
            'summary': ' '.join(summary_lines),
            'read_only': True,
        }

    @staticmethod
    def _current_version(offer: SellerOffer) -> Optional[SellerOfferVersion]:
        if offer.current_version_id:
            version = SellerOfferVersion.query.filter_by(
                id=offer.current_version_id,
                offer_id=offer.id,
                organization_id=offer.organization_id,
            ).first()
            if version:
                return version
        return (
            SellerOfferVersion.query
            .filter_by(offer_id=offer.id, organization_id=offer.organization_id)
            .order_by(SellerOfferVersion.version_number.desc())
            .first()
        )

    @staticmethod
    def _column_for_offer(
        offer: SellerOffer,
        version: Optional[SellerOfferVersion],
    ) -> Dict[str, Any]:
        terms_data = (version.terms_data if version and version.terms_data else {}) or {}
        terms: Dict[str, Any] = {}
        sources: Dict[str, str] = {}

        for field_key, _label in COMPARE_FIELDS:
            value = getattr(offer, field_key, None)
            source = 'offer'
            if value is None:
                for alias in TERMS_DATA_ALIASES.get(field_key, (field_key,)):
                    if alias in terms_data and terms_data[alias] not in (None, ''):
                        value = terms_data[alias]
                        source = f'version.terms_data.{alias}'
                        break
            terms[field_key] = OfferCompareService._normalize(value)
            if value is not None:
                sources[field_key] = source

        return {
            'offer_id': offer.id,
            'buyer_names': offer.buyer_names,
            'buyer_agent_name': offer.buyer_agent_name,
            'status': offer.status,
            'received_at': offer.received_at.isoformat() if offer.received_at else None,
            'version_id': version.id if version else None,
            'version_number': version.version_number if version else None,
            'terms': terms,
            'sources': sources,
            'label': offer.buyer_names or f'Offer {offer.id}',
        }

    @staticmethod
    def _normalize(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        return value

    @staticmethod
    def _differs(values: List[Any]) -> bool:
        present = [v for v in values if v is not None]
        if len(present) < 2:
            return False
        first = present[0]
        return any(v != first for v in present[1:])

    @staticmethod
    def _best_numeric(columns: List[dict], field: str, *, higher: bool) -> Optional[dict]:
        best = None
        best_val = None
        for col in columns:
            raw = col['terms'].get(field)
            if raw is None:
                continue
            try:
                num = float(raw)
            except (TypeError, ValueError):
                continue
            if best_val is None or (higher and num > best_val) or (not higher and num < best_val):
                best_val = num
                best = {
                    'offer_id': col['offer_id'],
                    'buyer_label': col['label'],
                    'value': num,
                    'display': f'${num:,.0f}',
                }
        return best

    @staticmethod
    def _best_date(columns: List[dict], field: str) -> Optional[dict]:
        best = None
        best_val = None
        for col in columns:
            raw = col['terms'].get(field)
            if not raw:
                continue
            text = str(raw)[:10]
            if best_val is None or text < best_val:
                best_val = text
                best = {
                    'offer_id': col['offer_id'],
                    'buyer_label': col['label'],
                    'value': text,
                    'display': text,
                }
        return best
