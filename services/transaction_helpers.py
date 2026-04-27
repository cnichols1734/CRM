"""
Shared helpers for transaction views and API endpoints.
"""

from datetime import datetime


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


def apply_listing_info_overrides(listing_info, overrides):
    """Apply user overrides from transaction.extra_data onto extracted listing info."""
    if not overrides:
        return listing_info

    data = dict(listing_info or {})

    def clean(value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    list_price = clean(overrides.get('list_price'))
    if list_price is not None:
        data['list_price'] = _format_currency(list_price)

    start_date = clean(overrides.get('listing_start_date'))
    if start_date is not None:
        data['listing_start_date'] = _format_date(start_date)

    end_date = clean(overrides.get('listing_end_date'))
    if end_date is not None:
        data['listing_end_date'] = _format_date(end_date)

    total_commission = clean(overrides.get('total_commission'))
    if total_commission is not None:
        data['total_commission'] = _format_percent(total_commission) or _format_currency(total_commission)

    buyer_commission = clean(overrides.get('buyer_commission'))
    if buyer_commission is not None:
        data['buyer_commission'] = _format_percent(buyer_commission) or _format_currency(buyer_commission)

    listing_side_commission = clean(overrides.get('listing_side_commission'))
    if listing_side_commission is not None:
        data['listing_side_commission'] = _format_percent(listing_side_commission) or _format_currency(listing_side_commission)

    protection_days = clean(overrides.get('protection_period_days'))
    if protection_days is not None:
        data['protection_period_days'] = protection_days

    financing_types = clean(overrides.get('financing_types'))
    if financing_types is not None:
        data['financing_types'] = financing_types

    has_hoa = clean(overrides.get('has_hoa'))
    if has_hoa is not None:
        normalized_hoa = has_hoa.lower()
        if normalized_hoa in ('yes', 'true', '1'):
            data['has_hoa'] = 'Yes'
        elif normalized_hoa in ('no', 'false', '0'):
            data['has_hoa'] = 'No'
        else:
            data['has_hoa'] = has_hoa

    # Keep commission rendering in the standard split-commission mode when editing.
    if data.get('commission_type') != '5b':
        data['commission_type'] = '5a'
        if not data.get('listing_side_commission'):
            derived = _derive_listing_side_commission(data.get('total_commission'), data.get('buyer_commission'))
            if derived:
                data['listing_side_commission'] = derived

    return data or None


def build_listing_info(documents, overrides=None):
    """
    Build listing info dict from the listing agreement document's field_data.
    Returns None if no listing agreement exists or has no data.

    Used by both the transaction detail view and the extraction-status API.
    """
    listing_doc = next((d for d in documents if d.template_slug == 'listing-agreement'), None)
    if not listing_doc or listing_doc.status == 'pending' or not listing_doc.field_data:
        return apply_listing_info_overrides(None, overrides)

    field_data = listing_doc.field_data

    listing_only_percent = field_data.get('listing_only_percent')
    listing_only_flat = field_data.get('listing_only_flat')
    is_listing_broker_only = bool(listing_only_percent or listing_only_flat)

    hoa_raw = field_data.get('has_hoa')
    if hoa_raw == 'yes':
        hoa_display = 'Yes'
    elif hoa_raw == 'no':
        hoa_display = 'No'
    else:
        hoa_display = None

    common = {
        'list_price': _format_currency(field_data.get('list_price')),
        'listing_start_date': _format_date(field_data.get('listing_start_date')),
        'listing_end_date': _format_date(field_data.get('listing_end_date')),
        'protection_period_days': field_data.get('protection_period_days'),
        'financing_types': field_data.get('financing_types'),
        'has_hoa': hoa_display,
        'special_provisions': field_data.get('special_provisions'),
    }

    if is_listing_broker_only:
        broker_fee = _format_percent(listing_only_percent) or _format_currency(listing_only_flat)
        common.update({
            'commission_type': '5b',
            'broker_fee': broker_fee,
        })
    else:
        buyer_commission = _format_percent(field_data.get('buyer_agent_percent'))
        if not buyer_commission:
            buyer_commission = _format_currency(field_data.get('buyer_agent_flat'))
        common.update({
            'commission_type': '5a',
            'total_commission': _format_percent(field_data.get('total_commission')),
            'buyer_commission': buyer_commission,
        })
        common['listing_side_commission'] = _derive_listing_side_commission(
            common.get('total_commission'),
            buyer_commission,
        )

    return apply_listing_info_overrides(common, overrides)
