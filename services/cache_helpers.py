# services/cache_helpers.py
"""
Cached query helpers for frequently accessed, rarely changing data.
Uses simple dict-based caching to reduce database queries for org-scoped lookup tables.

Note: Using a simple dict cache instead of Flask-Caching due to initialization timing issues.
This cache is cleared on app restart which is acceptable for this use case.
"""

import time

# Simple in-memory cache: {key: (value, expiry_timestamp)}
_cache = {}
_CACHE_TIMEOUT = 300  # 5 minutes


def _get_cached(key):
    """Get value from cache if not expired."""
    if key in _cache:
        value, expiry = _cache[key]
        if time.time() < expiry:
            return value
        else:
            del _cache[key]
    return None


def _set_cached(key, value, timeout=_CACHE_TIMEOUT):
    """Set value in cache with expiry."""
    _cache[key] = (value, time.time() + timeout)


def _delete_cached(key):
    """Delete value from cache."""
    if key in _cache:
        del _cache[key]


def get_org_contact_groups(org_id: int):
    """
    Get all contact groups for an organization (cached 5 min).
    
    Args:
        org_id: Organization ID
        
    Returns:
        List of ContactGroup objects
    """
    from models import ContactGroup
    
    cache_key = f'contact_groups_{org_id}'
    
    result = _get_cached(cache_key)
    if result is not None:
        return result
    
    # Query from database
    result = ContactGroup.query.filter_by(
        organization_id=org_id
    ).order_by(ContactGroup.sort_order).all()
    
    _set_cached(cache_key, result)
    return result


def get_org_transaction_types(org_id: int):
    """
    Get all active transaction types for an organization (cached 5 min).
    
    Args:
        org_id: Organization ID
        
    Returns:
        List of TransactionType objects
    """
    from models import TransactionType
    
    cache_key = f'transaction_types_{org_id}'
    
    result = _get_cached(cache_key)
    if result is not None:
        return result
    
    # Query from database
    result = TransactionType.query.filter_by(
        organization_id=org_id,
        is_active=True
    ).order_by(TransactionType.sort_order).all()
    
    _set_cached(cache_key, result)
    return result


def clear_org_contact_groups_cache(org_id: int):
    """Clear the contact groups cache for an organization."""
    _delete_cached(f'contact_groups_{org_id}')


def clear_org_transaction_types_cache(org_id: int):
    """Clear the transaction types cache for an organization."""
    _delete_cached(f'transaction_types_{org_id}')
