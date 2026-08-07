"""
Seller net-sheet estimator.

Read-only proceeds estimate for an offer or accepted contract.
Never writes to the database and never invents numbers it was not given.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional, Sequence

from models import SellerCommissionTerms, SellerOffer, SellerOfferVersion

logger = logging.getLogger(__name__)

MONEY_QUANT = Decimal('0.01')
PERCENT_DIVISOR = Decimal('100')

# Prefer these keys when pulling sales price from version.terms_data.
PRICE_TERMS_ALIASES = ('offer_price', 'sales_price', 'purchase_price')

LINE_SPECS: tuple[tuple[str, str, str], ...] = (
    ('sales_price', 'Sales price', 'credit'),
    ('listing_commission', 'Listing commission', 'cost'),
    ('buyer_agent_commission', 'Buyer agent commission', 'cost'),
    ('seller_concessions', 'Seller concessions', 'cost'),
    ('residential_service_contract', 'Residential service contract', 'cost'),
    ('bonus', 'Bonus', 'cost'),
    ('referral_fee', 'Referral fee', 'cost'),
    ('admin_transaction_fee', 'Admin / transaction fee', 'cost'),
    ('option_fee', 'Option fee', 'credit'),
    ('loan_payoff', 'Loan payoff', 'cost'),
    ('title_and_closing_costs', 'Title and closing costs', 'cost'),
    ('estimated_net', 'Estimated net', 'total'),
)


@dataclass
class NetSheetLine:
    key: str
    label: str
    amount: Decimal | None
    kind: str  # 'credit' | 'cost' | 'total'
    basis: str | None
    known: bool


@dataclass
class NetSheet:
    offer_id: int | None
    sales_price: Decimal | None
    lines: list[NetSheetLine] = field(default_factory=list)
    total_known_costs: Decimal | None = None
    estimated_net: Decimal | None = None
    unknown_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Serialize for JSON — Decimals become strings so money stays exact."""
        return {
            'offer_id': self.offer_id,
            'sales_price': _decimal_to_str(self.sales_price),
            'lines': [
                {
                    'key': line.key,
                    'label': line.label,
                    'amount': _decimal_to_str(line.amount),
                    'kind': line.kind,
                    'basis': line.basis,
                    'known': line.known,
                }
                for line in self.lines
            ],
            'total_known_costs': _decimal_to_str(self.total_known_costs),
            'estimated_net': _decimal_to_str(self.estimated_net),
            'unknown_keys': list(self.unknown_keys),
        }


def build_for_offer(
    offer: SellerOffer,
    *,
    commission_terms: SellerCommissionTerms | None = None,
    loan_payoff: Decimal | None = None,
) -> NetSheet:
    """Build a read-only net sheet for a seller offer. Never writes."""
    if offer is None:
        raise ValueError('offer is required')

    terms = _resolve_commission_terms(
        offer.transaction_id,
        offer.organization_id,
        commission_terms,
    )
    sales_price = _sales_price_for_offer(offer)
    return _assemble(
        offer_id=offer.id,
        sales_price=sales_price,
        buyer_agent_percent=getattr(offer, 'buyer_agent_commission_percent', None),
        buyer_agent_flat=getattr(offer, 'buyer_agent_commission_flat', None),
        seller_concessions=getattr(offer, 'seller_concessions_amount', None),
        residential_service_contract=getattr(offer, 'residential_service_contract', None),
        option_fee=getattr(offer, 'option_fee', None),
        commission_terms=terms,
        loan_payoff=loan_payoff,
    )


def build_for_contract(
    contract: Any,
    *,
    commission_terms: SellerCommissionTerms | None = None,
    loan_payoff: Decimal | None = None,
) -> NetSheet:
    """Build a read-only net sheet for an accepted seller contract. Never writes."""
    if contract is None:
        raise ValueError('contract is required')

    terms = _resolve_commission_terms(
        contract.transaction_id,
        contract.organization_id,
        commission_terms,
    )
    sales_price = _sales_price_for_contract(contract)
    option_fee = _option_fee_for_contract(contract)
    return _assemble(
        offer_id=getattr(contract, 'offer_id', None),
        sales_price=sales_price,
        buyer_agent_percent=getattr(contract, 'buyer_agent_commission_percent', None),
        buyer_agent_flat=getattr(contract, 'buyer_agent_commission_flat', None),
        seller_concessions=getattr(contract, 'seller_concessions_amount', None),
        residential_service_contract=getattr(contract, 'residential_service_contract', None),
        option_fee=option_fee,
        commission_terms=terms,
        loan_payoff=loan_payoff,
    )


