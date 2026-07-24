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

# Keep in sync with frontend/analytics.js blocked key parts.
BLOCKED_KEY_PARTS = (
    'address', 'brokerage', 'company', 'content', 'description', 'email',
    'first_name', 'last_name', 'message', 'name', 'notes', 'phone', 'subject',
)

EVENT_SCHEMA_VERSION = 2
ACTIVATION_EXPERIENCE_VERSION = 'retention_v2'


def _safe_properties(properties):
    """Return scalar, non-PII event properties only."""
    cleaned = {}
    for key, value in (properties or {}).items():
        normalized = str(key).strip().lower()
        if not normalized or any(part in normalized for part in BLOCKED_KEY_PARTS):
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


def opaque_user_id(user_id):
    return f'user_{int(user_id)}'


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
        if 'event_schema_version' not in safe:
            safe['event_schema_version'] = (
                current_app.config.get(
                    'ACTIVATION_EVENT_SCHEMA_VERSION', EVENT_SCHEMA_VERSION
                )
            )
        if 'activation_experience_version' not in safe:
            safe['activation_experience_version'] = (
                current_app.config.get(
                    'ACTIVATION_EXPERIENCE_VERSION',
                    ACTIVATION_EXPERIENCE_VERSION,
                )
            )
        client.capture(
            str(event),
            distinct_id=opaque_user_id(user_id),
            properties=safe,
        )
        return True
    except Exception:
        logger.exception('PostHog capture failed event=%s user_id=%s',
                         event, user_id)
        return False


def capture_anonymous(event, *, distinct_id=None, properties=None):
    """Capture an aggregate event not attributed to a known user."""
    try:
        client = _get_client()
        if client is None:
            return False
        safe = _safe_properties(properties)
        safe['event_schema_version'] = current_app.config.get(
            'ACTIVATION_EVENT_SCHEMA_VERSION', EVENT_SCHEMA_VERSION
        )
        client.capture(
            str(event),
            distinct_id=distinct_id or 'anonymous',
            properties=safe,
        )
        return True
    except Exception:
        logger.exception('PostHog anonymous capture failed event=%s', event)
        return False


def identify_user(user, *, set_properties=None, set_once_properties=None):
    """Upsert privacy-safe person properties for an opaque user id."""
    try:
        if user is None or not getattr(user, 'id', None):
            return False
        client = _get_client()
        if client is None:
            return False

        distinct_id = opaque_user_id(user.id)
        payload = {}
        cleaned_set = _safe_properties(set_properties)
        cleaned_once = _safe_properties(set_once_properties)
        if cleaned_set:
            payload['$set'] = cleaned_set
        if cleaned_once:
            payload['$set_once'] = cleaned_once
        if not payload:
            return True

        # Prefer the dedicated identify API when available.
        if hasattr(client, 'identify'):
            client.identify(distinct_id, properties=payload)
        else:
            client.capture(
                '$identify',
                distinct_id=distinct_id,
                properties=payload,
            )
        return True
    except Exception:
        logger.exception('PostHog identify failed user_id=%s',
                         getattr(user, 'id', None))
        return False
