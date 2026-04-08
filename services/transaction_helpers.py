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


def build_listing_info(documents):
    """
    Build listing info dict from the listing agreement document's field_data.
    Returns None if no listing agreement exists or has no data.

    Used by both the transaction detail view and the extraction-status API.
    """
    listing_doc = next((d for d in documents if d.template_slug == 'listing-agreement'), None)
    if not listing_doc or listing_doc.status == 'pending' or not listing_doc.field_data:
        return None

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

    return common
