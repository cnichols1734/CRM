"""Register APNs device tokens and enqueue a push on new PortalMessage."""
from __future__ import annotations

import logging
from datetime import datetime

from models import DeviceToken, db

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
                    user_id=None, participant_id=None):
    token = (token or '').strip()
    if not token:
        raise ValueError('A device token is required.')
    if audience not in DeviceToken.AUDIENCES:
        raise ValueError('Unknown device audience.')
    if audience == DeviceToken.AUDIENCE_AGENT and not user_id:
        raise ValueError('Agent devices need a user.')
    if audience == DeviceToken.AUDIENCE_CLIENT and not participant_id:
        raise ValueError('Client devices need a participant.')

    platform = normalize_platform(platform)
    row = DeviceToken.query.filter_by(audience=audience, token=token).first()
    if row:
        row.organization_id = organization_id
        row.platform = platform
        row.user_id = user_id if audience == DeviceToken.AUDIENCE_AGENT else None
        row.participant_id = (
            participant_id if audience == DeviceToken.AUDIENCE_CLIENT else None
        )
        row.last_seen_at = datetime.utcnow()
        db.session.commit()
        return row

    row = DeviceToken(
        organization_id=organization_id,
        audience=audience,
        token=token,
        platform=platform,
        user_id=user_id if audience == DeviceToken.AUDIENCE_AGENT else None,
        participant_id=(
            participant_id if audience == DeviceToken.AUDIENCE_CLIENT else None
        ),
    )
    db.session.add(row)
    db.session.commit()
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
        from rq import Queue
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
            job_timeout=60,
        )
        return {'ok': True, 'queued': True}
    except Exception:
        logger.exception(
            'APNs enqueue failed for message %s; skipping push',
            message.id,
        )
        return {'ok': False, 'reason': 'enqueue_failed'}
