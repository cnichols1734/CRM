"""Turn a draft campaign into enrollments and send rows.

Launch is the moment the audience becomes a snapshot. Contacts added to a group
afterwards do not join a running campaign. Skipped recipients still get a row,
with a reason, so the monitor can account for everyone the agent thought they
were sending to.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from models import (
    MarketingAudience, MarketingCampaign, MarketingCampaignStep,
    MarketingEnrollment, MarketingSend, MarketingTemplate, db,
)
from services.marketing import audience as aud
from services.marketing import sending_config
from services.marketing import suppression as supp
from services.marketing.blocks import find_placeholders
from services.marketing.templates import TemplateError, get_visible, mark_used


class LaunchError(ValueError):
    """The campaign cannot go out. Message is shown to the agent."""


def _tz(name: Optional[str]):
    try:
        return ZoneInfo(name or 'America/Chicago')
    except Exception:
        return ZoneInfo('America/Chicago')


def send_at(
    *,
    now: datetime,
    timezone_name: str,
    delay_days: int,
    send_hour_local: int,
) -> datetime:
    """UTC instant for a step. delay_days=0 means as soon as we can."""
    if delay_days <= 0:
        return now
    tz = _tz(timezone_name)
    if now.tzinfo is None:
        local_now = now.replace(tzinfo=timezone.utc).astimezone(tz)
    else:
        local_now = now.astimezone(tz)
    hour = max(0, min(int(send_hour_local), 23))
    target = (local_now + timedelta(days=delay_days)).replace(
        hour=hour, minute=0, second=0, microsecond=0,
    )
    return target.astimezone(timezone.utc).replace(tzinfo=None)


def _steps(campaign: MarketingCampaign) -> list[MarketingCampaignStep]:
    return (
        MarketingCampaignStep.query
        .filter_by(campaign_id=campaign.id)
        .order_by(MarketingCampaignStep.step_index.asc())
        .all()
    )


def _template_ready(template: MarketingTemplate) -> None:
    if getattr(template, 'source', None) == 'system':
        raise LaunchError(
            'Starters are not used in campaigns. Save a copy and set it active first.'
        )
    if not template.is_sendable:
        if template.compliance_state == 'blocked':
            raise LaunchError(
                f'"{template.name}" is blocked by the Fair Housing check. '
                'Fix the flagged copy before sending.'
            )
        raise LaunchError(f'"{template.name}" is not ready to send.')
    placeholders = find_placeholders(
        template.blocks or [], template.subject, template.preheader or '',
    )
    if placeholders:
        listed = ', '.join(placeholders[:6])
        raise LaunchError(
            f'"{template.name}" still has unfilled details: {listed}. '
            'Replace the brackets before sending.'
        )


def validate_for_launch(campaign: MarketingCampaign, org, user) -> list[MarketingCampaignStep]:
    if campaign.status not in MarketingCampaign.EDITABLE_STATUSES:
        raise LaunchError('This campaign has already been launched.')
    if not campaign.audience_id:
        raise LaunchError('Pick who this goes to.')

    steps = _steps(campaign)
    if not steps:
        raise LaunchError('A campaign needs at least one email.')

    readiness = sending_config.readiness_for(org)
    if not readiness.ok:
        raise LaunchError(readiness.message)

    for step in steps:
        template = step.template or db.session.get(MarketingTemplate, step.template_id)
        if template is None:
            raise LaunchError('A campaign step is missing its template.')
        try:
            get_visible(campaign.organization_id, user.id, template.id)
        except TemplateError as exc:
            raise LaunchError(str(exc)) from exc
        _template_ready(template)

    return steps


def _ensure_audience(campaign: MarketingCampaign, user) -> MarketingAudience:
    audience = campaign.audience
    if audience is None:
        raise LaunchError('Pick who this goes to.')
    if audience.organization_id != campaign.organization_id:
        raise LaunchError('That audience is not available.')
    return audience


@dataclass
class LaunchResult:
    campaign: MarketingCampaign
    sendable: int
    skipped: int
    breakdown: dict[str, int]


def launch(
    campaign: MarketingCampaign,
    org,
    user,
    *,
    now: Optional[datetime] = None,
    commit: bool = True,
) -> LaunchResult:
    now = now or datetime.utcnow()
    steps = validate_for_launch(campaign, org, user)
    audience = _ensure_audience(campaign, user)
    estimate = aud.estimate(campaign.organization_id, audience.filter, user)

    quota = sending_config.quota_for(org, now)
    refusal = quota.refusal(estimate.sendable_count)
    if refusal:
        raise LaunchError(refusal)
    if estimate.sendable_count == 0:
        raise LaunchError(
            'Nobody in this audience can receive the email. '
            'Check emails, unsubscribes, and consent.'
        )

    first = steps[0]
    scheduled = campaign.scheduled_at if campaign.scheduled_at and campaign.scheduled_at > now else now
    is_future = campaign.scheduled_at is not None and campaign.scheduled_at > now
    is_drip = campaign.kind == 'drip' or len(steps) > 1

    campaign.kind = 'drip' if is_drip else 'one_time'
    campaign.launched_at = now
    campaign.total_recipients = estimate.matched
    campaign.queued_count = 0
    campaign.skipped_count = 0
    campaign.sent_count = 0
    campaign.delivered_count = 0
    campaign.bounced_count = 0
    campaign.failed_count = 0
    campaign.unsubscribed_count = 0
    campaign.auto_paused_reason = None
    campaign.from_name = campaign.from_name or sending_config.sender_for(user, org).from_name
    campaign.reply_to = campaign.reply_to or getattr(user, 'email', None)

    if is_future:
        campaign.status = 'scheduled'
    elif is_drip:
        campaign.status = 'active'
    else:
        campaign.status = 'sending'

    seen_templates = set()
    for step in steps:
        if step.template_id not in seen_templates:
            mark_used(step.template or db.session.get(MarketingTemplate, step.template_id))
            seen_templates.add(step.template_id)

    for recipient in estimate.sendable:
        enrollment = MarketingEnrollment(
            organization_id=campaign.organization_id,
            campaign_id=campaign.id,
            contact_id=recipient.contact.id,
            status='active',
            current_step_index=0,
            next_send_at=scheduled if (is_drip or not is_future) else scheduled,
            enrolled_at=now,
        )
        if is_drip:
            enrollment.next_send_at = send_at(
                now=scheduled,
                timezone_name=campaign.timezone,
                delay_days=first.delay_days,
                send_hour_local=first.send_hour_local,
            )
        db.session.add(enrollment)
        db.session.flush()

        if not is_drip or first.delay_days <= 0:
            when = scheduled
            db.session.add(_queued_send(
                campaign, first, enrollment, recipient.contact, recipient.email,
                user_id=user.id, scheduled_for=when,
            ))
            campaign.queued_count += 1
            if is_drip:
                _advance_enrollment_pointer(enrollment, steps, scheduled, campaign.timezone)
        else:
            # First drip step is in the future; the drip worker creates the send.
            pass

    for exclusion in estimate.excluded:
        enrollment = MarketingEnrollment(
            organization_id=campaign.organization_id,
            campaign_id=campaign.id,
            contact_id=exclusion.contact.id,
            status='stopped',
            current_step_index=0,
            enrolled_at=now,
            completed_at=now,
            stop_reason=exclusion.reason,
        )
        db.session.add(enrollment)
        db.session.flush()
        db.session.add(_skipped_send(
            campaign, first, enrollment, exclusion.contact,
            email=exclusion.email or '',
            reason=exclusion.reason,
            user_id=user.id,
        ))
        campaign.skipped_count += 1

    audience.cached_count = estimate.sendable_count
    audience.cached_at = now

    if commit:
        db.session.commit()
    return LaunchResult(
        campaign=campaign,
        sendable=estimate.sendable_count,
        skipped=estimate.excluded_count,
        breakdown=estimate.breakdown(),
    )


def _queued_send(campaign, step, enrollment, contact, email, *, user_id, scheduled_for):
    return MarketingSend(
        organization_id=campaign.organization_id,
        campaign_id=campaign.id,
        step_id=step.id,
        enrollment_id=enrollment.id,
        contact_id=contact.id,
        template_id=step.template_id,
        user_id=user_id,
        to_email=email,
        status='queued',
        scheduled_for=scheduled_for,
        unsubscribe_token=supp.issue_token(campaign.organization_id),
    )


def _skipped_send(campaign, step, enrollment, contact, *, email, reason, user_id):
    return MarketingSend(
        organization_id=campaign.organization_id,
        campaign_id=campaign.id,
        step_id=step.id,
        enrollment_id=enrollment.id,
        contact_id=contact.id,
        template_id=step.template_id,
        user_id=user_id,
        to_email=email or 'none',
        status='skipped',
        skip_reason=reason,
        unsubscribe_token=supp.issue_token(campaign.organization_id),
    )


def _advance_enrollment_pointer(enrollment, steps, now, timezone_name):
    """After queueing step 0 of a drip, point at the next step or complete."""
    nxt = [s for s in steps if s.step_index > enrollment.current_step_index]
    if not nxt:
        enrollment.status = 'completed'
        enrollment.completed_at = now
        enrollment.next_send_at = None
        return
    following = nxt[0]
    enrollment.current_step_index = following.step_index
    enrollment.next_send_at = send_at(
        now=now,
        timezone_name=timezone_name,
        delay_days=following.delay_days,
        send_hour_local=following.send_hour_local,
    )


def pause(campaign: MarketingCampaign, *, reason: Optional[str] = None, commit: bool = True):
    if campaign.status not in ('sending', 'active', 'scheduled'):
        raise LaunchError('This campaign is not running.')
    campaign.status = 'paused'
    campaign.paused_at = datetime.utcnow()
    if reason:
        campaign.auto_paused_reason = reason
    if commit:
        db.session.commit()


def resume(campaign: MarketingCampaign, *, commit: bool = True):
    if campaign.status != 'paused':
        raise LaunchError('This campaign is not paused.')
    campaign.auto_paused_reason = None
    campaign.paused_at = None
    if campaign.kind == 'drip':
        campaign.status = 'active'
    elif campaign.queued_count > 0:
        campaign.status = 'sending'
    else:
        campaign.status = 'completed'
        campaign.completed_at = datetime.utcnow()
    if commit:
        db.session.commit()


def cancel(campaign: MarketingCampaign, *, commit: bool = True):
    if campaign.status in ('completed', 'cancelled'):
        raise LaunchError('This campaign is already finished.')
    campaign.status = 'cancelled'
    campaign.completed_at = datetime.utcnow()
    queued = MarketingSend.query.filter_by(
        campaign_id=campaign.id, status='queued',
    ).all()
    for send in queued:
        send.status = 'skipped'
        send.skip_reason = 'campaign_cancelled'
        campaign.queued_count = max(campaign.queued_count - 1, 0)
        campaign.skipped_count += 1
    MarketingEnrollment.query.filter_by(
        campaign_id=campaign.id, status='active',
    ).update({
        'status': 'stopped',
        'stop_reason': 'campaign_cancelled',
        'completed_at': campaign.completed_at,
        'next_send_at': None,
    }, synchronize_session=False)
    if commit:
        db.session.commit()


def maybe_complete(campaign: MarketingCampaign) -> None:
    """Mark finished when nothing is left to send."""
    if campaign.status not in ('sending', 'active'):
        return
    remaining = MarketingSend.query.filter(
        MarketingSend.campaign_id == campaign.id,
        MarketingSend.status.in_(('queued', 'sending', 'deferred')),
    ).count()
    active_enrollments = MarketingEnrollment.query.filter_by(
        campaign_id=campaign.id, status='active',
    ).count()
    if remaining == 0 and (campaign.kind != 'drip' or active_enrollments == 0):
        campaign.status = 'completed'
        campaign.completed_at = datetime.utcnow()
