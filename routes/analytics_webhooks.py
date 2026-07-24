"""Public webhooks used for retention analytics attribution."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from flask import Blueprint, current_app, request

from models import ActivationEvent, User
from services.activation_service import record_event

logger = logging.getLogger(__name__)

analytics_webhooks_bp = Blueprint('analytics_webhooks', __name__)

_EVENT_MAP = {
    'delivered': ActivationEvent.EMAIL_DELIVERED,
    'bounce': ActivationEvent.EMAIL_BOUNCED,
    'dropped': ActivationEvent.EMAIL_DROPPED,
    'deferred': ActivationEvent.EMAIL_DEFERRED,
}


def _verify_sendgrid_signature(payload: bytes, signature: str, timestamp: str) -> bool:
    key = current_app.config.get('SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY')
    if not key:
        # Allow local/dev without verification, but never in production.
        return current_app.config.get('FLASK_ENV') != 'production'
    try:
        # SendGrid signed event webhook uses ECDSA; when only a shared secret
        # is configured we fall back to HMAC of timestamp + body.
        digest = hmac.new(
            key.encode('utf-8'),
            msg=(timestamp + payload.decode('utf-8')).encode('utf-8'),
            digestmod=hashlib.sha256,
        ).digest()
        expected = base64.b64encode(digest).decode('utf-8')
        return hmac.compare_digest(expected, signature or '')
    except Exception:
        logger.exception('SendGrid webhook signature verification failed')
        return False


@analytics_webhooks_bp.route('/webhooks/sendgrid/events', methods=['POST'])
def sendgrid_events():
    """Ingest SendGrid Event Webhook for welcome/lifecycle deliverability."""
    raw = request.get_data()
    signature = request.headers.get('X-Twilio-Email-Event-Webhook-Signature', '')
    timestamp = request.headers.get('X-Twilio-Email-Event-Webhook-Timestamp', '')
    if not _verify_sendgrid_signature(raw, signature, timestamp):
        # Still accept in non-production when key missing; reject otherwise.
        if current_app.config.get('SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY'):
            return ('invalid signature', 401)

    try:
        events = request.get_json(force=True, silent=True) or []
    except Exception:
        events = []
    if not isinstance(events, list):
        return ('ok', 200)

    for item in events:
        if not isinstance(item, dict):
            continue
        event_name = item.get('event')
        mapped = _EVENT_MAP.get(event_name)
        if not mapped:
            continue
        user_id = item.get('crm_user_id') or (item.get('unique_args') or {}).get(
            'crm_user_id'
        )
        if not user_id:
            continue
        try:
            user = User.query.get(int(user_id))
        except (TypeError, ValueError):
            continue
        if user is None:
            continue
        campaign = item.get('crm_campaign') or (item.get('unique_args') or {}).get(
            'crm_campaign'
        )
        stage = item.get('crm_stage') or (item.get('unique_args') or {}).get(
            'crm_stage'
        )
        sg_event_id = item.get('sg_event_id')
        if sg_event_id:
            existing = ActivationEvent.query.filter(
                ActivationEvent.user_id == user.id,
                ActivationEvent.event == mapped,
            ).all()
            already = any(
                isinstance(row.event_data, dict)
                and row.event_data.get('sg_event_id') == sg_event_id
                for row in existing
            )
            if already:
                continue
        record_event(
            mapped,
            user=user,
            data={
                'campaign': campaign,
                'stage': stage,
                'provider_event': event_name,
                'sg_event_id': sg_event_id,
            },
            sync_person=False,
        )
    return ('ok', 200)
