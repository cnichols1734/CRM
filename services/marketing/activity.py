"""Read-only campaign progress, planned follow-ups, and recipient activity."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import and_, func
from sqlalchemy.orm import joinedload

from models import Contact, MarketingCampaignStep, MarketingEnrollment, MarketingSend, db
from services.marketing import merge_fields
from services.marketing.launch import _tz, send_at

PENDING = ('queued', 'sending', 'deferred')
SENT = ('sent', 'delivered')


def local_time(value, zone):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(_tz(zone)).strftime('%b %d, %Y at %-I:%M %p %Z')


def planned_time(pointer, next_send_at, target, steps, zone):
    """Project later steps from the enrollment's actual next-send pointer."""
    if next_send_at is None or pointer > target.step_index:
        return None
    when = next_send_at
    previous = next((s for s in steps if s.step_index == pointer), None)
    if previous is None:
        return None
    for step in steps:
        if pointer < step.step_index <= target.step_index:
            when = send_at(now=when, timezone_name=zone,
                           delay_days=max(0, step.delay_days - previous.delay_days),
                           send_hour_local=step.send_hour_local)
            previous = step
    return when


def snapshot(campaign, *, steps=None, send_groups=None, enrollment_groups=None):
    steps = steps if steps is not None else MarketingCampaignStep.query.options(joinedload(MarketingCampaignStep.template)).filter_by(
        campaign_id=campaign.id, organization_id=campaign.organization_id,
    ).order_by(MarketingCampaignStep.step_index).all()
    send_groups = send_groups if send_groups is not None else db.session.query(
        MarketingSend.step_id, MarketingSend.status, func.count(MarketingSend.id),
        func.min(MarketingSend.scheduled_for), func.max(MarketingSend.sent_at),
    ).filter_by(campaign_id=campaign.id, organization_id=campaign.organization_id).group_by(
        MarketingSend.step_id, MarketingSend.status,
    ).all()
    enrollment_groups = enrollment_groups if enrollment_groups is not None else db.session.query(
        MarketingEnrollment.status, MarketingEnrollment.current_step_index,
        func.count(MarketingEnrollment.id), func.min(MarketingEnrollment.next_send_at),
    ).filter_by(campaign_id=campaign.id, organization_id=campaign.organization_id).group_by(
        MarketingEnrollment.status, MarketingEnrollment.current_step_index,
    ).all()
    cards = []
    running = campaign.status in ('sending', 'active', 'scheduled')
    for step in steps:
        counts = {status: count for sid, status, count, _, _ in send_groups if sid == step.id}
        queued = sum(counts.get(status, 0) for status in PENDING)
        sent = sum(counts.get(status, 0) for status in SENT)
        dates = [when for sid, status, _, when, _ in send_groups
                 if sid == step.id and status in PENDING and when]
        upcoming = 0
        for status, pointer, count, next_at in enrollment_groups:
            if status != 'active' or pointer > step.step_index or next_at is None:
                continue
            # One-time enrollments retain their pointer after queueing.
            if campaign.kind != 'drip':
                continue
            upcoming += count
            projected = planned_time(pointer, next_at, step, steps, campaign.timezone)
            if projected:
                dates.append(projected)
        pending = queued + upcoming
        if campaign.status == 'cancelled' and pending:
            label = 'Cancelled'
        elif campaign.status == 'paused' and pending:
            label = 'Paused'
        elif counts.get('sending'):
            label = 'Sending now'
        elif pending:
            label = 'Scheduled'
        elif counts.get('failed') or counts.get('bounced') or counts.get('dropped'):
            label = 'Finished with issues'
        elif sent:
            label = 'Sent'
        elif counts.get('skipped'):
            label = 'Skipped'
        else:
            label = 'Not scheduled'
        next_at = min(dates) if dates else None
        last_sent = max((when for sid, _, _, _, when in send_groups if sid == step.id and when), default=None)
        cards.append(dict(step=step, sent=sent, pending=pending,
                          failed=sum(counts.get(s, 0) for s in ('failed', 'bounced', 'dropped')),
                          skipped=counts.get('skipped', 0), status=label,
                          next_at=next_at, next_local=local_time(next_at, campaign.timezone),
                          last_sent_local=local_time(last_sent, campaign.timezone)))
    next_card = min((c for c in cards if c['pending'] and c['next_at']),
                    key=lambda c: c['next_at'], default=None)
    status_label = campaign.status_label
    if running and next_card:
        if any(status == 'sending' for _, status, _, _, _ in send_groups):
            status_label = 'Sending now'
        elif any(status in PENDING and when and when <= datetime.utcnow()
                 for _, status, _, when, _ in send_groups):
            status_label = 'Queued to send'
        elif campaign.sent_count and campaign.kind == 'drip' and next_card['step'].step_index > 0:
            status_label = 'Waiting for follow-up'
        else:
            status_label = 'Scheduled'
    revision = hashlib.sha256(json.dumps([
        campaign.status, campaign.auto_paused_reason,
        sorted((tuple(r) for r in send_groups), key=str), sorted((tuple(r) for r in enrollment_groups), key=str),
    ], default=str, sort_keys=True).encode()).hexdigest()[:16]
    return dict(steps=steps, cards=cards, next_card=next_card if running else None,
                status_label=status_label, revision=revision,
                pending=sum(c['pending'] for c in cards) if running else 0)


