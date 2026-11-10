"""Public webhooks used for retention analytics attribution."""
from __future__ import annotations

import base64
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
        return False
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        public_key = serialization.load_der_public_key(base64.b64decode(key, validate=True))
        if not isinstance(public_key, ec.EllipticCurvePublicKey) or not timestamp.isdecimal():
            return False
        public_key.verify(base64.b64decode(signature, validate=True), timestamp.encode('ascii') + payload, ec.ECDSA(hashes.SHA256()))
        return True
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
        return ('invalid signature', 401)

    try:
        events = request.get_json(force=True, silent=True) or []
    except Exception:
        events = []
    if not isinstance(events, list):
        return ('ok', 200)

    from services.mobile_org_context import set_org_context
    from models import db
    from services.marketing.attribution import apply_event as apply_marketing_event

    marketing_touched = False
    for item in events:
        if not isinstance(item, dict):
            continue
        if item.get('home_facts_receipt') or (item.get('unique_args') or {}).get('home_facts_receipt'):
            from services.home_facts.tracking import apply_event as apply_home_facts_event
            try:
                db.session.commit()
                apply_home_facts_event(item)
                db.session.commit()
            except Exception:
                logger.exception('Home Facts delivery event failed')
                db.session.rollback()
                return ('retry', 503)
            continue
        org_hint = item.get('organization_id') or (item.get('unique_args') or {}).get(
            'organization_id'
        )
        if org_hint:
            try:
                set_org_context(int(org_hint))
            except Exception:
                db.session.rollback()

        try:
            if apply_marketing_event(item):
                marketing_touched = True
                db.session.commit()
                continue
        except Exception:
            logger.exception('Marketing webhook event failed')
            db.session.rollback()
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

    if marketing_touched:
        try:
            db.session.commit()
        except Exception:
            logger.exception('Marketing webhook commit failed')
            db.session.rollback()
    return ('ok', 200)