def build_for_offers(
    offers: Sequence[SellerOffer],
    *,
    commission_terms: SellerCommissionTerms | None = None,
    loan_payoff: Decimal | None = None,
) -> list[NetSheet]:
    """Build one net sheet per offer, preserving input order. Never writes."""
    return [
        build_for_offer(
            offer,
            commission_terms=commission_terms,
            loan_payoff=loan_payoff,
        )
        for offer in offers
    ]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _assemble(
    *,
    offer_id: int | None,
    sales_price: Decimal | None,
    buyer_agent_percent: Any,
    buyer_agent_flat: Any,
    seller_concessions: Any,
    residential_service_contract: Any,
    option_fee: Any,
    commission_terms: SellerCommissionTerms | None,
    loan_payoff: Decimal | None,
) -> NetSheet:
    lines: list[NetSheetLine] = []

    # 1. sales_price
    if sales_price is not None:
        lines.append(_line(
            'sales_price', sales_price, 'credit',
            basis=None, known=True,
        ))
    else:
        lines.append(_line(
            'sales_price', None, 'credit',
            basis='Not provided', known=False,
        ))

    # 2. listing_commission
    lines.append(_commission_from_terms(
        key='listing_commission',
        sales_price=sales_price,
        percent=getattr(commission_terms, 'listing_commission_percent', None) if commission_terms else None,
        flat=getattr(commission_terms, 'listing_commission_flat', None) if commission_terms else None,
        percent_basis_label=None,
        missing_basis='Commission terms not available' if commission_terms is None else 'Not provided',
    ))

    # 3. buyer_agent_commission — offer/contract first, then listing coop
    lines.append(_buyer_agent_commission_line(
        sales_price=sales_price,
        buyer_agent_percent=buyer_agent_percent,
        buyer_agent_flat=buyer_agent_flat,
        commission_terms=commission_terms,
    ))

    # 4. seller_concessions
    lines.append(_fixed_amount_line(
        key='seller_concessions',
        kind='cost',
        raw=seller_concessions,
        basis_when_known=None,
    ))

    # 5. residential_service_contract
    lines.append(_residential_service_contract_line(residential_service_contract))

    # 6. bonus
    lines.append(_fixed_amount_line(
        key='bonus',
        kind='cost',
        raw=getattr(commission_terms, 'bonus_amount', None) if commission_terms else None,
        basis_when_known=None,
        missing_basis='Commission terms not available' if commission_terms is None else 'Not provided',
    ))

    # 7. referral_fee
    lines.append(_commission_from_terms(
        key='referral_fee',
        sales_price=sales_price,
        percent=getattr(commission_terms, 'referral_fee_percent', None) if commission_terms else None,
        flat=getattr(commission_terms, 'referral_fee_flat', None) if commission_terms else None,
        percent_basis_label=None,
        missing_basis='Commission terms not available' if commission_terms is None else 'Not provided',
    ))

    # 8. admin_transaction_fee
    lines.append(_fixed_amount_line(
        key='admin_transaction_fee',
        kind='cost',
        raw=getattr(commission_terms, 'admin_transaction_fee', None) if commission_terms else None,
        basis_when_known=None,
        missing_basis='Commission terms not available' if commission_terms is None else 'Not provided',
    ))

    # 9. option_fee (buyer pays seller — credit)
    lines.append(_fixed_amount_line(
        key='option_fee',
        kind='credit',
        raw=option_fee,
        basis_when_known='Paid by buyer to seller',
    ))

    # 10. loan_payoff — only known when caller supplies it
    if loan_payoff is not None:
        payoff = _money(loan_payoff)
        if payoff is not None:
            lines.append(_line(
                'loan_payoff', payoff, 'cost',
                basis='Caller-supplied', known=True,
            ))
        else:
            lines.append(_line(
                'loan_payoff', None, 'cost',
                basis='Not provided', known=False,
            ))
    else:
        lines.append(_line(
            'loan_payoff', None, 'cost',
            basis='Not provided', known=False,
        ))

    # 11. title_and_closing_costs — always unknown; never guess
    lines.append(_line(
        'title_and_closing_costs', None, 'cost',
        basis='Not estimated — varies by title company and county',
        known=False,
    ))

    # Totals from known lines only
    if sales_price is None:
        estimated_net: Decimal | None = None
        total_known_costs: Decimal | None = _sum_known_costs(lines)
    else:
        credits = Decimal('0.00')
        costs = Decimal('0.00')
        for line in lines:
            if not line.known or line.amount is None or line.kind == 'total':
                continue
            if line.kind == 'credit':
                credits += line.amount
            elif line.kind == 'cost':
                costs += line.amount
        estimated_net = _money(credits - costs)
        total_known_costs = _money(costs)

    lines.append(_line(
        'estimated_net', estimated_net, 'total',
        basis='Sales price + known credits − known costs' if estimated_net is not None else 'Sales price required',
        known=estimated_net is not None,
    ))

    unknown_keys = [line.key for line in lines if not line.known]

    return NetSheet(
        offer_id=offer_id,
        sales_price=sales_price,
        lines=lines,
        total_known_costs=total_known_costs,
        estimated_net=estimated_net,
        unknown_keys=unknown_keys,
    )


