"""
Field Transforms

Functions that transform raw values into formatted strings
for DocuSeal fields. Each transform is registered by name
and can be referenced in YAML field definitions.

Usage in YAML:
    - field_key: list_price
      docuseal_field: "List Price"
      source: form.list_price
      transform: currency
"""

import logging
import re
from datetime import datetime, date
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Type alias for transform functions
TransformFunc = Callable[[Any], str]


def transform_currency(value: Any) -> str:
    """
    Format a number as US currency.
    
    Examples:
        500000 -> "$500,000.00"
        1234.5 -> "$1,234.50"
        "500000" -> "$500,000.00"
    """
    if value is None:
        return ""
    
    try:
        # Handle string input
        if isinstance(value, str):
            # Remove existing formatting
            value = value.replace('$', '').replace(',', '').strip()
            if not value:
                return ""
            value = float(value)
        
        return f"${value:,.2f}"
    except (ValueError, TypeError):
        logger.warning(f"Could not format as currency: {value}")
        return str(value) if value else ""


def transform_currency_no_cents(value: Any) -> str:
    """
    Format a number as US currency without cents.
    
    Examples:
        500000 -> "$500,000"
        1234.5 -> "$1,235"
    """
    if value is None:
        return ""
    
    try:
        if isinstance(value, str):
            value = value.replace('$', '').replace(',', '').strip()
            if not value:
                return ""
            value = float(value)
        
        return f"${int(round(value)):,}"
    except (ValueError, TypeError):
        logger.warning(f"Could not format as currency: {value}")
        return str(value) if value else ""


def transform_percent(value: Any) -> str:
    """
    Format a number as a percentage.
    
    Examples:
        6 -> "6%"
        6.5 -> "6.5%"
        "6" -> "6%"
    """
    if value is None:
        return ""
    
    try:
        if isinstance(value, str):
            value = value.replace('%', '').strip()
            if not value:
                return ""
        
        # Convert and check if it's a whole number
        num = float(value)
        if num == int(num):
            return f"{int(num)}%"
        return f"{num}%"
    except (ValueError, TypeError):
        logger.warning(f"Could not format as percent: {value}")
        return str(value) if value else ""


def transform_date(value: Any) -> str:
    """
    Format a date in standard US format.
    
    Examples:
        "2026-01-15" -> "January 15, 2026"
        datetime(2026, 1, 15) -> "January 15, 2026"
        date(2026, 1, 15) -> "January 15, 2026"
    """
    if value is None:
        return ""
    
    try:
        if isinstance(value, (datetime, date)):
            return value.strftime("%B %d, %Y")
        
        if isinstance(value, str):
            if not value.strip():
                return ""
            # Try common formats
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"]:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.strftime("%B %d, %Y")
                except ValueError:
                    continue
        
        logger.warning(f"Could not parse date: {value}")
        return str(value)
    except Exception:
        logger.warning(f"Could not format as date: {value}")
        return str(value) if value else ""


def transform_date_short(value: Any) -> str:
    """
    Format a date in short US format.
    
    Examples:
        "2026-01-15" -> "01/15/2026"
    """
    if value is None:
        return ""
    
    try:
        if isinstance(value, (datetime, date)):
            return value.strftime("%m/%d/%Y")
        
        if isinstance(value, str):
            if not value.strip():
                return ""
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"]:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.strftime("%m/%d/%Y")
                except ValueError:
                    continue
        
        return str(value)
    except Exception:
        return str(value) if value else ""


def transform_phone(value: Any) -> str:
    """
    Format a phone number in US format.
    
    Examples:
        "7137254459" -> "(713) 725-4459"
        7137254459 -> "(713) 725-4459"
        "(713) 725-4459" -> "(713) 725-4459"
    """
    if value is None:
        return ""
    
    # Convert to string and extract digits
    phone_str = str(value)
    digits = re.sub(r'\D', '', phone_str)
    
    if not digits:
        return ""
    
    # Handle 10-digit US numbers
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    
    # Handle 11-digit with country code
    if len(digits) == 11 and digits[0] == '1':
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    
    # Return original if can't format
    return phone_str


def transform_uppercase(value: Any) -> str:
    """Convert to uppercase."""
    if value is None:
        return ""
    return str(value).upper()


def transform_lowercase(value: Any) -> str:
    """Convert to lowercase."""
    if value is None:
        return ""
    return str(value).lower()


def transform_titlecase(value: Any) -> str:
    """Convert to title case."""
    if value is None:
        return ""
    return str(value).title()


def transform_trim(value: Any) -> str:
    """Trim whitespace."""
    if value is None:
        return ""
    return str(value).strip()


def transform_none(value: Any) -> str:
    """No transformation - just convert to string."""
    if value is None:
        return ""
    return str(value)


# Registry of available transforms
TRANSFORMS: Dict[str, TransformFunc] = {
    'currency': transform_currency,
    'currency_no_cents': transform_currency_no_cents,
    'percent': transform_percent,
    'date': transform_date,
    'date_short': transform_date_short,
    'phone': transform_phone,
    'uppercase': transform_uppercase,
    'lowercase': transform_lowercase,
    'titlecase': transform_titlecase,
    'trim': transform_trim,
    'none': transform_none,
}


def get_transform(name: str) -> Optional[TransformFunc]:
    """Get a transform function by name."""
    return TRANSFORMS.get(name)


def apply_transform(value: Any, transform_name: Optional[str]) -> str:
    """
    Apply a named transform to a value.
    
    If transform_name is None or not found, returns str(value).
    """
    if value is None:
        return ""
    
    if not transform_name:
        return str(value)
    
    transform_func = get_transform(transform_name)
    if transform_func:
        return transform_func(value)
    
    logger.warning(f"Unknown transform: {transform_name}")
    return str(value)


def register_transform(name: str, func: TransformFunc) -> None:
    """
    Register a custom transform function.
    
    Use this to add new transforms without modifying this file:
        from services.documents.transforms import register_transform
        register_transform('county_name', my_county_formatter)
    """
    TRANSFORMS[name] = func
    logger.debug(f"Registered transform: {name}")

