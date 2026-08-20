"""Send an APNs alert for a PortalMessage.

No-ops when APNS_* env is missing. The RQ worker consumes the apns queue.
"""
from __future__ import annotations

import logging
import os

from jobs.base import set_job_org_context

logger = logging.getLogger(__name__)

APNS_ENV_KEYS = ('APNS_KEY_ID', 'APNS_TEAM_ID', 'APNS_KEY')


def apns_configured() -> bool:
    return all((os.environ.get(key) or '').strip() for key in APNS_ENV_KEYS)


def send_portal_push(*, message_id: int, org_id: int):
    """Deliver APNs to the other side of a PortalMessage. Missing env is a no-op."""
    if not apns_configured():
        logger.info(
            'APNs skipped: APNS_* env missing (message_id=%s org_id=%s)',
            message_id, org_id,
        )
        return {'ok': False, 'reason': 'apns_unconfigured'}

    from app import app
    from models import DeviceToken, PortalMessage

    with app.app_context():
        set_job_org_context(org_id)
        msg = PortalMessage.query.filter_by(
            id=message_id, organization_id=org_id,
        ).first()
        if not msg:
            logger.info('APNs skipped: message %s not found', message_id)
            return {'ok': False, 'reason': 'message_not_found'}

        if msg.sender == 'client':
            tokens = DeviceToken.query.filter_by(
                organization_id=org_id,
                audience=DeviceToken.AUDIENCE_AGENT,
            ).all()
        else:
            tokens = DeviceToken.query.filter_by(
                organization_id=org_id,
                audience=DeviceToken.AUDIENCE_CLIENT,
                participant_id=msg.participant_id,
            ).all()

        if not tokens:
            logger.info('APNs skipped: no device tokens for message %s', message_id)
            return {'ok': False, 'reason': 'no_tokens'}

        sent = 0
        for row in tokens:
            if _send_one(row.token, msg, row.audience):
                sent += 1
        return {'ok': True, 'sent': sent}


def _send_one(device_token: str, msg, audience) -> bool:
    """HTTP/2 APNs post. Isolated so missing env never reaches here."""
    try:
        from services.apns_client import send_alert
        return bool(send_alert(device_token, msg, audience=audience))
    except Exception:
        logger.exception('APNs send failed for token suffix %s', device_token[-8:])
        return False
