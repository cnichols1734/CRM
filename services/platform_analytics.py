"""Platform reports from aggregate snapshots and bounded milestone queries."""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func
from models import ActivationEvent as Event, db
from services.acquisition import _campaign_labels

ACTION_LABELS = {
    'csv_import_completed': 'CSV imports',
    'inbox_contact_created': 'Inbox contact saves',
    'contact_created': 'Contacts added',
    'interaction_logged': 'Interactions logged',
    'follow_up_created': 'Follow-ups created',
    'task_completed': 'Tasks completed',
}
EVENT_LABELS = {
    Event.ACCOUNT_CREATED: 'Account created',
    Event.CONTACT_CREATED: 'Contact added',
    Event.FOLLOW_UP_CREATED: 'Follow-up created',
    Event.TASK_CREATED: 'Task created',
    Event.TASK_COMPLETED: 'Task completed',
    Event.DASHBOARD_VIEWED: 'Dashboard viewed',
    Event.ACTIVATION_PATH_SELECTED: 'Getting-started path selected',
    Event.CSV_IMPORT_STARTED: 'CSV import started',
    Event.CSV_IMPORT_COMPLETED: 'CSV import completed',
    Event.CSV_IMPORT_PARTIAL: 'CSV import partially completed',
    Event.CSV_IMPORT_FAILED: 'CSV import failed',
    Event.INBOX_ADDRESS_COPIED: 'Inbox address copied',
    Event.FEATURE_GATE_HIT: 'Plan limit reached',
    Event.ACTIVATION_COMPLETED: 'Activation completed',
    Event.SESSION_STARTED: 'Daily app visit',
    Event.LOGIN_SUCCEEDED: 'Signed in',
    Event.MEANINGFUL_ACTION: 'Meaningful action',
    Event.SURFACE_VIEWED: 'App page viewed',
}
MILESTONE_LABELS = {
    **EVENT_LABELS,
    Event.CONTACT_CREATED: 'First contact added',
    Event.FOLLOW_UP_CREATED: 'First follow-up created',
}
# Automated mail, inbox processing and scheduled retention changes are excluded.
ACTIVITY_EVENTS = tuple(EVENT_LABELS)


def acquisition_source(data):
    """Distinguish absent history from a captured visit without a referrer."""
    data = data if isinstance(data, dict) else {}
    labels = _campaign_labels({
        key: data.get(f'first_touch_{key}') or data.get(key)
        for key in ('utm_source', 'utm_medium', 'utm_campaign', 'ref')
    })
    source = labels.get('first_touch_utm_source')
    ref = labels.get('first_touch_ref')
    domain = data.get('first_touch_referring_domain')
    if source:
        label = source
    elif ref:
        label = f'Referral: {ref}'
    elif data.get('first_touch_referrer_kind') == 'external' and isinstance(domain, str):
        label = domain[:80]
    elif data.get('first_touch_at'):
        label = 'Direct / unknown' if data.get('first_touch_referrer_kind') != 'internal' else 'Internal / unknown'
    else:
        label = 'Not captured'
    return {
        'label': label,
        'captured': bool(data.get('first_touch_at')),
        'campaign': labels.get('first_touch_utm_campaign'),
        'medium': labels.get('first_touch_utm_medium'),
        'landing': data.get('first_touch_landing_path'),
        'first_visit': data.get('first_touch_at'),
    }


