"""
Shared helpers for transaction views and API endpoints.
"""

from datetime import datetime


LISTING_INFO_DISPLAY_FIELDS = [
    'list_price',
    'go_live_date',
    'listing_start_date',
    'listing_end_date',
    'total_commission',
    'listing_side_commission',
    'buyer_commission',
    'protection_period_days',
    'financing_types',
    'has_hoa',
]

CONTRACT_TERMS_DISPLAY_FIELDS = [
    'sales_price',
    'effective_date',
    'closing_date',
    'option_fee',
    'option_period_days',
    'earnest_money',
    'financing_type',
    'title_company',
    'escrow_officer',
    'survey_choice',
    'has_hoa',
    'buyer_commission',
]

PURCHASE_CONTRACT_TEMPLATE_SLUGS = frozenset({
    'lease-document',
    'one-to-four-family-contract',
    'condominium-contract',
    'new-home-completed-construction-contract',
    'new-home-incomplete-construction-contract',
    'farm-and-ranch-contract',
    'purchase-contract',
    'seller-accepted-contract',
})


def _format_date(date_str):
    if not date_str:
        return None
    try:
        dt_obj = datetime.strptime(str(date_str), '%Y-%m-%d')
        return dt_obj.strftime('%B %d, %Y')
    except (ValueError, TypeError):
        return str(date_str)


def _format_currency(value):
    """Format a numeric value as $X,XXX,XXX."""
    if value is None:
        return None
    try:
        num = int(float(str(value).replace(',', '').replace('$', '')))
        return f"${num:,}"
    except (ValueError, TypeError):
        return str(value)


def resolve_header_price_display(transaction, listing_info=None, contract_terms=None):
    """Price for the transaction detail header.

    Prefer executed/deal price when present. Listing-stage seller files keep
    list price out of sales_price on purpose, so fall back to listing info.
    """
    extra = getattr(transaction, 'extra_data', None) or {}
    for key in ('sales_price', 'purchase_price'):
        formatted = _format_currency(extra.get(key))
        if formatted:
            return formatted

    if isinstance(contract_terms, dict):
        contract_price = contract_terms.get('sales_price')
        if contract_price not in (None, '', '—', '-'):
            if str(contract_price).strip().startswith('$'):
                return str(contract_price).strip()
            formatted = _format_currency(contract_price)
            if formatted:
                return formatted

    if isinstance(listing_info, dict):
        list_price = listing_info.get('list_price')
        if list_price not in (None, '', '—', '-'):
            return str(list_price).strip()

    return _format_currency(extra.get('list_price'))


def _format_percent(value):
    """Format a numeric value as X% (strips trailing zeros)."""
    if value is None:
        return None
    try:
        num = float(str(value).replace('%', ''))
        formatted = f"{num:g}"
        return f"{formatted}%"
    except (ValueError, TypeError):
        return str(value)


