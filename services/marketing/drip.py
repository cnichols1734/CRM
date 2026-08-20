"""Advance drip enrollments whose next send is due."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from models import (
    MarketingCampaign, MarketingCampaignStep, MarketingEnrollment,
    MarketingSend, db,
)
from services.marketing import launch as launchmod
from services.marketing import suppression as supp

logger = logging.getLogger(__name__)


def due_enrollments(organization_id: int, *, now: datetime, limit: int = 100):
    return (
        MarketingEnrollment.query
        .filter(
            MarketingEnrollment.organization_id == organization_id,
            MarketingEnrollment.status == 'active',
            MarketingEnrollment.next_send_at.isnot(None),
            MarketingEnrollment.next_send_at <= now,
        )
        .order_by(MarketingEnrollment.next_send_at.asc())
        .limit(limit)
        .all()
    )


def advance_one(enrollment: MarketingEnrollment, *, now: Optional[datetime] = None) -> bool:
    """Queue the current step's send and point at the next one.

    Returns True if a send was queued.
    """
    now = now or datetime.utcnow()
    campaign = enrollment.campaign or db.session.get(
        MarketingCampaign, enrollment.campaign_id,
    )
    if campaign is None or campaign.status not in ('active', 'sending'):
        return False

    step = MarketingCampaignStep.query.filter_by(
        campaign_id=campaign.id,
        step_index=enrollment.current_step_index,
    ).first()
    if step is None:
        enrollment.status = 'completed'
        enrollment.completed_at = now
        enrollment.next_send_at = None
        launchmod.maybe_complete(campaign)
        return False

    already = MarketingSend.query.filter_by(
        enrollment_id=enrollment.id, step_id=step.id,
    ).first()
    if already is None:
        contact = enrollment.contact
        email = supp.normalize(getattr(contact, 'email', None)) if contact else ''
        if not email:
            enrollment.status = 'stopped'
            enrollment.stop_reason = 'no_email'
            enrollment.completed_at = now
            enrollment.next_send_at = None
            return False
        if supp.is_suppressed(email, campaign.organization_id):
            enrollment.status = 'stopped'
            enrollment.stop_reason = 'suppressed'
            enrollment.completed_at = now
            enrollment.next_send_at = None
            return False
        db.session.add(MarketingSend(
            organization_id=campaign.organization_id,
            campaign_id=campaign.id,
            step_id=step.id,
            enrollment_id=enrollment.id,
            contact_id=enrollment.contact_id,
            template_id=step.template_id,
            user_id=campaign.user_id,
            to_email=email,
            status='queued',
            scheduled_for=now,
            unsubscribe_token=supp.issue_token(campaign.organization_id),
        ))
        campaign.queued_count = (campaign.queued_count or 0) + 1
        if campaign.status == 'active':
            campaign.status = 'sending'

    steps = (
        MarketingCampaignStep.query
        .filter_by(campaign_id=campaign.id)
        .order_by(MarketingCampaignStep.step_index.asc())
        .all()
    )
    launchmod._advance_enrollment_pointer(
        enrollment, steps, now, campaign.timezone,
    )
    launchmod.maybe_complete(campaign)
    return already is None