# ---------------------------------------------------------------------------
# Line builders
# ---------------------------------------------------------------------------


def _line(
    key: str,
    amount: Decimal | None,
    kind: str,
    *,
    basis: str | None,
    known: bool,
) -> NetSheetLine:
    label = next(spec[1] for spec in LINE_SPECS if spec[0] == key)
    return NetSheetLine(
        key=key,
        label=label,
        amount=amount,
        kind=kind,
        basis=basis,
        known=known,
    )


def _commission_from_terms(
    *,
    key: str,
    sales_price: Decimal | None,
    percent: Any,
    flat: Any,
    percent_basis_label: str | None,
    missing_basis: str,
) -> NetSheetLine:
    pct = _as_decimal(percent)
    if pct is not None:
        if sales_price is None:
            return _line(key, None, 'cost', basis='Sales price required', known=False)
        amount = _percent_of(sales_price, pct)
        basis = f'{_format_percent(pct)} of {_format_money(sales_price)}'
        if percent_basis_label:
            basis = f'{basis} ({percent_basis_label})'
        return _line(key, amount, 'cost', basis=basis, known=True)

    flat_amt = _as_decimal(flat)
    if flat_amt is not None:
        return _line(key, _money(flat_amt), 'cost', basis='Flat fee', known=True)

    return _line(key, None, 'cost', basis=missing_basis, known=False)


def _buyer_agent_commission_line(
    *,
    sales_price: Decimal | None,
    buyer_agent_percent: Any,
    buyer_agent_flat: Any,
    commission_terms: SellerCommissionTerms | None,
) -> NetSheetLine:
    key = 'buyer_agent_commission'

    offer_pct = _as_decimal(buyer_agent_percent)
    if offer_pct is not None:
        if sales_price is None:
            return _line(key, None, 'cost', basis='Sales price required', known=False)
        amount = _percent_of(sales_price, offer_pct)
        basis = (
            f'{_format_percent(offer_pct)} of {_format_money(sales_price)} '
            f'(offer buyer-agent commission)'
        )
        return _line(key, amount, 'cost', basis=basis, known=True)

    offer_flat = _as_decimal(buyer_agent_flat)
    if offer_flat is not None:
        return _line(
            key, _money(offer_flat), 'cost',
            basis='Flat fee (offer buyer-agent commission)',
            known=True,
        )

    if commission_terms is None:
        return _line(
            key, None, 'cost',
            basis='Commission terms not available',
            known=False,
        )

    coop_pct = _as_decimal(commission_terms.coop_compensation_percent)
    if coop_pct is not None:
        if sales_price is None:
            return _line(key, None, 'cost', basis='Sales price required', known=False)
        amount = _percent_of(sales_price, coop_pct)
        basis = (
            f'{_format_percent(coop_pct)} of {_format_money(sales_price)} '
            f'(listing coop compensation)'
        )
        return _line(key, amount, 'cost', basis=basis, known=True)

    coop_flat = _as_decimal(commission_terms.coop_compensation_flat)
    if coop_flat is not None:
        return _line(
            key, _money(coop_flat), 'cost',
            basis='Flat fee (listing coop compensation)',
            known=True,
        )

    return _line(key, None, 'cost', basis='Not provided', known=False)


def _fixed_amount_line(
    *,
    key: str,
    kind: str,
    raw: Any,
    basis_when_known: str | None,
    missing_basis: str = 'Not provided',
) -> NetSheetLine:
    amount = _as_decimal(raw)
    if amount is not None:
        return _line(key, _money(amount), kind, basis=basis_when_known, known=True)
    return _line(key, None, kind, basis=missing_basis, known=False)


