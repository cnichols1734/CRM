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
    from models import DeviceToken, PortalMessage, Organization, db

    with app.app_context():
        set_job_org_context(org_id)
        msg = PortalMessage.query.filter_by(
            id=message_id, organization_id=org_id,
        ).first()
        if not msg:
            logger.info('APNs skipped: message %s not found', message_id)
            return {'ok': False, 'reason': 'message_not_found'}

        org = db.session.get(Organization, org_id)
        if not org or org.status != 'active':
            return {'ok': False, 'reason': 'organization_inactive'}

        if msg.sender == 'client':
            tokens = DeviceToken.query.filter_by(
                organization_id=org_id,
                audience=DeviceToken.AUDIENCE_AGENT, platform='ios',
            ).all()
            from services.transaction_auth import can_view_transaction
            tokens = [row for row in tokens if row.user and can_view_transaction(msg.transaction, row.user).allowed]
        else:
            tokens = DeviceToken.query.filter_by(
                organization_id=org_id,
                audience=DeviceToken.AUDIENCE_CLIENT,
                participant_id=msg.participant_id, platform='ios',
            ).all()

        if not tokens:
            logger.info('APNs skipped: no device tokens for message %s', message_id)
            return {'ok': False, 'reason': 'no_tokens'}

        from rq import get_current_job
        job = get_current_job()
        delivered = set(job.meta.get('delivered_devices', [])) if job else set()
        sent, failed = 0, 0
        for row in tokens:
            if row.id in delivered:
                continue
            if _send_one(row.token, msg, row.audience, row.environment):
                sent += 1
                if job:
                    delivered.add(row.id)
                    job.meta['delivered_devices'] = sorted(delivered)
                    job.save_meta()
            else:
                failed += 1
        if failed:
            raise RuntimeError(f'Message notification failed for {failed} devices')
        return {'ok': True, 'sent': sent}


def _send_one(device_token: str, msg, audience, environment=None) -> bool:
    """HTTP/2 APNs post. Isolated so missing env never reaches here."""
    try:
        from services.apns_client import send_alert
        return bool(send_alert(device_token, msg, audience=audience, environment=environment))
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
            if send_payload(row.token, payload, audience='client', environment=row.environment):
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


def send_inquiry_push(*, inquiry_id, org_id, message_id=None, assignment=False):
    if not apns_configured():
        return {'ok': False, 'reason': 'apns_unconfigured'}
    from app import app
    from models import db, DeviceToken, ClientBrowseInquiry, ClientBrowseAccount, ClientInquiryMessage, Organization
    from services.apns_client import send_payload
    from rq import get_current_job
    with app.app_context():
        set_job_org_context(org_id)
        org = db.session.get(Organization, org_id)
        row = ClientBrowseInquiry.query.filter_by(id=inquiry_id, organization_id=org_id).first()
        if not org or org.status != 'active' or row is None:
            return {'ok': False, 'reason': 'conversation_unavailable'}
        account = ClientBrowseAccount.query.filter_by(id=row.account_id, organization_id=org_id).first()
        message = ClientInquiryMessage.query.filter_by(id=message_id, inquiry_id=row.id,
            organization_id=org_id).first() if message_id else None
        if account is None or (message_id and message is None):
            return {'ok': False, 'reason': 'message_unavailable'}
        if assignment and (message is None or message.author_user_id != account.agent_id):
            return {'ok': False, 'reason': 'assignment_changed'}
        sender = message.sender if message else 'client'
        body = (message.body if message else row.body) or 'Sent an attachment.'
        if sender == 'client':
            query = DeviceToken.query.filter_by(organization_id=org_id, audience=DeviceToken.AUDIENCE_AGENT, platform='ios')
            if row.agent_id:
                query = query.filter_by(user_id=row.agent_id)
            tokens = [device for device in query.all() if device.user and device.user.organization_id == org_id]
            title = 'New client message' if row.agent_id else 'New unassigned client'
        else:
            tokens = DeviceToken.query.filter_by(organization_id=org_id, audience=DeviceToken.AUDIENCE_BROWSE,
                browse_account_id=account.id, platform='ios').all()
            from datetime import datetime
            tokens = [device for device in tokens if device.expires_at and device.expires_at > datetime.utcnow()]
            title = 'Your agent' if assignment else 'Message from your agent'
        job = get_current_job()
        delivered = set(job.meta.get('delivered_devices', [])) if job else set()
        sent, failed = 0, 0
        for device in tokens:
            if device.id in delivered:
                continue
            payload = {'aps': {'alert': {'title': title, 'body': body if assignment else body[:120]}, 'sound': 'default'},
                'kind': 'inquiry_assignment' if assignment else 'inquiry_message',
                'inquiry_id': row.id, 'account_id': account.id}
            if send_payload(device.token, payload, audience=device.audience, environment=device.environment):
                sent += 1
                if job:
                    delivered.add(device.id)
                    job.meta['delivered_devices'] = sorted(delivered)
                    job.save_meta()
            else:
                failed += 1
        if failed:
            raise RuntimeError(f'Inquiry notification failed for {failed} devices')
        return {'ok': True, 'sent': sent}
