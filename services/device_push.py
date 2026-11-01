"""Register APNs device tokens and enqueue a push on new PortalMessage."""
from __future__ import annotations

import logging
from datetime import datetime

from models import DeviceToken, db
from sqlalchemy import text

logger = logging.getLogger(__name__)

QUEUE_NAME = 'apns'


def normalize_platform(value):
    raw = (value or '').strip().lower()
    if raw in ('ios', 'iphone', 'ipad'):
        return 'ios'
    if raw in ('android',):
        return 'android'
    return 'ios'


def register_device(*, organization_id, audience, token, platform,
                    user_id=None, participant_id=None, browse_account_id=None, environment=None, expires_at=None):
    if not isinstance(token, str) or len(token) > 255:
        raise ValueError('A valid device token is required.')
    if environment not in (None, 'sandbox', 'production'):
        raise ValueError('Choose sandbox or production for notifications.')
    token = (token or '').strip()
    if not token:
        raise ValueError('A device token is required.')
    if audience not in DeviceToken.AUDIENCES:
        raise ValueError('Unknown device audience.')
    if audience == DeviceToken.AUDIENCE_AGENT and not user_id:
        raise ValueError('Agent devices need a user.')
    if audience == DeviceToken.AUDIENCE_CLIENT and not participant_id:
        raise ValueError('Client devices need a participant.')

    if audience == DeviceToken.AUDIENCE_BROWSE and not browse_account_id:
        raise ValueError('Client account devices need an account.')

    platform = normalize_platform(platform)
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
            {'key': audience + ':' + token})
        db.session.execute(text('SELECT public.release_other_device_bindings(:token, :audience, :org)'),
            {'token': token, 'audience': audience, 'org': organization_id})
    else:
        DeviceToken.query.filter(DeviceToken.audience == audience, DeviceToken.token == token,
            DeviceToken.organization_id != organization_id).delete(synchronize_session=False)
    row = DeviceToken.query.filter_by(organization_id=organization_id, audience=audience, token=token).first()
    if row:
        row.organization_id = organization_id
        row.platform = platform
        row.user_id = user_id if audience == DeviceToken.AUDIENCE_AGENT else None
        row.participant_id = (
            participant_id if audience == DeviceToken.AUDIENCE_CLIENT else None
        )
        row.browse_account_id = browse_account_id if audience == DeviceToken.AUDIENCE_BROWSE else None
        row.environment = environment
        row.expires_at = expires_at
        row.last_seen_at = datetime.utcnow()
        db.session.flush()
        return row

    row = DeviceToken(
        organization_id=organization_id,
        audience=audience,
        token=token,
        platform=platform,
        environment=environment,
        expires_at=expires_at,
        browse_account_id=browse_account_id if audience == DeviceToken.AUDIENCE_BROWSE else None,
        user_id=user_id if audience == DeviceToken.AUDIENCE_AGENT else None,
        participant_id=(
            participant_id if audience == DeviceToken.AUDIENCE_CLIENT else None
        ),
    )
    db.session.add(row)
    db.session.flush()
    return row


def enqueue_portal_push(message):
    """Ask the RQ worker to notify the other side. Missing APNS_* is a no-op."""
    from jobs.apns_push import apns_configured

    if message is None or not getattr(message, 'id', None):
        return {'ok': False, 'reason': 'no_message'}
    if not apns_configured():
        logger.info(
            'APNs skipped: APNS_* env missing (message_id=%s)',
            message.id,
        )
        return {'ok': False, 'reason': 'apns_unconfigured'}

    try:
        from redis import Redis
        from rq import Queue, Retry
        from config import Config

        conn = Redis.from_url(
            Config.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        conn.ping()
        Queue(QUEUE_NAME, connection=conn).enqueue(
            'jobs.apns_push.send_portal_push',
            message_id=message.id,
            org_id=message.organization_id,
            retry=Retry(max=3, interval=[30, 120, 300]),
            job_timeout=120,
        )
        return {'ok': True, 'queued': True}
    except Exception:
        logger.exception(
            'APNs enqueue failed for message %s; skipping push',
            message.id,
        )
        return {'ok': False, 'reason': 'enqueue_failed'}


def enqueue_date_push(*, org_id, transaction_id, added, changed):
    from jobs.apns_push import apns_configured
    if not apns_configured():
        return {'ok': False, 'reason': 'apns_unconfigured'}
    try:
        from redis import Redis
        from rq import Queue, Retry
        from config import Config
        conn = Redis.from_url(Config.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        Queue(QUEUE_NAME, connection=conn).enqueue(
            'jobs.apns_push.send_date_push', org_id=org_id, transaction_id=transaction_id,
            added=added, changed=changed, job_timeout=120, retry=Retry(max=3, interval=[30, 120, 300]))
        return {'ok': True, 'queued': True}
    except Exception:
        logger.exception('Could not queue date alert for transaction %s', transaction_id)
        return {'ok': False, 'reason': 'enqueue_failed'}


def enqueue_inquiry_push(*, inquiry_id, org_id, message_id=None, assignment=False):
    from jobs.apns_push import apns_configured
    if not apns_configured():
        return {'ok': False, 'reason': 'apns_unconfigured'}
    try:
        from redis import Redis
        from rq import Queue, Retry
        from config import Config
        connection = Redis.from_url(Config.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        Queue(QUEUE_NAME, connection=connection).enqueue(
            'jobs.apns_push.send_inquiry_push', inquiry_id=inquiry_id, org_id=org_id,
            message_id=message_id, assignment=assignment,
            job_timeout=120, retry=Retry(max=3, interval=[30, 120, 300]))
        return {'ok': True, 'queued': True}
    except Exception:
        logger.exception('Could not queue client inquiry notification %s', inquiry_id)
        return {'ok': False, 'reason': 'enqueue_failed'}
