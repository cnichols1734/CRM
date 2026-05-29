"""New-user activation tracking.

A thin, best-effort wrapper around the ``ActivationEvent`` append-only log.
The whole point of this module is to *measure* the funnel that is currently
invisible: signup -> first contact -> habit. It must never get in the way of
the thing it is measuring, so every write is wrapped and failures are swallowed
(logged, then rolled back) rather than raised.

Usage:
    from services.activation_service import record_event, ActivationEvent
    record_event(ActivationEvent.CONTACT_CREATED, user=current_user,
                 data={'source': 'quick_add'})
"""
from __future__ import annotations

import logging

from models import db, ActivationEvent

logger = logging.getLogger(__name__)


def record_event(event, *, user=None, organization_id=None, user_id=None,
                 data=None, commit=True):
    """Append an activation event. Never raises.

    Pass either a ``user`` (org/user ids are pulled from it) or explicit
    ``organization_id`` / ``user_id``. ``data`` is optional JSON metadata.
    """
    try:
        if user is not None:
            organization_id = organization_id or getattr(user, 'organization_id', None)
            user_id = user_id or getattr(user, 'id', None)

        entry = ActivationEvent(
            event=event,
            organization_id=organization_id,
            user_id=user_id,
            event_data=data or {},
        )
        db.session.add(entry)
        if commit:
            db.session.commit()
        return entry
    except Exception:
        logger.exception('Failed to record activation event %s (org=%s user=%s)',
                         event, organization_id, user_id)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def funnel_summary():
    """Return a small activation funnel summary for reporting.

    Computed entirely from the event log so it stays correct as new event
    types are added. Time-to-first-contact is measured per organization as the
    gap between its earliest ``account_created`` and earliest ``contact_created``.
    """
    from sqlalchemy import func

    signups = (
        db.session.query(ActivationEvent.organization_id,
                         func.min(ActivationEvent.created_at))
        .filter(ActivationEvent.event == ActivationEvent.ACCOUNT_CREATED)
        .group_by(ActivationEvent.organization_id)
        .all()
    )
    signup_at = {org_id: ts for org_id, ts in signups if org_id is not None}

    first_contacts = (
        db.session.query(ActivationEvent.organization_id,
                         func.min(ActivationEvent.created_at))
        .filter(ActivationEvent.event == ActivationEvent.CONTACT_CREATED)
        .group_by(ActivationEvent.organization_id)
        .all()
    )
    first_contact_at = {org_id: ts for org_id, ts in first_contacts if org_id is not None}

    quick_add_orgs = _quick_add_orgs_portable()

    total_signups = len(signup_at)
    activated = [org for org in signup_at if org in first_contact_at]

    times_to_first = []
    for org in activated:
        delta = first_contact_at[org] - signup_at[org]
        secs = delta.total_seconds()
        if secs >= 0:
            times_to_first.append(secs)
    times_to_first.sort()

    def _median(values):
        if not values:
            return None
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2

    return {
        'total_signups': total_signups,
        'activated': len(activated),
        'activation_rate': (len(activated) / total_signups) if total_signups else 0.0,
        'quick_add_orgs': len(quick_add_orgs),
        'median_seconds_to_first_contact': _median(times_to_first),
        'avg_seconds_to_first_contact': (sum(times_to_first) / len(times_to_first))
                                        if times_to_first else None,
    }


def _quick_add_orgs_portable():
    """SQLite-friendly fallback for counting quick-add orgs (JSON ops vary)."""
    orgs = set()
    rows = (
        db.session.query(ActivationEvent.organization_id, ActivationEvent.event_data)
        .filter(ActivationEvent.event == ActivationEvent.CONTACT_CREATED)
        .all()
    )
    for org_id, payload in rows:
        if org_id is None:
            continue
        if isinstance(payload, dict) and payload.get('source') == 'quick_add':
            orgs.add(org_id)
    return orgs