def platform_overview(org_ids, now=None):
    now = now or datetime.utcnow()
    start = datetime.combine(now.date() - timedelta(days=29), datetime.min.time())
    reports = {oid: {'source': acquisition_source({}), 'last_activity': None, 'actions': 0} for oid in org_ids}
    if not reports:
        return {'orgs': reports, 'sources': [], 'active_orgs': 0, 'actions': 0, 'captured': 0}
    signups = db.session.query(Event.organization_id, Event.event_data).filter(
        Event.organization_id.in_(org_ids), Event.event == Event.ACCOUNT_CREATED,
    ).order_by(Event.created_at, Event.id).all()
    seen = set()
    for oid, data in signups:
        if oid not in seen:
            reports[oid]['source'] = acquisition_source(data)
            seen.add(oid)
    for oid, latest in db.session.query(Event.organization_id, func.max(Event.created_at)).filter(
        Event.organization_id.in_(org_ids), Event.event.in_(ACTIVITY_EVENTS),
    ).group_by(Event.organization_id):
        reports[oid]['last_activity'] = latest
    for oid, count in db.session.query(Event.organization_id, func.count(Event.id)).filter(
        Event.organization_id.in_(org_ids), Event.event == Event.MEANINGFUL_ACTION,
        Event.created_at >= start, Event.created_at <= now,
    ).group_by(Event.organization_id):
        reports[oid]['actions'] = count
    sources = Counter(row['source']['label'] for row in reports.values())
    return {
        'orgs': reports, 'sources': sources.most_common(),
        'active_orgs': sum(bool(row['last_activity'] and row['last_activity'] >= start) for row in reports.values()),
        'actions': sum(row['actions'] for row in reports.values()),
        'captured': sum(row['source']['captured'] for row in reports.values()),
    }


def organization_activity(org_id, now=None):
    now = now or datetime.utcnow()
    start = datetime.combine(now.date() - timedelta(days=29), datetime.min.time())
    query = Event.query.filter(Event.organization_id == org_id)
    first_signup = query.filter(Event.event == Event.ACCOUNT_CREATED).order_by(Event.created_at, Event.id).first()
    source = acquisition_source(first_signup.event_data if first_signup else {})
    counts = dict(db.session.query(Event.event, func.count(Event.id)).filter(
        Event.organization_id == org_id,
        Event.event.in_((Event.MEANINGFUL_ACTION, Event.SESSION_STARTED)),
    ).group_by(Event.event).all())
    action_rows = db.session.query(func.date(Event.created_at), func.count(Event.id)).filter(
        Event.organization_id == org_id, Event.event == Event.MEANINGFUL_ACTION,
        Event.created_at >= start, Event.created_at <= now,
    ).group_by(func.date(Event.created_at)).all()
    by_day = {str(day): count for day, count in action_rows}
    days = [{'date': (start + timedelta(days=i)).strftime('%b %d'),
             'count': by_day.get((start + timedelta(days=i)).date().isoformat(), 0)} for i in range(30)]
    action_key = Event.event_data['action'].as_string()
    breakdown = db.session.query(action_key, func.count(Event.id)).filter(
        Event.organization_id == org_id, Event.event == Event.MEANINGFUL_ACTION,
        Event.created_at >= start, Event.created_at <= now,
    ).group_by(action_key).all()
    actions = sorted([(ACTION_LABELS.get(key, 'Other actions'), count) for key, count in breakdown], key=lambda row: -row[1])
    timeline = query.filter(Event.event.in_(EVENT_LABELS)).order_by(Event.created_at.desc(), Event.id.desc()).limit(20).all()
    milestones = dict(db.session.query(Event.event, func.min(Event.created_at)).filter(
        Event.organization_id == org_id,
        Event.event.in_((Event.ACCOUNT_CREATED, Event.CONTACT_CREATED, Event.FOLLOW_UP_CREATED, Event.ACTIVATION_COMPLETED)),
    ).group_by(Event.event).all())
    return {
        'source': source, 'days': days, 'max_daily': max([d['count'] for d in days] + [1]),
        'actions': actions, 'actions_30d': sum(d['count'] for d in days),
        'actions_total': counts.get(Event.MEANINGFUL_ACTION, 0),
        'daily_visits': counts.get(Event.SESSION_STARTED, 0),
        'last_activity': timeline[0].created_at if timeline else None,
        'milestones': [(MILESTONE_LABELS[event], at) for event, at in sorted(milestones.items(), key=lambda row: row[1])],
        'timeline': [{'at': row.created_at, 'label': ACTION_LABELS.get((row.event_data or {}).get('action'), EVENT_LABELS[row.event]) if row.event == Event.MEANINGFUL_ACTION else EVENT_LABELS[row.event]} for row in timeline],
    }
