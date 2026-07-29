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


def get_user_contact_groups(org_id: int, user_id: int, active_only: bool = True):
    """
    Get contact groups for a specific user (cached 5 min).

    Args:
        org_id: Organization ID
        user_id: Owning user ID
        active_only: When True (default), only return active groups

    Returns:
        List of ContactGroup objects

    Only the ordered IDs are cached, never the ORM instances. Caching instances
    across requests hands out rows whose session is gone: once the request that
    warmed the cache commits, those instances are expired, and the next request
    to touch an attribute raises DetachedInstanceError. Rehydrating by primary
    key keeps every row bound to the caller's session and still avoids the
    filtered scan and sort on every call.
    """
    from models import ContactGroup

    cache_key = f'contact_groups_{org_id}_{user_id}_{"active" if active_only else "all"}'

    cached_ids = _get_cached(cache_key)
    if cached_ids is not None:
        if not cached_ids:
            return []
        rows = ContactGroup.query.filter(ContactGroup.id.in_(cached_ids)).all()
        by_id = {row.id: row for row in rows}
        hydrated = [by_id[gid] for gid in cached_ids if gid in by_id]
        # A group deleted since we cached means the cache is stale; rebuild it.
        if len(hydrated) == len(cached_ids):
            return hydrated
        _delete_cached(cache_key)

    query = ContactGroup.query.filter_by(
        organization_id=org_id,
        user_id=user_id,
    )
    if active_only:
        query = query.filter_by(is_active=True)

    result = query.order_by(ContactGroup.sort_order, ContactGroup.id).all()
    _set_cached(cache_key, [row.id for row in result])
    return result


def get_org_contact_groups(org_id: int):
    """Deprecated: org-wide groups no longer exist.

    Kept as a thin shim that returns an empty list so stale imports fail softly
    in tests/scripts. Prefer get_user_contact_groups / contact_group_service.
    """
    import warnings
    warnings.warn(
        'get_org_contact_groups is deprecated; use get_user_contact_groups',
        DeprecationWarning,
        stacklevel=2,
    )
    return []


def get_org_transaction_types(org_id: int):
    """
    Get all active transaction types for an organization (cached 5 min).
    
    Args:
        org_id: Organization ID
        
    Returns:
        List of TransactionType objects

    Caches ordered IDs only, for the same reason as get_user_contact_groups.
    """
    from models import TransactionType

    cache_key = f'transaction_types_{org_id}'

    cached_ids = _get_cached(cache_key)
    if cached_ids is not None:
        if not cached_ids:
            return []
        rows = TransactionType.query.filter(
            TransactionType.id.in_(cached_ids)
        ).all()
        by_id = {row.id: row for row in rows}
        hydrated = [by_id[tid] for tid in cached_ids if tid in by_id]
        if len(hydrated) == len(cached_ids):
            return hydrated
        _delete_cached(cache_key)

    result = TransactionType.query.filter_by(
        organization_id=org_id,
        is_active=True
    ).order_by(TransactionType.sort_order).all()

    _set_cached(cache_key, [row.id for row in result])
    return result


def clear_user_contact_groups_cache(org_id: int, user_id: int):
    """Clear the contact groups cache for a specific user."""
    _delete_cached(f'contact_groups_{org_id}_{user_id}_active')
    _delete_cached(f'contact_groups_{org_id}_{user_id}_all')


def clear_org_contact_groups_cache(org_id: int):
    """Clear contact group caches for every user in an organization."""
    from models import User

    # Drop any leftover org-level key
    _delete_cached(f'contact_groups_{org_id}')

    user_ids = [
        row[0]
        for row in User.query.filter_by(organization_id=org_id).with_entities(User.id).all()
    ]
    for user_id in user_ids:
        clear_user_contact_groups_cache(org_id, user_id)


def clear_org_transaction_types_cache(org_id: int):
    """Clear the transaction types cache for an organization."""
    _delete_cached(f'transaction_types_{org_id}')
