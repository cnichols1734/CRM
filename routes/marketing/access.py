"""Shared access helpers for marketing routes."""
from __future__ import annotations

from flask import abort
from flask_login import current_user
from feature_flags import org_has_feature
from models import MarketingCampaign, MarketingTemplate
from services.marketing.templates import get_visible, TemplateError
from services.tenant_service import org_query


def require_campaigns():
    if not org_has_feature('EMAIL_CAMPAIGNS'):
        abort(404)


def campaign_or_404(campaign_id: int) -> MarketingCampaign:
    require_campaigns()
    campaign = org_query(MarketingCampaign).filter_by(id=campaign_id).first_or_404()
    if campaign.user_id != current_user.id:
        from services.tenant_service import can_view_all_org_data
        if not can_view_all_org_data():
            abort(403)
    return campaign


def template_or_404(template_id: int) -> MarketingTemplate:
    require_campaigns()
    try:
        return get_visible(current_user.organization_id, current_user.id, template_id)
    except TemplateError:
        abort(404)
