"""Privacy-safe, best-effort PostHog product analytics.

The CRM database remains the source of truth for durable activation milestones.
PostHog is only the diagnostic lens. Analytics must never block user work and
must never receive customer names, contact details, notes, or free-form text.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from flask import current_app

logger = logging.getLogger(__name__)

_client = None
_client_config = None

_BLOCKED_KEY_PARTS = {
    'address', 'brokerage', 'company', 'content', 'description', 'email',
    'first_name', 'last_name', 'message', 'name', 'notes', 'phone', 'subject',
}


def _safe_properties(properties):
    """Return scalar, non-PII event properties only."""
    cleaned = {}
    for key, value in (properties or {}).items():
        normalized = str(key).strip().lower()
        if not normalized or any(part in normalized for part in _BLOCKED_KEY_PARTS):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            cleaned[normalized] = value
        elif isinstance(value, (date, datetime)):
            cleaned[normalized] = value.isoformat()
        elif isinstance(value, str) and len(value) <= 80:
            cleaned[normalized] = value
    return cleaned


def _get_client():
    global _client, _client_config

    if current_app.testing:
        return None

    token = current_app.config.get('POSTHOG_PROJECT_TOKEN')
    host = current_app.config.get('POSTHOG_HOST', 'https://us.i.posthog.com')
    if not token or not current_app.config.get('POSTHOG_ENABLED', bool(token)):
        return None

    config = (token, host)
    if _client is not None and _client_config == config:
        return _client

    try:
        from posthog import Posthog
        _client = Posthog(token, host=host)
        _client_config = config
        return _client
    except Exception:
        logger.exception('PostHog client initialization failed')
        return None


def capture(event, *, user=None, user_id=None, organization_id=None,
            properties=None):
    """Capture one event using opaque IDs. Returns False on any failure."""
    try:
        if user is not None:
            user_id = user_id or getattr(user, 'id', None)
            organization_id = organization_id or getattr(
                user, 'organization_id', None
            )
        if not user_id:
            return False

        client = _get_client()
        if client is None:
            return False

        safe = _safe_properties(properties)
        if organization_id:
            safe['organization_id'] = int(organization_id)
        client.capture(
            str(event),
            distinct_id=f'user_{int(user_id)}',
            properties=safe,
        )
        return True
    except Exception:
        logger.exception('PostHog capture failed event=%s user_id=%s',
                         event, user_id)
        return False

