"""Decide when the GA4 page tag may load gtag.js.

The measurement ID is public. Pytest and localhost never fetch Google.
"""

from flask import current_app, has_request_context, request

LOCAL_HOSTS = frozenset({'localhost', '127.0.0.1', '::1', '0.0.0.0'})


def measurement_id():
    return (current_app.config.get('GA4_MEASUREMENT_ID') or '').strip()


def should_load_gtag():
    """True only when a real browser on a non-local host should load gtag.js."""
    if current_app.config.get('TESTING'):
        return False
    if not measurement_id():
        return False
    if not has_request_context():
        return False
    host = (request.host or '').split(':', 1)[0].lower()
    return host not in LOCAL_HOSTS