def _parse_percent_number(value):
    if value in (None, ''):
        return None
    try:
        return float(str(value).replace('%', '').replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _derive_listing_side_commission(total_commission, buyer_commission):
    total = _parse_percent_number(total_commission)
    buyer = _parse_percent_number(buyer_commission)
    if total is None or buyer is None:
        return None
    side = total - buyer
    if side < 0:
        return None
    return _format_percent(side)


def _is_filled(value):
    return value not in (None, '')


def _set_sourced_value(data, sources, key, value, source):
    if not _is_filled(value):
        return
    data[key] = value
    sources[key] = source


def _listing_display_fields(data):
    if (data or {}).get('commission_type') == '5b':
        return [
            'list_price',
            'go_live_date',
            'listing_start_date',
            'listing_end_date',
            'broker_fee',
            'protection_period_days',
            'financing_types',
            'has_hoa',
        ]
    return list(LISTING_INFO_DISPLAY_FIELDS)


def _compute_listing_completeness(data):
    fields = _listing_display_fields(data)
    missing = [key for key in fields if not _is_filled((data or {}).get(key))]
    filled = len(fields) - len(missing)
    return {
        'filled': filled,
        'total': len(fields),
        'missing': missing,
    }


def _compute_contract_completeness(data):
    fields = list(CONTRACT_TERMS_DISPLAY_FIELDS)
    missing = [key for key in fields if not _is_filled((data or {}).get(key))]
    filled = len(fields) - len(missing)
    return {
        'filled': filled,
        'total': len(fields),
        'missing': missing,
    }


def _has_display_values(data):
    if not data:
        return False
    return any(
        _is_filled(value)
        for key, value in data.items()
        if not str(key).startswith('_')
    )


def _attach_listing_meta(data, sources):
    if not data and not sources:
        return None
    result = dict(data or {})
    result['_sources'] = dict(sources or {})
    result['_completeness'] = _compute_listing_completeness(result)
    if not _has_display_values(result):
        return None
    return result


def _format_hoa(value):
    if value in (True, 'yes', 'true', '1', 'Yes'):
        return 'Yes'
    if value in (False, 'no', 'false', '0', 'No'):
        return 'No'
    return None


def _format_buyer_commission(percent, flat):
    return _format_percent(percent) or _format_currency(flat)


def _format_commission_side(percent, flat):
    """Prefer explicit percent, else flat dollars — never treat flat as percent."""
    if percent not in (None, ''):
        return _format_percent(percent)
    if flat not in (None, ''):
        return _format_currency(flat)
    return None


def _compose_total_commission_display(
    *,
    total_percent=None,
    total_display=None,
    listing_percent=None,
    listing_flat=None,
    buyer_percent=None,
    buyer_flat=None,
    raw_text=None,
):
    """Build a UI string for seller total compensation (supports hybrid fees)."""
    if total_display not in (None, ''):
        text = str(total_display).strip()
        if text:
            return text
    if total_percent not in (None, ''):
        return _format_percent(total_percent)

    listing = _format_commission_side(listing_percent, listing_flat)
    buyer = _format_commission_side(buyer_percent, buyer_flat)
    if listing and buyer and listing != buyer:
        return f'{listing} + {buyer}'
    if raw_text not in (None, ''):
        text = str(raw_text).strip()
        if text:
            return text
    return listing or buyer


def apply_listing_info_overrides(listing_info, overrides):
    """Apply user overrides from transaction.extra_data onto extracted listing info."""
    if not overrides:
        if listing_info is None:
            return None
        data = dict(listing_info)
        sources = dict(data.pop('_sources', {}) or {})
        data.pop('_completeness', None)
        return _attach_listing_meta(data, sources)

    data = dict(listing_info or {})
    sources = dict(data.pop('_sources', {}) or {})
    data.pop('_completeness', None)

    def clean(value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    list_price = clean(overrides.get('list_price'))
    if list_price is not None:
        data['list_price'] = _format_currency(list_price)
        sources['list_price'] = 'override'

    start_date = clean(overrides.get('listing_start_date'))
    if start_date is not None:
        data['listing_start_date'] = _format_date(start_date)
        sources['listing_start_date'] = 'override'

    end_date = clean(overrides.get('listing_end_date'))
    if end_date is not None:
        data['listing_end_date'] = _format_date(end_date)
        sources['listing_end_date'] = 'override'

    total_commission = clean(overrides.get('total_commission'))
    if total_commission is not None:
        data['total_commission'] = _format_percent(total_commission) or _format_currency(total_commission)
        sources['total_commission'] = 'override'

    buyer_commission = clean(overrides.get('buyer_commission'))
    if buyer_commission is not None:
        data['buyer_commission'] = _format_percent(buyer_commission) or _format_currency(buyer_commission)
        sources['buyer_commission'] = 'override'

    listing_side_commission = clean(overrides.get('listing_side_commission'))
    if listing_side_commission is not None:
        data['listing_side_commission'] = (
            _format_percent(listing_side_commission) or _format_currency(listing_side_commission)
        )
        sources['listing_side_commission'] = 'override'

    protection_days = clean(overrides.get('protection_period_days'))
    if protection_days is not None:
        data['protection_period_days'] = protection_days
        sources['protection_period_days'] = 'override'

    financing_types = clean(overrides.get('financing_types'))
    if financing_types is not None:
        data['financing_types'] = financing_types
        sources['financing_types'] = 'override'

    has_hoa = clean(overrides.get('has_hoa'))
    if has_hoa is not None:
        normalized_hoa = has_hoa.lower()
        if normalized_hoa in ('yes', 'true', '1'):
            data['has_hoa'] = 'Yes'
        elif normalized_hoa in ('no', 'false', '0'):
            data['has_hoa'] = 'No'
        else:
            data['has_hoa'] = has_hoa
        sources['has_hoa'] = 'override'

    # Keep commission rendering in the standard split-commission mode when editing.
    if data.get('commission_type') != '5b':
        data['commission_type'] = '5a'
        if not data.get('listing_side_commission'):
            derived = _derive_listing_side_commission(data.get('total_commission'), data.get('buyer_commission'))
            if derived:
                data['listing_side_commission'] = derived
                if 'listing_side_commission' not in sources:
                    sources['listing_side_commission'] = sources.get('total_commission') or sources.get('buyer_commission') or 'override'

    return _attach_listing_meta(data, sources)


def build_listing_info(
    documents,
    overrides=None,
    *,
    transaction=None,
    listing_profile=None,
):
    """
    Merge listing data from approved operational sources.

    Precedence: listing profile / questionnaire, extracted listing agreement,
    then explicit user overrides. Contract sales price is intentionally never
    used as list price.

    Used by both the transaction detail view and the extraction-status API.
    """
    data = {}
    sources = {}

    if listing_profile:
        if listing_profile.current_list_price is not None:
            _set_sourced_value(
                data, sources, 'list_price',
                _format_currency(listing_profile.current_list_price),
                'listing_profile',
            )
        if listing_profile.go_live_date:
            _set_sourced_value(
                data, sources, 'go_live_date',
                _format_date(listing_profile.go_live_date),
                'listing_profile',
            )

    intake_data = (getattr(transaction, 'intake_data', None) or {}) if transaction else {}
    if 'has_hoa' in intake_data and intake_data.get('has_hoa') is not None:
        hoa_display = _format_hoa(intake_data.get('has_hoa'))
        if hoa_display:
            _set_sourced_value(data, sources, 'has_hoa', hoa_display, 'intake')

    listing_doc = next((d for d in documents if d.template_slug == 'listing-agreement'), None)
    field_data = listing_doc.field_data if listing_doc and isinstance(listing_doc.field_data, dict) else {}
    if not field_data:
        return apply_listing_info_overrides(_attach_listing_meta(data, sources), overrides)

    listing_only_percent = field_data.get('listing_only_percent')
    listing_only_flat = field_data.get('listing_only_flat')
    is_listing_broker_only = bool(listing_only_percent or listing_only_flat)

    hoa_display = _format_hoa(field_data.get('has_hoa'))

    common = {
        'list_price': _format_currency(field_data.get('list_price')),
        'listing_start_date': _format_date(field_data.get('listing_start_date')),
        'listing_end_date': _format_date(field_data.get('listing_end_date')),
        'protection_period_days': field_data.get('protection_period_days'),
        'financing_types': field_data.get('financing_types'),
        'has_hoa': hoa_display,
        'special_provisions': field_data.get('special_provisions'),
    }

    for key, value in common.items():
        if _is_filled(value):
            data[key] = value
            sources[key] = 'listing_agreement'

    if is_listing_broker_only:
        broker_fee = _format_percent(listing_only_percent) or _format_currency(listing_only_flat)
        data['commission_type'] = '5b'
        if _is_filled(broker_fee):
            data['broker_fee'] = broker_fee
            sources['broker_fee'] = 'listing_agreement'
    else:
        buyer_commission = _format_buyer_commission(
            field_data.get('buyer_agent_percent'),
            field_data.get('buyer_agent_flat'),
        )
        data['commission_type'] = '5a'
        listing_side = _format_commission_side(
            field_data.get('listing_side_percent'),
            field_data.get('listing_side_flat'),
        )
        if not _is_filled(listing_side):
            listing_side = _derive_listing_side_commission(
                field_data.get('total_commission'),
                buyer_commission,
            )
        total_commission = _compose_total_commission_display(
            total_percent=field_data.get('total_commission'),
            total_display=field_data.get('total_commission_display'),
            listing_percent=field_data.get('listing_side_percent'),
            listing_flat=field_data.get('listing_side_flat'),
            buyer_percent=field_data.get('buyer_agent_percent'),
            buyer_flat=field_data.get('buyer_agent_flat'),
            raw_text=field_data.get('broker_fee_raw_text'),
        )
        if _is_filled(total_commission):
            data['total_commission'] = total_commission
            sources['total_commission'] = 'listing_agreement'
        if _is_filled(buyer_commission):
            data['buyer_commission'] = buyer_commission
            sources['buyer_commission'] = 'listing_agreement'
        if _is_filled(listing_side):
            data['listing_side_commission'] = listing_side
            sources['listing_side_commission'] = 'listing_agreement'
        if field_data.get('broker_fee_raw_text'):
            data['broker_fee_raw_text'] = str(field_data.get('broker_fee_raw_text')).strip()

    return apply_listing_info_overrides(_attach_listing_meta(data, sources), overrides)


def _decimal_or_none(value):
    if value in (None, ''):
        return None
    try:
        from decimal import Decimal, InvalidOperation
        cleaned = str(value).replace(',', '').replace('$', '').replace('%', '').strip()
        if not cleaned:
            return None
        return Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError):
        return None


def sync_seller_commission_terms_from_listing(
    *,
    transaction,
    field_data,
    user_id,
    org_id,
):
    """Upsert SellerCommissionTerms from listing-agreement extraction fields."""
    if not transaction or not isinstance(field_data, dict):
        return None

    from models import SellerCommissionTerms, db

    listing_pct = _decimal_or_none(field_data.get('listing_side_percent'))
    listing_flat = _decimal_or_none(field_data.get('listing_side_flat'))
    buyer_pct = _decimal_or_none(
        field_data.get('buyer_agent_percent')
        or field_data.get('buyer_agent_commission_percent')
    )
    buyer_flat = _decimal_or_none(
        field_data.get('buyer_agent_flat')
        or field_data.get('buyer_agent_commission_flat')
    )

    # Classic 5A(1)(a) percent total with buyer share → derive listing percent.
    if listing_pct is None and listing_flat is None:
        total_pct = _decimal_or_none(field_data.get('total_commission'))
        if total_pct is not None and buyer_pct is not None:
            derived = total_pct - buyer_pct
            if derived >= 0:
                listing_pct = derived

    if not any(v is not None for v in (listing_pct, listing_flat, buyer_pct, buyer_flat)):
        return None

    terms = SellerCommissionTerms.query.filter_by(
        transaction_id=transaction.id,
        organization_id=org_id,
    ).first()
    if terms and terms.source == 'manual':
        # Preserve agent-edited terms; only fill blanks.
        if terms.listing_commission_percent is None and listing_pct is not None:
            terms.listing_commission_percent = listing_pct
        if terms.listing_commission_flat is None and listing_flat is not None:
            terms.listing_commission_flat = listing_flat
        if terms.coop_compensation_percent is None and buyer_pct is not None:
            terms.coop_compensation_percent = buyer_pct
        if terms.coop_compensation_flat is None and buyer_flat is not None:
            terms.coop_compensation_flat = buyer_flat
        return terms

    if not terms:
        terms = SellerCommissionTerms(
            organization_id=org_id,
            transaction_id=transaction.id,
            created_by_id=user_id,
        )
        db.session.add(terms)

    terms.listing_commission_percent = listing_pct
    terms.listing_commission_flat = listing_flat
    terms.coop_compensation_percent = buyer_pct
    terms.coop_compensation_flat = buyer_flat
    terms.source = 'listing_agreement_extraction'
    notes_bits = []
    raw = field_data.get('broker_fee_raw_text') or field_data.get('total_commission_display')
    if raw:
        notes_bits.append(str(raw).strip())
    if notes_bits:
        terms.notes = ' | '.join(notes_bits)[:2000]
    return terms


def _first_filled(*values):
    for value in values:
        if _is_filled(value):
            return value
    return None


def _apply_contract_document_fields(data, sources, field_data):
    if not isinstance(field_data, dict):
        return

    sales_price = _format_currency(_first_filled(
        field_data.get('sales_price'),
        field_data.get('purchase_price'),
        field_data.get('offer_price'),
    ))
    _set_sourced_value(data, sources, 'sales_price', sales_price, 'contract_document')

    _set_sourced_value(
        data, sources, 'effective_date',
        _format_date(field_data.get('effective_date')),
        'contract_document',
    )
    closing_date = _format_date(_first_filled(
        field_data.get('closing_date'),
        field_data.get('close_date'),
        field_data.get('proposed_close_date'),
    ))
    _set_sourced_value(data, sources, 'closing_date', closing_date, 'contract_document')

    _set_sourced_value(
        data, sources, 'option_fee',
        _format_currency(field_data.get('option_fee')),
        'contract_document',
    )
    option_days = field_data.get('option_period_days')
    if _is_filled(option_days):
        data['option_period_days'] = str(option_days)
        sources['option_period_days'] = 'contract_document'

    _set_sourced_value(
        data, sources, 'earnest_money',
        _format_currency(field_data.get('earnest_money')),
        'contract_document',
    )
    _set_sourced_value(
        data, sources, 'financing_type',
        field_data.get('financing_type'),
        'contract_document',
    )
    _set_sourced_value(
        data, sources, 'title_company',
        field_data.get('title_company'),
        'contract_document',
    )
    _set_sourced_value(
        data, sources, 'escrow_officer',
        field_data.get('escrow_officer'),
        'contract_document',
    )
    survey = _first_filled(field_data.get('survey_choice'), field_data.get('survey_furnished_by'))
    _set_sourced_value(data, sources, 'survey_choice', survey, 'contract_document')

    hoa = _format_hoa(_first_filled(field_data.get('hoa_applicable'), field_data.get('has_hoa')))
    _set_sourced_value(data, sources, 'has_hoa', hoa, 'contract_document')

    buyer_commission = _format_buyer_commission(
        field_data.get('buyer_agent_commission_percent') or field_data.get('buyer_agent_percent'),
        field_data.get('buyer_agent_commission_flat') or field_data.get('buyer_agent_flat'),
    )
    _set_sourced_value(data, sources, 'buyer_commission', buyer_commission, 'contract_document')


def _apply_accepted_contract_fields(data, sources, accepted_contract):
    if not accepted_contract:
        return

    _set_sourced_value(
        data, sources, 'sales_price',
        _format_currency(accepted_contract.accepted_price),
        'accepted_contract',
    )
    _set_sourced_value(
        data, sources, 'effective_date',
        _format_date(accepted_contract.effective_date),
        'accepted_contract',
    )
    _set_sourced_value(
        data, sources, 'closing_date',
        _format_date(accepted_contract.closing_date),
        'accepted_contract',
    )
    if _is_filled(accepted_contract.option_period_days):
        data['option_period_days'] = str(accepted_contract.option_period_days)
        sources['option_period_days'] = 'accepted_contract'
    _set_sourced_value(
        data, sources, 'financing_type',
        accepted_contract.financing_type,
        'accepted_contract',
    )
    _set_sourced_value(
        data, sources, 'title_company',
        accepted_contract.title_company,
        'accepted_contract',
    )
    _set_sourced_value(
        data, sources, 'escrow_officer',
        accepted_contract.escrow_officer,
        'accepted_contract',
    )
    survey = _first_filled(accepted_contract.survey_choice, accepted_contract.survey_furnished_by)
    _set_sourced_value(data, sources, 'survey_choice', survey, 'accepted_contract')

    hoa = _format_hoa(accepted_contract.hoa_applicable)
    _set_sourced_value(data, sources, 'has_hoa', hoa, 'accepted_contract')

    buyer_commission = _format_buyer_commission(
        accepted_contract.buyer_agent_commission_percent,
        accepted_contract.buyer_agent_commission_flat,
    )
    _set_sourced_value(data, sources, 'buyer_commission', buyer_commission, 'accepted_contract')

    frozen = accepted_contract.frozen_terms if isinstance(getattr(accepted_contract, 'frozen_terms', None), dict) else {}
    extra = accepted_contract.extra_data if isinstance(getattr(accepted_contract, 'extra_data', None), dict) else {}
    for key in ('option_fee', 'earnest_money'):
        raw = _first_filled(frozen.get(key), extra.get(key))
        _set_sourced_value(data, sources, key, _format_currency(raw), 'accepted_contract')


def _apply_contract_overrides(data, sources, overrides):
    if not isinstance(overrides, dict) or not overrides:
        return

    def clean(value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    currency_keys = ('sales_price', 'option_fee', 'earnest_money', 'buyer_commission')
    date_keys = ('effective_date', 'closing_date')
    plain_keys = (
        'option_period_days', 'financing_type', 'title_company',
        'escrow_officer', 'survey_choice',
    )

    for key in currency_keys:
        value = clean(overrides.get(key))
        if value is not None:
            formatted = _format_percent(value) if key == 'buyer_commission' else None
            data[key] = formatted or _format_currency(value) or value
            sources[key] = 'override'

    for key in date_keys:
        value = clean(overrides.get(key))
        if value is not None:
            data[key] = _format_date(value)
            sources[key] = 'override'

    for key in plain_keys:
        value = clean(overrides.get(key))
        if value is not None:
            data[key] = value
            sources[key] = 'override'

    has_hoa = clean(overrides.get('has_hoa'))
    if has_hoa is not None:
        hoa = _format_hoa(has_hoa)
        data['has_hoa'] = hoa or has_hoa
        sources['has_hoa'] = 'override'


def build_contract_terms(transaction, accepted_contract=None, documents=None):
    """Merge executed-contract terms from the accepted contract and bootstrapped contract PDF."""
    data = {}
    sources = {}
    documents = documents or []

    contract_doc = next(
        (
            doc for doc in documents
            if getattr(doc, 'template_slug', None) in PURCHASE_CONTRACT_TEMPLATE_SLUGS
            and isinstance(getattr(doc, 'field_data', None), dict)
            and doc.field_data
        ),
        None,
    )
    if contract_doc:
        _apply_contract_document_fields(data, sources, contract_doc.field_data)

    _apply_accepted_contract_fields(data, sources, accepted_contract)

    extra_data = getattr(transaction, 'extra_data', None) or {}
    _apply_contract_overrides(data, sources, extra_data.get('contract_terms_overrides'))

    if not _has_display_values(data):
        return None

    data['_sources'] = sources
    data['_completeness'] = _compute_contract_completeness(data)
    return data
