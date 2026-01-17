# utils.py
"""
Utility functions for the CRM application.
"""

import re
from typing import Optional


def slugify(text: str) -> str:
    """
    Convert text to URL-friendly slug.
    
    Args:
        text: Text to convert
        
    Returns:
        URL-friendly slug
    """
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    text = re.sub(r'^-+|-+$', '', text)
    return text


def generate_unique_slug(name: str, check_exists_func=None) -> str:
    """
    Generate a unique slug, appending numbers if needed.
    
    Args:
        name: Name to slugify
        check_exists_func: Optional function to check if slug exists.
                          If None, uses Organization.query.filter_by
                          
    Returns:
        Unique slug string
    """
    from models import Organization
    
    base_slug = slugify(name)
    slug = base_slug
    counter = 1
    
    if check_exists_func is None:
        check_exists_func = lambda s: Organization.query.filter_by(slug=s).first() is not None
    
    while check_exists_func(slug):
        slug = f"{base_slug}-{counter}"
        counter += 1
    
    return slug


def format_phone_number(phone: Optional[str]) -> Optional[str]:
    """
    Format phone number to (XXX) XXX-XXXX format.
    
    Args:
        phone: Phone number string
        
    Returns:
        Formatted phone number or None if invalid
    """
    if not phone:
        return None
        
    # Remove any non-digit characters
    digits = ''.join(filter(str.isdigit, phone))
    
    # Handle numbers with or without country code
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]  # Remove leading 1
    
    # If we don't have exactly 10 digits, return None
    if len(digits) != 10:
        return None
        
    # Format as (XXX) XXX-XXXX
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
