"""Read-only, grant-scoped calendar subscriptions and committed date changes."""
from datetime import datetime, timedelta, timezone
import hashlib

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from models import (ClientPortalAccess, Organization, SellerAcceptedContract, SellerContractMilestone,
                    SellerListingProfile, Transaction, TransactionDocument, db)


def _signer():
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='client-calendar-v1')


def calendar_token(access):
    return _signer().dumps({'aid': access.id, 'oid': access.organization_id,
                          'pid': access.participant_id, 'tid': access.transaction_id,
                          'sv': access.session_version or 1,
                          'cv': access.calendar_version or 1})


def calendar_claims(token):
    try:
        claims = _signer().loads(token)
        if not isinstance(claims, dict) or not all(type(claims.get(k)) is int for k in ('aid', 'oid', 'pid', 'tid', 'sv')):
            return None
        claims.setdefault('cv', 1)
        if type(claims['cv']) is not int or claims['cv'] < 1:
            return None
        return claims
    except (BadSignature, TypeError, ValueError):
        return None


def calendar_access(claims):
    access = ClientPortalAccess.query.filter_by(id=claims['aid'], organization_id=claims['oid'],
        transaction_id=claims['tid'], participant_id=claims['pid'], is_active=True).first()
    if not access or (access.session_version or 1) != claims['sv']:
        return None
    if access.calendar_disabled_at or (access.calendar_version or 1) != claims['cv']:
        return None
    return access if calendar_grant_is_active(access) else None


def calendar_grant_is_active(access):
    if not access or not access.is_active:
        return False
    organization = db.session.get(Organization, access.organization_id)
    if not organization or organization.status != 'active':
        return False
    from services.portal_service import CLIENT_PORTAL_ROLES
    participant, tx = access.participant, access.transaction
    if not participant or not tx or participant.transaction_id != tx.id or participant.organization_id != access.organization_id or tx.organization_id != access.organization_id:
        return False
    return participant.role in CLIENT_PORTAL_ROLES


def calendar_subscription_status(access):
    if access.calendar_disabled_at:
        return 'disabled'
    return 'link_enabled' if access.calendar_issued_at else 'not_started'


def calendar_rows(session, org_id, transaction_id):
    from services.transaction_dates import transaction_date_rows
    return [r for r in transaction_date_rows(session, org_id, transaction_id) if r['due_at']]


def event_key(row):
    key = str(row['id']) if row['milestone_key'] == 'manual' else row['milestone_key']
    return f"{row['accepted_contract_id']}:{key}"


def _escape(value):
    return str(value or '').replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def _fold(line):
    chunks, chunk = [], ''
    for char in line:
        if len((chunk + char).encode('utf-8')) > 75:
            chunks.append(chunk)
            chunk = ' '
        chunk += char
    return '\r\n'.join(chunks + [chunk])


def render_calendar(access):
    rows = calendar_rows(db.session, access.organization_id, access.transaction_id)
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//AgentFlow//Client dates//EN',
             'CALSCALE:GREGORIAN', 'X-WR-CALNAME:AgentFlow transaction dates',
             'REFRESH-INTERVAL;VALUE=DURATION:PT1H', 'X-PUBLISHED-TTL:PT1H']
    for row in sorted(rows, key=lambda r: (r['due_at'], event_key(r))):
        day = row['due_at'].date()
        updated = row['updated_at'] or row['created_at'] or datetime.now(timezone.utc)
        uid = hashlib.sha256(f"{access.organization_id}:{access.transaction_id}:{event_key(row)}".encode()).hexdigest()
        lines += ['BEGIN:VEVENT', f'UID:{uid}@calendar.agentflow',
                  'DTSTAMP:' + updated.strftime('%Y%m%dT%H%M%SZ'),
                  'LAST-MODIFIED:' + updated.strftime('%Y%m%dT%H%M%SZ'),
                  'DTSTART;VALUE=DATE:' + day.strftime('%Y%m%d'),
                  'DTEND;VALUE=DATE:' + (day + timedelta(days=1)).strftime('%Y%m%d'),
                  'SUMMARY:' + _escape(row['title']),
                  'DESCRIPTION:Open AgentFlow for the latest transaction details and confirmed appointment times.',
                  'TRANSP:TRANSPARENT', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(line) for line in lines) + '\r\n'


def _snapshot(session, key):
    return {event_key(r): (r['due_at'].date().isoformat(), r['title']) for r in calendar_rows(session, *key)}


def _before_flush(session, *_):
    pending = session.info.setdefault('client_date_changes', {})
    for row in list(session.new) + list(session.dirty) + list(session.deleted):
        if not isinstance(row, (SellerContractMilestone, SellerAcceptedContract, SellerListingProfile, Transaction, TransactionDocument)):
            continue
        tx_id = row.id if isinstance(row, Transaction) else row.transaction_id
        if row.organization_id and tx_id:
            key = (row.organization_id, tx_id)
            if key not in pending:
                pending[key] = [_snapshot(session, key), None]


def _after_flush(session, *_):
    for key, values in session.info.get('client_date_changes', {}).items():
        values[1] = _snapshot(session, key)


def _after_commit(session):
    if session.in_nested_transaction():
        return
    from services.device_push import enqueue_date_push
    for (org_id, transaction_id), (before, after) in session.info.pop('client_date_changes', {}).items():
        if after is not None and before != after:
            added = sum(key not in before for key in after)
            enqueue_date_push(org_id=org_id, transaction_id=transaction_id,
                              added=added, changed=sum(before.get(key) != after.get(key) for key in before))


def _after_rollback(session, previous_transaction):
    if previous_transaction.nested:
        _after_flush(session)
    else:
        session.info.pop('client_date_changes', None)


def register_date_change_listeners():
    for name, callback in [('before_flush', _before_flush), ('after_flush_postexec', _after_flush),
                           ('after_commit', _after_commit), ('after_soft_rollback', _after_rollback)]:
        if not event.contains(Session, name, callback):
            event.listen(Session, name, callback)
