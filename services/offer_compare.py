"""
Offer Compare Assist - Phase 2 (E2-4)

Read-only side-by-side comparison of SellerOffer / SellerOfferVersion terms.
Never auto-accepts or writes CRM fields.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from models import SellerOffer, SellerOfferVersion, Transaction
from services.offer_summary_email import (
    _VersionBackedOffer,
    _commission,
    _current_offer_version,
    _home_warranty,
    _pick,
    _survey_responsibility,
    _text,
)

# Fields compared across offers (SellerOffer columns + common terms_data keys).
# Survey, commission, warranty, and title policy use the same formatters as the
# client offer email so the chart and the outbound summary agree.
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
    ('survey_responsibility', 'Survey provided by'),
    ('buyer_agent_commission', "Commission to buyer's agent"),
    ('residential_service_contract', 'Home warranty'),
    ('title_policy_payer', 'Title policy paid by'),
    ('possession_type', 'Possession'),
    ('leaseback_days', 'Leaseback (days)'),
    ('appraisal_contingency', 'Appraisal contingency'),
    ('financing_contingency', 'Financing contingency'),
    ('sale_of_other_property_contingency', 'Sale-of-other-property contingency'),
    ('net_to_seller_estimate', 'Est. net to seller'),
)

_FORMATTED_FIELDS = {
    'survey_responsibility': _survey_responsibility,
    'buyer_agent_commission': _commission,
    'residential_service_contract': _home_warranty,
    'title_policy_payer': lambda offer: _text(_pick(offer, 'title_policy_payer')),
}

# Prefer these keys when pulling from version.terms_data.
TERMS_DATA_ALIASES = {
    'offer_price': ('offer_price', 'sales_price', 'purchase_price'),
    'earnest_money': ('earnest_money',),
    'option_fee': ('option_fee',),
    'seller_concessions_amount': ('seller_concessions_amount', 'seller_concessions'),
    'proposed_close_date': ('proposed_close_date', 'closing_date', 'close_date'),
    'financing_type': ('financing_type', 'loan_type'),
    'survey_responsibility': ('survey_furnished_by', 'survey_choice', 'survey_payer'),
    'buyer_agent_commission': (
        'buyer_agent_commission_percent',
        'buyer_agent_commission_flat',
    ),
    'residential_service_contract': ('residential_service_contract',),
    'title_policy_payer': ('title_policy_payer',),
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
        return _current_offer_version(offer)

    @staticmethod
    def _column_for_offer(
        offer: SellerOffer,
        version: Optional[SellerOfferVersion],
    ) -> Dict[str, Any]:
        terms_data = (version.terms_data if version and version.terms_data else {}) or {}
        summary = getattr(offer, 'terms_summary', None)
        terms: Dict[str, Any] = {}
        sources: Dict[str, str] = {}
        backed = _VersionBackedOffer(offer, terms_data)

        for field_key, _label in COMPARE_FIELDS:
            formatter = _FORMATTED_FIELDS.get(field_key)
            if formatter:
                value = formatter(backed)
                source = (
                    OfferCompareService._source_for_field(
                        offer, field_key, terms_data, summary,
                    )
                    if value is not None else None
                )
            else:
                value, source = OfferCompareService._unformatted_value(
                    offer, field_key, terms_data, summary,
                )
            terms[field_key] = OfferCompareService._normalize(value)
            if value is not None and source:
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
    def _unformatted_value(
        offer: SellerOffer,
        field_key: str,
        terms_data: Dict[str, Any],
        summary: Optional[Dict[str, Any]],
    ) -> tuple[Any, Optional[str]]:
        """Column, then terms_summary aliases, then version.terms_data.

        Same order as ``_pick`` and the Compare net sheet so a reviewed
        summary (seller concessions, price) wins over a stale extract.
        """
        value = getattr(offer, field_key, None)
        if value not in (None, ''):
            return value, 'offer'
        aliases = TERMS_DATA_ALIASES.get(field_key, (field_key,))
        if isinstance(summary, dict):
            for alias in aliases:
                if alias in summary and summary[alias] not in (None, ''):
                    return summary[alias], f'terms_summary.{alias}'
        for alias in aliases:
            if alias in terms_data and terms_data[alias] not in (None, ''):
                return terms_data[alias], f'version.terms_data.{alias}'
        return None, None

    @staticmethod
    def _source_for_field(
        offer: SellerOffer,
        field_key: str,
        terms_data: Dict[str, Any],
        summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Offer columns, then reviewed terms_summary, then version terms_data."""
        aliases = TERMS_DATA_ALIASES.get(field_key, (field_key,))
        for key in aliases:
            if getattr(offer, key, None) not in (None, ''):
                return 'offer'
        if isinstance(summary, dict):
            for key in aliases:
                if key in summary and summary[key] not in (None, ''):
                    return f'terms_summary.{key}'
        for key in aliases:
            if key in terms_data and terms_data[key] not in (None, ''):
                return f'version.terms_data.{key}'
        return 'offer'

    @staticmethod
    def _normalize(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        return value

    @staticmethod
    def _omitted(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        return False

    @staticmethod
    def _differs(values: List[Any]) -> bool:
        if len(values) < 2:
            return False
        omitted = [OfferCompareService._omitted(v) for v in values]
        if all(omitted):
            return False
        if any(omitted):
            return True
        first = values[0]
        return any(v != first for v in values[1:])

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
