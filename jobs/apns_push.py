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


def send_date_push(*, org_id, transaction_id, added, changed):
    if not apns_configured():
        return {'ok': False, 'reason': 'apns_unconfigured'}
    from app import app
    from models import ClientPortalAccess, DeviceToken, Organization, TransactionParticipant, db
    from services.apns_client import send_payload
    from services.portal_service import CLIENT_PORTAL_ROLES
    with app.app_context():
        set_job_org_context(org_id)
        organization = db.session.get(Organization, org_id)
        if not organization or organization.status != 'active':
            return {'ok': False, 'reason': 'organization_inactive'}
        participants = ClientPortalAccess.query.join(
            TransactionParticipant, ClientPortalAccess.participant_id == TransactionParticipant.id
        ).filter(ClientPortalAccess.organization_id == org_id,
                 ClientPortalAccess.transaction_id == transaction_id,
                 ClientPortalAccess.is_active.is_(True),
                 TransactionParticipant.organization_id == org_id,
                 TransactionParticipant.transaction_id == transaction_id,
                 TransactionParticipant.role.in_(CLIENT_PORTAL_ROLES)).all()
        ids = [p.participant_id for p in participants]
        tokens = DeviceToken.query.filter(DeviceToken.organization_id == org_id,
            DeviceToken.audience == DeviceToken.AUDIENCE_CLIENT,
            DeviceToken.platform == 'ios', DeviceToken.participant_id.in_(ids)).all() if ids else []
        title = 'New transaction dates' if added and not changed else 'Transaction dates updated'
        from rq import get_current_job
        job = get_current_job()
        delivered = set(job.meta.get('delivered_devices', [])) if job else set()
        sent, failed = 0, 0
        for row in tokens:
            if row.id in delivered:
                continue
            payload = {'aps': {'alert': {'title': title, 'body': 'Open Next steps to review your dates.'},
                               'sound': 'default'}, 'kind': 'milestones',
                       'transaction_id': transaction_id, 'participant_id': row.participant_id}
            if send_payload(row.token, payload, audience='client'):
                sent += 1
                if job:
                    delivered.add(row.id)
                    job.meta['delivered_devices'] = sorted(delivered)
                    job.save_meta()
            else:
                failed += 1
        if failed:
            raise RuntimeError(f'Date alert delivery failed for {failed} devices')
        return {'ok': True, 'sent': sent}
