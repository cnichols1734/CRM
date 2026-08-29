"""Decide when the GA4 page tag may load gtag.js.

The measurement ID is public. Pytest and localhost never fetch Google.
"""

from flask import current_app, has_request_context, request

LOCAL_HOSTS = frozenset({'localhost', '127.0.0.1', '::1', '0.0.0.0'})


def measurement_id():
    return (current_app.config.get('GA4_MEASUREMENT_ID') or '').strip()


def hostname_from_host_header(host):
    """Hostname from a Host header, including IPv6 loopback.

    `127.0.0.1:5011` -> `127.0.0.1`
    `[::1]:5011` -> `::1`
    `[::1]` -> `::1`
    `::1` -> `::1`
    """
    raw = (host or '').strip().lower()
    if raw.startswith('['):
        end = raw.find(']')
        if end != -1:
            return raw[1:end]
        return raw
    if raw.count(':') > 1:
        return raw
    return raw.split(':', 1)[0]


def should_load_gtag():
    """True only when a real browser on a non-local host should load gtag.js."""
    if current_app.config.get('TESTING'):
        return False
    if not measurement_id():
        return False
    if not has_request_context():
        return False
    # Werkzeug's request.host splits on the first colon, so a Host of
    # `::1` becomes empty. Read the raw header first.
    raw = request.environ.get('HTTP_HOST') or request.host
    host = hostname_from_host_header(raw)
    return host not in LOCAL_HOSTS
