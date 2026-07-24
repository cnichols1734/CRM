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

from datetime import datetime, timedelta

from models import db, ActivationEvent

logger = logging.getLogger(__name__)


def record_event(event, *, user=None, organization_id=None, user_id=None,
                 data=None, commit=True, once=False):
    """Append an activation event. Never raises.

    Pass either a ``user`` (org/user ids are pulled from it) or explicit
    ``organization_id`` / ``user_id``. ``data`` is optional JSON metadata.
    """
    try:
        if user is not None:
            organization_id = organization_id or getattr(user, 'organization_id', None)
            user_id = user_id or getattr(user, 'id', None)

        if once:
            existing = ActivationEvent.query.filter_by(
                event=event,
                organization_id=organization_id,
                user_id=user_id,
            ).first()
            if existing is not None:
                return existing

        entry = ActivationEvent(
            event=event,
            organization_id=organization_id,
            user_id=user_id,
            event_data=data or {},
        )
        db.session.add(entry)
        if commit:
            db.session.commit()

        try:
            from services.product_analytics import capture
            capture(
                event,
                user=user,
                user_id=user_id,
                organization_id=organization_id,
                properties=data,
            )
        except Exception:
            logger.exception('PostHog mirror failed for activation event %s', event)
        return entry
    except Exception:
        logger.exception('Failed to record activation event %s (org=%s user=%s)',
                         event, organization_id, user_id)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def record_daily_session(user):
    """Record at most one authenticated session event per user per UTC day."""
    today = datetime.utcnow().date()
    existing = ActivationEvent.query.filter(
        ActivationEvent.event == ActivationEvent.SESSION_STARTED,
        ActivationEvent.user_id == user.id,
        ActivationEvent.created_at >= datetime.combine(today, datetime.min.time()),
    ).first()
    if existing is not None:
        return existing
    return record_event(
        ActivationEvent.SESSION_STARTED,
        user=user,
        data={'day': today.isoformat()},
    )


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

    first_activations = (
        db.session.query(ActivationEvent.organization_id,
                         func.min(ActivationEvent.created_at))
        .filter(ActivationEvent.event == ActivationEvent.ACTIVATION_COMPLETED)
        .group_by(ActivationEvent.organization_id)
        .all()
    )
    activation_at = {
        org_id: ts for org_id, ts in first_activations if org_id is not None
    }
    quick_add_orgs = _quick_add_orgs_portable()

    total_signups = len(signup_at)
    activated = [
        org_id for org_id, ts in activation_at.items()
        if org_id in signup_at
        and timedelta(0) <= ts - signup_at[org_id] <= timedelta(hours=24)
    ]

    times_to_first = []
    for org in activated:
        if org not in first_contact_at:
            continue
        delta = first_contact_at[org] - signup_at[org]
        secs = delta.total_seconds()
        if secs >= 0:
            times_to_first.append(secs)
    times_to_first.sort()
    times_to_activation = [
        (activation_at[org_id] - signup_at[org_id]).total_seconds()
        for org_id in activated
    ]
    times_to_activation.sort()

    returned_d2_d7 = set()
    session_rows = ActivationEvent.query.filter_by(
        event=ActivationEvent.SESSION_STARTED
    ).all()
    for event in session_rows:
        signup = signup_at.get(event.organization_id)
        if signup and timedelta(days=1) <= event.created_at - signup < timedelta(days=8):
            returned_d2_d7.add(event.organization_id)

    stage_order = [
        (ActivationEvent.FOLLOW_UP_CREATED, 'follow-up'),
        (ActivationEvent.CONTACT_CREATED, 'contact'),
        (ActivationEvent.ACTIVATION_PATH_SELECTED, 'path selection'),
        (ActivationEvent.DASHBOARD_VIEWED, 'dashboard'),
        (ActivationEvent.ACCOUNT_CREATED, 'signup'),
    ]
    stalled_counts = {}
    all_events = {}
    for org_id, event_name in db.session.query(
        ActivationEvent.organization_id, ActivationEvent.event
    ).filter(ActivationEvent.organization_id.in_(signup_at.keys())).all():
        all_events.setdefault(org_id, set()).add(event_name)
    for org_id in signup_at:
        if org_id in activated:
            continue
        names = all_events.get(org_id, set())
        stage = next(
            (label for event_name, label in stage_order if event_name in names),
            'signup',
        )
        stalled_counts[stage] = stalled_counts.get(stage, 0) + 1
    stalled_stage, stalled_count = (
        max(stalled_counts.items(), key=lambda item: item[1])
        if stalled_counts else ('none', 0)
    )

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
        'median_seconds_to_activation': _median(times_to_activation),
        'd7_returned': len(returned_d2_d7),
        'd7_return_rate': (
            len(returned_d2_d7) / total_signups if total_signups else 0.0
        ),
        'stalled_stage': stalled_stage,
        'stalled_count': stalled_count,
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
