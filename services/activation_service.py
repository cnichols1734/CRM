"""Activation and retention tracking.

A thin, best-effort wrapper around the ``ActivationEvent`` append-only log.
Analytics must never get in the way of the thing it is measuring, so every
write is wrapped and failures are swallowed (logged, then rolled back).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from models import (
    ActivationEvent, Contact, Organization, Task, TaskSubtype, User, db,
)

logger = logging.getLogger(__name__)

FOLLOW_UP_SUBTYPE_NAMES = ('Follow-up', 'Follow Up')

FUNNEL_STEP_RANK = {
    ActivationEvent.ACCOUNT_CREATED: 10,
    ActivationEvent.DASHBOARD_VIEWED: 20,
    ActivationEvent.ACTIVATION_PATH_SELECTED: 30,
    ActivationEvent.CONTACT_CREATED: 40,
    ActivationEvent.FOLLOW_UP_CREATED: 50,
    ActivationEvent.ACTIVATION_COMPLETED: 60,
}

RETENTION_STAGES = (
    'activation_observing',
    'unactivated_no_path',
    'unactivated_path_stalled',
    'unactivated_returning',
    'activated_observing',
    'activated_retained',
    'activated_idle',
    'established_active',
    'established_at_risk',
    'established_dormant',
)


def _follow_up_subtype_ids(organization_id):
    return [
        row.id for row in TaskSubtype.query.filter(
            TaskSubtype.organization_id == organization_id,
            TaskSubtype.name.in_(FOLLOW_UP_SUBTYPE_NAMES),
        ).all()
    ]


def is_follow_up_task(task):
    """Return True when a task is a dated follow-up of the activation subtype."""
    if task is None or not getattr(task, 'due_date', None):
        return False
    subtype = getattr(task, 'task_subtype', None)
    if subtype is not None:
        return subtype.name in FOLLOW_UP_SUBTYPE_NAMES
    if not task.subtype_id:
        return False
    subtype = TaskSubtype.query.get(task.subtype_id)
    return bool(subtype and subtype.name in FOLLOW_UP_SUBTYPE_NAMES)


def is_user_activated(user):
    """Shared activation rule: contact exists + dated follow-up task."""
    if user is None or not user.id:
        return False
    has_contact = Contact.query.filter_by(user_id=user.id).first() is not None
    if not has_contact:
        return False
    subtype_ids = _follow_up_subtype_ids(user.organization_id)
    if not subtype_ids:
        return False
    return Task.query.filter(
        Task.assigned_to_id == user.id,
        Task.subtype_id.in_(subtype_ids),
        Task.due_date.isnot(None),
    ).first() is not None


def is_customer_user(user):
    """Exclude platform-admin orgs and inactive accounts from customer cohorts."""
    if user is None or not user.organization_id:
        return False
    org = getattr(user, 'organization', None) or Organization.query.get(
        user.organization_id
    )
    if org is None:
        return False
    if getattr(org, 'is_platform_admin', False):
        return False
    if getattr(org, 'status', None) not in (None, 'active'):
        return False
    return True


def count_bucket(value):
    """Privacy-safe count buckets for PostHog properties."""
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return '0'
    if n == 1:
        return '1'
    if n <= 5:
        return '2_5'
    if n <= 20:
        return '6_20'
    if n <= 100:
        return '21_100'
    return '100_plus'


def elapsed_bucket(seconds):
    try:
        n = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        n = 0
    if n < 5:
        return '0_5s'
    if n < 30:
        return '5_30s'
    if n < 120:
        return '30_120s'
    if n < 600:
        return '2_10m'
    return '10m_plus'


def _account_age_days(user, now=None):
    now = now or datetime.utcnow()
    created = getattr(user, 'created_at', None)
    if not created:
        return 0
    return max(0, (now - created).days)


def _event_once_exists(event, *, user_id, organization_id, stage=None):
    query = ActivationEvent.query.filter_by(
        event=event,
        organization_id=organization_id,
        user_id=user_id,
    )
    if stage is None:
        return query.first()
    for row in query.all():
        data = row.event_data or {}
        if isinstance(data, dict) and data.get('stage') == stage:
            return row
    return None


def build_event_context(user, *, data=None, surface=None, source=None):
    """Attach canonical privacy-safe context to every durable event."""
    from flask import current_app

    payload = dict(data or {})
    if user is None:
        return payload

    org = getattr(user, 'organization', None)
    activated = is_user_activated(user)
    path_event = ActivationEvent.query.filter_by(
        user_id=user.id,
        event=ActivationEvent.ACTIVATION_PATH_SELECTED,
    ).order_by(ActivationEvent.created_at.asc()).first()
    selected_path = None
    if path_event and isinstance(path_event.event_data, dict):
        selected_path = path_event.event_data.get('path')

    highest = ActivationEvent.ACCOUNT_CREATED
    highest_rank = 0
    for row in ActivationEvent.query.filter_by(user_id=user.id).all():
        rank = FUNNEL_STEP_RANK.get(row.event, 0)
        if rank > highest_rank:
            highest_rank = rank
            highest = row.event

    payload.setdefault(
        'event_schema_version',
        current_app.config.get('ACTIVATION_EVENT_SCHEMA_VERSION', 2),
    )
    payload.setdefault(
        'activation_experience_version',
        current_app.config.get('ACTIVATION_EXPERIENCE_VERSION', 'retention_v2'),
    )
    if source:
        payload.setdefault('source', source)
    if surface:
        payload.setdefault('surface', surface)
    payload.setdefault(
        'subscription_tier',
        getattr(org, 'subscription_tier', None) or 'free',
    )
    payload.setdefault('account_age_days', _account_age_days(user))
    payload.setdefault('activated', activated)
    if selected_path:
        payload.setdefault('selected_path', selected_path)
    payload.setdefault('highest_funnel_step', highest)
    return payload


def sync_person_properties(user, *, extra_set=None, extra_set_once=None):
    """Mirror current retention state onto the PostHog person profile."""
    try:
        from services.product_analytics import identify_user

        if user is None or not is_customer_user(user):
            return False

        stage_row = ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.RETENTION_STAGE_CHANGED,
        ).order_by(ActivationEvent.created_at.desc()).first()
        stage = None
        if stage_row and isinstance(stage_row.event_data, dict):
            stage = stage_row.event_data.get('current')

        path_event = ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.ACTIVATION_PATH_SELECTED,
        ).order_by(ActivationEvent.created_at.asc()).first()
        selected_path = None
        if path_event and isinstance(path_event.event_data, dict):
            selected_path = path_event.event_data.get('path')

        friction = ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.FRICTION_RESPONSE,
        ).order_by(ActivationEvent.created_at.desc()).first()
        friction_reason = None
        if friction and isinstance(friction.event_data, dict):
            friction_reason = friction.event_data.get('reason')

        churn = ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.CHURN_REASON,
        ).order_by(ActivationEvent.created_at.desc()).first()
        churn_reason = None
        if churn and isinstance(churn.event_data, dict):
            churn_reason = churn.event_data.get('reason')

        session_row = ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.SESSION_STARTED,
        ).order_by(ActivationEvent.created_at.desc()).first()
        meaningful = ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.MEANINGFUL_ACTION,
        ).order_by(ActivationEvent.created_at.desc()).first()

        set_props = {
            'subscription_tier': getattr(
                getattr(user, 'organization', None), 'subscription_tier', 'free'
            ),
            'activated': is_user_activated(user),
            'activated_within_24h': _activated_within_hours(user, 24),
            'has_follow_up': _has_follow_up(user),
            'selected_path': selected_path,
            'highest_funnel_step': _highest_funnel_step(user),
            'retention_stage': stage,
            'last_session_day': (
                session_row.created_at.date().isoformat()
                if session_row else None
            ),
            'last_meaningful_day': (
                meaningful.created_at.date().isoformat()
                if meaningful else None
            ),
            'friction_reason': friction_reason,
            'churn_reason': churn_reason,
        }
        if extra_set:
            set_props.update(extra_set)

        set_once = {
            'signup_at': (
                user.created_at.date().isoformat() if user.created_at else None
            ),
        }
        account = ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.ACCOUNT_CREATED,
        ).order_by(ActivationEvent.created_at.asc()).first()
        if account and isinstance(account.event_data, dict):
            for key in (
                'source', 'utm_source', 'utm_medium', 'utm_campaign',
                'utm_content', 'ref', 'activation_experience_version',
            ):
                if account.event_data.get(key) is not None:
                    set_once[key] = account.event_data.get(key)
        if extra_set_once:
            set_once.update(extra_set_once)

        return identify_user(
            user,
            set_properties=set_props,
            set_once_properties=set_once,
        )
    except Exception:
        logger.exception('Failed to sync person properties user_id=%s',
                         getattr(user, 'id', None))
        return False


def _has_follow_up(user):
    subtype_ids = _follow_up_subtype_ids(user.organization_id)
    if not subtype_ids:
        return False
    return Task.query.filter(
        Task.assigned_to_id == user.id,
        Task.subtype_id.in_(subtype_ids),
        Task.due_date.isnot(None),
    ).first() is not None


def _highest_funnel_step(user):
    highest = ActivationEvent.ACCOUNT_CREATED
    highest_rank = 0
    for row in ActivationEvent.query.filter_by(user_id=user.id).all():
        rank = FUNNEL_STEP_RANK.get(row.event, 0)
        if rank > highest_rank:
            highest_rank = rank
            highest = row.event
    return highest


def _activated_within_hours(user, hours):
    account = ActivationEvent.query.filter_by(
        user_id=user.id,
        event=ActivationEvent.ACCOUNT_CREATED,
    ).order_by(ActivationEvent.created_at.asc()).first()
    completed = ActivationEvent.query.filter_by(
        user_id=user.id,
        event=ActivationEvent.ACTIVATION_COMPLETED,
    ).order_by(ActivationEvent.created_at.asc()).first()
    if not account or not completed:
        return False
    delta = completed.created_at - account.created_at
    return timedelta(0) <= delta <= timedelta(hours=hours)


def record_event(event, *, user=None, organization_id=None, user_id=None,
                 data=None, commit=True, once=False, once_stage=None,
                 surface=None, source=None, sync_person=True):
    """Append an activation/retention event. Never raises."""
    try:
        if user is not None:
            organization_id = organization_id or getattr(user, 'organization_id', None)
            user_id = user_id or getattr(user, 'id', None)

        if once:
            existing = _event_once_exists(
                event,
                user_id=user_id,
                organization_id=organization_id,
                stage=once_stage,
            )
            if existing is not None:
                return existing

        payload = build_event_context(
            user, data=data, surface=surface, source=source,
        ) if user is not None else dict(data or {})

        entry = ActivationEvent(
            event=event,
            organization_id=organization_id,
            user_id=user_id,
            event_data=payload,
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
                properties=payload,
            )
        except Exception:
            logger.exception('PostHog mirror failed for activation event %s', event)

        if sync_person and user is not None:
            try:
                sync_person_properties(user)
            except Exception:
                logger.exception('Person property sync failed for %s', event)
        return entry
    except Exception:
        logger.exception('Failed to record activation event %s (org=%s user=%s)',
                         event, organization_id, user_id)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def record_daily_session(user, *, surface=None):
    """Record at most one authenticated session event per user per UTC day."""
    if user is None or not is_customer_user(user):
        return None
    today = datetime.utcnow().date()
    existing = ActivationEvent.query.filter(
        ActivationEvent.event == ActivationEvent.SESSION_STARTED,
        ActivationEvent.user_id == user.id,
        ActivationEvent.created_at >= datetime.combine(today, datetime.min.time()),
    ).first()
    if existing is not None:
        return existing

    has_due_today = False
    try:
        start = datetime.combine(today, datetime.min.time())
        end = start + timedelta(days=1)
        has_due_today = Task.query.filter(
            Task.assigned_to_id == user.id,
            Task.status == 'pending',
            Task.due_date.isnot(None),
            Task.due_date < end,
        ).first() is not None
    except Exception:
        has_due_today = False

    return record_event(
        ActivationEvent.SESSION_STARTED,
        user=user,
        surface=surface or 'app',
        data={
            'day': today.isoformat(),
            'days_since_signup': _account_age_days(user),
            'has_overdue_or_due_today': has_due_today,
        },
    )


def record_meaningful_action(user, *, action, surface=None, data=None):
    """Record a CRM-value action used for meaningful retention."""
    if user is None or not is_customer_user(user):
        return None
    payload = dict(data or {})
    payload['action'] = action
    return record_event(
        ActivationEvent.MEANINGFUL_ACTION,
        user=user,
        surface=surface,
        data=payload,
        sync_person=True,
    )


def record_surface_viewed(user, surface):
    """At most one surface_viewed event per user/surface/UTC day."""
    if user is None or not surface or not is_customer_user(user):
        return None
    today = datetime.utcnow().date()
    existing = ActivationEvent.query.filter(
        ActivationEvent.event == ActivationEvent.SURFACE_VIEWED,
        ActivationEvent.user_id == user.id,
        ActivationEvent.created_at >= datetime.combine(today, datetime.min.time()),
    ).all()
    for row in existing:
        if isinstance(row.event_data, dict) and row.event_data.get('surface') == surface:
            return row
    return record_event(
        ActivationEvent.SURFACE_VIEWED,
        user=user,
        surface=surface,
        data={'surface': surface},
        sync_person=False,
    )


def classify_retention_stage(user, now=None):
    """Return the mutually exclusive current retention stage for a user."""
    now = now or datetime.utcnow()
    if user is None or not user.created_at:
        return 'activation_observing'

    age = now - user.created_at
    activated = is_user_activated(user)
    has_path = ActivationEvent.query.filter_by(
        user_id=user.id,
        event=ActivationEvent.ACTIVATION_PATH_SELECTED,
    ).first() is not None

    sessions = ActivationEvent.query.filter_by(
        user_id=user.id,
        event=ActivationEvent.SESSION_STARTED,
    ).all()
    returned_after_day0 = any(
        row.created_at.date() > user.created_at.date() for row in sessions
    )

    meaningful_rows = ActivationEvent.query.filter_by(
        user_id=user.id,
        event=ActivationEvent.MEANINGFUL_ACTION,
    ).order_by(ActivationEvent.created_at.desc()).all()
    last_meaningful = meaningful_rows[0].created_at if meaningful_rows else None
    meaningful_d2_d7 = any(
        timedelta(days=1) <= row.created_at - user.created_at < timedelta(days=8)
        for row in meaningful_rows
    )
    # Fallback: any authenticated session on D2-D7 counts toward early return
    # observing until meaningful-action instrumentation fully rolls out.
    session_d2_d7 = any(
        timedelta(days=1) <= row.created_at - user.created_at < timedelta(days=8)
        for row in sessions
    )

    if age < timedelta(hours=24) and not activated:
        return 'activation_observing'
    if not activated:
        if returned_after_day0:
            return 'unactivated_returning'
        if has_path:
            return 'unactivated_path_stalled'
        return 'unactivated_no_path'

    if age < timedelta(days=8):
        if meaningful_d2_d7 or session_d2_d7:
            return 'activated_retained'
        return 'activated_observing'

    reference = last_meaningful or (
        max((row.created_at for row in sessions), default=None)
    )
    if reference is None:
        return 'established_dormant'
    idle = now - reference
    if idle <= timedelta(days=7):
        return 'established_active'
    if idle <= timedelta(days=29):
        return 'established_at_risk'
    return 'established_dormant'


def maybe_transition_retention_stage(user, now=None, reason='scheduled'):
    """Emit retention_stage_changed when the computed stage differs."""
    if user is None or not is_customer_user(user):
        return None
    now = now or datetime.utcnow()
    current = classify_retention_stage(user, now=now)
    previous_row = ActivationEvent.query.filter_by(
        user_id=user.id,
        event=ActivationEvent.RETENTION_STAGE_CHANGED,
    ).order_by(ActivationEvent.created_at.desc()).first()
    previous = None
    if previous_row and isinstance(previous_row.event_data, dict):
        previous = previous_row.event_data.get('current')
    if previous == current:
        return previous_row
    return record_event(
        ActivationEvent.RETENTION_STAGE_CHANGED,
        user=user,
        data={
            'previous': previous,
            'current': current,
            'reason': reason,
        },
    )


def funnel_summary(now=None):
    """Return a user-level activation/retention funnel summary."""
    now = now or datetime.utcnow()

    customer_org_ids = {
        row.id for row in Organization.query.filter(
            Organization.is_platform_admin.is_(False),
            Organization.status == 'active',
        ).all()
    }

    signups = (
        ActivationEvent.query.filter(
            ActivationEvent.event == ActivationEvent.ACCOUNT_CREATED,
            ActivationEvent.user_id.isnot(None),
            ActivationEvent.organization_id.in_(customer_org_ids or {-1}),
        ).all()
    )
    signup_at = {}
    for row in signups:
        existing = signup_at.get(row.user_id)
        if existing is None or row.created_at < existing:
            signup_at[row.user_id] = row.created_at

    activations = (
        ActivationEvent.query.filter(
            ActivationEvent.event == ActivationEvent.ACTIVATION_COMPLETED,
            ActivationEvent.user_id.in_(signup_at.keys() or {-1}),
        ).all()
    )
    activation_at = {}
    for row in activations:
        existing = activation_at.get(row.user_id)
        if existing is None or row.created_at < existing:
            activation_at[row.user_id] = row.created_at

    contacts = (
        ActivationEvent.query.filter(
            ActivationEvent.event == ActivationEvent.CONTACT_CREATED,
            ActivationEvent.user_id.in_(signup_at.keys() or {-1}),
        ).all()
    )
    first_contact_at = {}
    for row in contacts:
        existing = first_contact_at.get(row.user_id)
        if existing is None or row.created_at < existing:
            first_contact_at[row.user_id] = row.created_at

    eligible_activation = {
        user_id: ts for user_id, ts in signup_at.items()
        if now - ts >= timedelta(hours=24)
    }
    observing_activation = len(signup_at) - len(eligible_activation)

    activated = [
        user_id for user_id, ts in activation_at.items()
        if user_id in signup_at
        and timedelta(0) <= ts - signup_at[user_id] <= timedelta(hours=24)
    ]
    activated_eligible = [
        user_id for user_id in activated if user_id in eligible_activation
    ]

    times_to_first = []
    for user_id in activated_eligible:
        if user_id not in first_contact_at:
            continue
        delta = first_contact_at[user_id] - signup_at[user_id]
        if delta.total_seconds() >= 0:
            times_to_first.append(delta.total_seconds())
    times_to_first.sort()
    times_to_activation = sorted(
        (activation_at[user_id] - signup_at[user_id]).total_seconds()
        for user_id in activated_eligible
    )

    eligible_d7 = {
        user_id: ts for user_id, ts in signup_at.items()
        if now - ts >= timedelta(days=7)
    }
    sessions = ActivationEvent.query.filter(
        ActivationEvent.event == ActivationEvent.SESSION_STARTED,
        ActivationEvent.user_id.in_(eligible_d7.keys() or {-1}),
    ).all()
    returned_d2_d7 = set()
    for event in sessions:
        signup = signup_at.get(event.user_id)
        if signup and timedelta(days=1) <= event.created_at - signup < timedelta(days=8):
            returned_d2_d7.add(event.user_id)

    meaningful = ActivationEvent.query.filter(
        ActivationEvent.event == ActivationEvent.MEANINGFUL_ACTION,
        ActivationEvent.user_id.in_(eligible_d7.keys() or {-1}),
    ).all()
    meaningful_d2_d7 = set()
    for event in meaningful:
        signup = signup_at.get(event.user_id)
        if signup and timedelta(days=1) <= event.created_at - signup < timedelta(days=8):
            meaningful_d2_d7.add(event.user_id)

    stage_counts = {}
    for user_id in signup_at:
        user = User.query.get(user_id)
        if user is None:
            continue
        stage = classify_retention_stage(user, now=now)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    friction_counts = {}
    for row in ActivationEvent.query.filter(
        ActivationEvent.event.in_([
            ActivationEvent.FRICTION_RESPONSE,
            ActivationEvent.CHURN_REASON,
        ]),
        ActivationEvent.user_id.in_(signup_at.keys() or {-1}),
    ).all():
        reason = (row.event_data or {}).get('reason') if isinstance(row.event_data, dict) else None
        if reason:
            friction_counts[reason] = friction_counts.get(reason, 0) + 1

    welcome_sent = ActivationEvent.query.filter(
        ActivationEvent.event == ActivationEvent.WELCOME_EMAIL_SENT,
        ActivationEvent.user_id.in_(signup_at.keys() or {-1}),
    ).count()
    welcome_clicked = ActivationEvent.query.filter(
        ActivationEvent.event == ActivationEvent.WELCOME_EMAIL_CLICKED,
        ActivationEvent.user_id.in_(signup_at.keys() or {-1}),
    ).count()
    login_failed = ActivationEvent.query.filter(
        ActivationEvent.event == ActivationEvent.LOGIN_FAILED,
        ActivationEvent.user_id.in_(signup_at.keys() or {-1}),
    ).count()

    stalled_stage, stalled_count = (
        max(stage_counts.items(), key=lambda item: item[1])
        if stage_counts else ('none', 0)
    )

    def _median(values):
        if not values:
            return None
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2

    eligible_activation_n = len(eligible_activation)
    eligible_d7_n = len(eligible_d7)
    return {
        'total_signups': len(signup_at),
        'eligible_activation_signups': eligible_activation_n,
        'activation_observing': observing_activation,
        'activated': len(activated_eligible),
        'activation_rate': (
            (len(activated_eligible) / eligible_activation_n)
            if eligible_activation_n else 0.0
        ),
        'quick_add_users': _quick_add_users_portable(signup_at.keys()),
        'median_seconds_to_first_contact': _median(times_to_first),
        'avg_seconds_to_first_contact': (
            sum(times_to_first) / len(times_to_first) if times_to_first else None
        ),
        'median_seconds_to_activation': _median(times_to_activation),
        'eligible_d7_signups': eligible_d7_n,
        'd7_returned': len(returned_d2_d7),
        'd7_return_rate': (
            len(returned_d2_d7) / eligible_d7_n if eligible_d7_n else 0.0
        ),
        'd7_meaningful': len(meaningful_d2_d7),
        'd7_meaningful_rate': (
            len(meaningful_d2_d7) / eligible_d7_n if eligible_d7_n else 0.0
        ),
        'stalled_stage': stalled_stage,
        'stalled_count': stalled_count,
        'stage_counts': stage_counts,
        'friction_counts': friction_counts,
        'welcome_sent': welcome_sent,
        'welcome_clicked': welcome_clicked,
        'login_failed': login_failed,
        # Backward-compatible aliases used by older templates/scripts.
        'quick_add_orgs': _quick_add_users_portable(signup_at.keys()),
    }


def _quick_add_users_portable(user_ids):
    users = set()
    if not user_ids:
        return 0
    rows = (
        db.session.query(ActivationEvent.user_id, ActivationEvent.event_data)
        .filter(
            ActivationEvent.event == ActivationEvent.CONTACT_CREATED,
            ActivationEvent.user_id.in_(list(user_ids)),
        )
        .all()
    )
    for user_id, payload in rows:
        if user_id is None:
            continue
        if isinstance(payload, dict) and payload.get('source') == 'quick_add':
            users.add(user_id)
    return len(users)
