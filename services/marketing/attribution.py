"""Apply SendGrid event webhooks to marketing send rows.

Lifecycle mail already lands on the same endpoint. Events carrying a marketing
``send_id`` custom arg are ours: update the row, write suppressions for bounces
and spam, and trip the bounce-rate circuit breaker when a campaign goes bad.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from config import Config
from models import MarketingCampaign, MarketingSend, db
from services.marketing import launch as launchmod
from services.marketing import suppression as supp

logger = logging.getLogger(__name__)

# SendGrid event name -> send status. Opens and clicks are recorded as
# timestamps only; v1 does not expose them in the UI.
_STATUS_EVENTS = {
    'delivered': 'delivered',
    'bounce': 'bounced',
    'dropped': 'dropped',
    'deferred': 'deferred',
    'spamreport': 'bounced',
}


def _custom(item: dict, key: str):
    if item.get(key) is not None:
        return item.get(key)
    unique = item.get('unique_args') or {}
    if isinstance(unique, dict) and unique.get(key) is not None:
        return unique.get(key)
    return None


def apply_event(item: dict, *, now: Optional[datetime] = None) -> bool:
    """Handle one webhook event. Returns True if a marketing send was updated."""
    if not isinstance(item, dict):
        return False
    send_id = _custom(item, 'send_id')
    kind = _custom(item, 'kind')
    if send_id is None:
        return False
    if kind not in (None, 'marketing'):
        return False

    try:
        send_id = int(send_id)
    except (TypeError, ValueError):
        return False

    send = db.session.get(MarketingSend, send_id)
    if send is None:
        return False

    now = now or datetime.utcnow()
    event = (item.get('event') or '').lower()
    campaign = send.campaign or db.session.get(MarketingCampaign, send.campaign_id)

    if event == 'delivered':
        if send.status not in ('bounced', 'dropped', 'failed', 'skipped', 'delivered'):
            send.status = 'delivered'
            send.delivered_at = now
            if campaign:
                campaign.delivered_count = (campaign.delivered_count or 0) + 1
        return True

    if event in ('open', 'opened'):
        if send.opened_at is None:
            send.opened_at = now
        return True

    if event in ('click', 'clicked'):
        if send.clicked_at is None:
            send.clicked_at = now
        return True

    if event in ('bounce', 'blocked'):
        _mark_bounce(send, campaign, item, now, spam=False)
        return True

    if event == 'dropped':
        if send.status not in ('bounced', 'delivered'):
            send.status = 'dropped'
            send.error = (item.get('reason') or 'dropped')[:500]
            if campaign:
                campaign.failed_count = (campaign.failed_count or 0) + 1
        return True

    if event == 'spamreport':
        _mark_bounce(send, campaign, item, now, spam=True)
        return True

    if event in ('unsubscribe', 'group_unsubscribe'):
        supp.record_unsubscribe(send)
        if campaign:
            campaign.unsubscribed_count = (campaign.unsubscribed_count or 0) + 1
        return True

    return False


def _mark_bounce(send, campaign, item, now, *, spam: bool) -> None:
    already = send.status == 'bounced'
    send.status = 'bounced'
    send.error = (item.get('reason') or ('spam report' if spam else 'bounce'))[:500]
    if not already and campaign:
        campaign.bounced_count = (campaign.bounced_count or 0) + 1

    reason = supp.REASON_SPAM if spam else supp.REASON_BOUNCE
    if send.to_email:
        supp.suppress(
            send.to_email, reason,
            organization_id=send.organization_id,
            source_send_id=send.id,
        )
        if spam:
            from models import Contact
            contact = send.contact or db.session.get(Contact, send.contact_id)
            if contact is not None:
                contact.marketing_consent = 'opted_out'
                contact.marketing_consent_source = 'spam_report'
                contact.marketing_consent_at = now

    if campaign:
        maybe_trip_breaker(campaign)


def maybe_trip_breaker(campaign: MarketingCampaign) -> None:
    """Pause a campaign whose bounce rate is high enough to hurt the domain."""
    min_attempts = Config.MARKETING_BOUNCE_PAUSE_MIN
    threshold = Config.MARKETING_BOUNCE_PAUSE_RATE
    attempted = (campaign.delivered_count or 0) + (campaign.bounced_count or 0)
    if attempted < min_attempts:
        return
    if campaign.bounce_rate < threshold:
        return
    if campaign.status not in ('sending', 'active'):
        return
    rate_pct = int(campaign.bounce_rate * 100)
    logger.warning(
        'Marketing bounce breaker tripped campaign=%s org=%s rate=%s',
        campaign.id, campaign.organization_id, campaign.bounce_rate,
    )
    launchmod.pause(
        campaign,
        reason=f'Bounce rate reached {rate_pct}% after {attempted} attempts.',
        commit=False,
    )