def recipient_page(campaign, step, steps, org, page=1, per_page=50):
    query = db.session.query(MarketingEnrollment, MarketingSend).outerjoin(
        MarketingSend, and_(MarketingSend.enrollment_id == MarketingEnrollment.id,
                           MarketingSend.step_id == step.id,
                           MarketingSend.organization_id == campaign.organization_id),
    ).options(joinedload(MarketingEnrollment.contact)).filter(
        MarketingEnrollment.campaign_id == campaign.id,
        MarketingEnrollment.organization_id == campaign.organization_id,
        MarketingEnrollment.contact.has(Contact.user_id == campaign.user_id),
    )
    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    rows = []
    for enrollment, send in query.order_by(MarketingEnrollment.id).offset((page - 1) * per_page).limit(per_page):
        contact = enrollment.contact
        when = None
        reason = ''
        if send:
            label = {'queued': 'Queued', 'sending': 'Sending now', 'deferred': 'Retry scheduled',
                     'sent': 'Sent', 'delivered': 'Sent', 'skipped': 'Skipped',
                     'failed': 'Failed', 'bounced': 'Bounced', 'dropped': 'Failed'}.get(send.status, send.status)
            when = send.sent_at or send.scheduled_for
            reason = send.skip_reason or send.error or ''
            if send.status in PENDING:
                label = 'Scheduled' if when and when > datetime.utcnow() else label
                if campaign.status in ('paused', 'cancelled'):
                    label = campaign.status.capitalize()
        elif campaign.status == 'cancelled':
            label, reason = 'Cancelled', 'Campaign cancelled'
        elif enrollment.status == 'active' and enrollment.current_step_index <= step.step_index:
            label = 'Paused' if campaign.status == 'paused' else 'Scheduled'
            when = planned_time(enrollment.current_step_index, enrollment.next_send_at, step, steps, campaign.timezone)
        else:
            label = 'Not scheduled'
            reason = (enrollment.stop_reason or 'No further emails scheduled').replace('_', ' ')
        subject = send.subject_rendered if send and send.subject_rendered else merge_fields.substitute(
            step.template.subject if step.template else '',
            merge_fields.resolve_values(contact, campaign.owner, org),
        )[0]
        rows.append(dict(contact=contact, email=send.to_email if send else getattr(contact, 'email', None),
                         status=label, when=local_time(when, campaign.timezone),
                         reason=reason.replace('_', ' '), subject=subject,
                         is_sent=bool(send and send.sent_at)))
    return dict(rows=rows, total=total, page=page, pages=pages)


def snapshots(campaigns):
    """Fetch list-page summaries in three queries regardless of campaign count."""
    if not campaigns:
        return {}
    ids = [c.id for c in campaigns]
    org_id = campaigns[0].organization_id
    steps = MarketingCampaignStep.query.options(joinedload(MarketingCampaignStep.template)).filter(
        MarketingCampaignStep.campaign_id.in_(ids), MarketingCampaignStep.organization_id == org_id,
    ).order_by(MarketingCampaignStep.step_index).all()
    sends = db.session.query(
        MarketingSend.campaign_id, MarketingSend.step_id, MarketingSend.status,
        func.count(MarketingSend.id), func.min(MarketingSend.scheduled_for), func.max(MarketingSend.sent_at),
    ).filter(MarketingSend.campaign_id.in_(ids), MarketingSend.organization_id == org_id).group_by(
        MarketingSend.campaign_id, MarketingSend.step_id, MarketingSend.status,
    ).all()
    enrollments = db.session.query(
        MarketingEnrollment.campaign_id, MarketingEnrollment.status,
        MarketingEnrollment.current_step_index, func.count(MarketingEnrollment.id),
        func.min(MarketingEnrollment.next_send_at),
    ).filter(MarketingEnrollment.campaign_id.in_(ids), MarketingEnrollment.organization_id == org_id).group_by(
        MarketingEnrollment.campaign_id, MarketingEnrollment.status, MarketingEnrollment.current_step_index,
    ).all()
    return {c.id: snapshot(c, steps=[s for s in steps if s.campaign_id == c.id],
                           send_groups=[r[1:] for r in sends if r[0] == c.id],
                           enrollment_groups=[r[1:] for r in enrollments if r[0] == c.id])
            for c in campaigns}