def _residential_service_contract_line(raw: Any) -> NetSheetLine:
    key = 'residential_service_contract'
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return _line(key, None, 'cost', basis='Not provided', known=False)

    amount = _as_decimal(raw)
    if amount is not None:
        # Only treat as known when the field itself is a number (or numeric string).
        # Descriptive text that happens to contain digits must stay unknown — but
        # _as_decimal rejects non-numeric text, so a successful parse is enough.
        if isinstance(raw, str):
            stripped = raw.strip().replace(',', '').replace('$', '')
            # Reject mixed descriptive strings like "Buyer to purchase $500 RSC"
            try:
                Decimal(stripped)
            except (InvalidOperation, ValueError):
                return _line(key, None, 'cost', basis=str(raw).strip(), known=False)
        return _line(key, _money(amount), 'cost', basis=None, known=True)

    return _line(key, None, 'cost', basis=str(raw).strip(), known=False)


# ---------------------------------------------------------------------------
# Lookups / field resolution
# ---------------------------------------------------------------------------


def _resolve_commission_terms(
    transaction_id: int,
    organization_id: int,
    commission_terms: SellerCommissionTerms | None,
) -> SellerCommissionTerms | None:
    if commission_terms is not None:
        return commission_terms
    return (
        SellerCommissionTerms.query
        .filter_by(
            transaction_id=transaction_id,
            organization_id=organization_id,
        )
        .first()
    )


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


def _sales_price_for_offer(offer: SellerOffer) -> Decimal | None:
    price = _as_decimal(offer.offer_price)
    if price is not None:
        return _money(price)

    version = _current_version(offer)
    terms_data = (version.terms_data if version and version.terms_data else {}) or {}
    for alias in PRICE_TERMS_ALIASES:
        if alias in terms_data and terms_data[alias] not in (None, ''):
            price = _as_decimal(terms_data[alias])
            if price is not None:
                return _money(price)
    return None


def _sales_price_for_contract(contract: Any) -> Decimal | None:
    price = _as_decimal(getattr(contract, 'accepted_price', None))
    if price is not None:
        return _money(price)

    version = getattr(contract, 'accepted_version', None)
    if version is None and getattr(contract, 'accepted_version_id', None):
        version = SellerOfferVersion.query.filter_by(
            id=contract.accepted_version_id,
            organization_id=contract.organization_id,
        ).first()
    terms_data = (version.terms_data if version and version.terms_data else {}) or {}
    frozen = getattr(contract, 'frozen_terms', None) or {}
    for source in (terms_data, frozen):
        for alias in PRICE_TERMS_ALIASES + ('accepted_price',):
            if alias in source and source[alias] not in (None, ''):
                price = _as_decimal(source[alias])
                if price is not None:
                    return _money(price)

    linked_offer = getattr(contract, 'offer', None)
    if linked_offer is not None:
        return _sales_price_for_offer(linked_offer)
    return None


def _option_fee_for_contract(contract: Any) -> Any:
    frozen = getattr(contract, 'frozen_terms', None) or {}
    if 'option_fee' in frozen and frozen['option_fee'] not in (None, ''):
        return frozen['option_fee']

    version = getattr(contract, 'accepted_version', None)
    if version is None and getattr(contract, 'accepted_version_id', None):
        version = SellerOfferVersion.query.filter_by(
            id=contract.accepted_version_id,
            organization_id=contract.organization_id,
        ).first()
    terms_data = (version.terms_data if version and version.terms_data else {}) or {}
    if 'option_fee' in terms_data and terms_data['option_fee'] not in (None, ''):
        return terms_data['option_fee']

    linked_offer = getattr(contract, 'offer', None)
    if linked_offer is not None:
        return getattr(linked_offer, 'option_fee', None)
    return None


# ---------------------------------------------------------------------------
# Decimal helpers
# ---------------------------------------------------------------------------


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


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _percent_of(sales_price: Decimal, percent: Decimal) -> Decimal:
    return _money(sales_price * percent / PERCENT_DIVISOR)


def _sum_known_costs(lines: Sequence[NetSheetLine]) -> Decimal:
    total = Decimal('0.00')
    for line in lines:
        if line.known and line.kind == 'cost' and line.amount is not None:
            total += line.amount
    return _money(total)


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _format_money(amount: Decimal) -> str:
    quantized = _money(amount)
    if quantized == quantized.to_integral_value():
        return f'${quantized:,.0f}'
    return f'${quantized:,.2f}'


def _format_percent(percent: Decimal) -> str:
    normalized = percent.normalize()
    text = format(normalized, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return f'{text}%'
