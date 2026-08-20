"""Marketing campaign tools for B.O.B. and MCP.

There is no launch tool. MCP builds and stages; a human clicks send in AgentFlow.
That is not an omission. MCP calls run precleared, and a send to hundreds of
people is not something a model should infer.
"""
from __future__ import annotations

from datetime import datetime

from models import (
    Contact, MarketingAudience, MarketingCampaign, MarketingCampaignStep,
    MarketingTemplate, Notification, db,
)
from services.bob_tools.common import ToolError
from services.bob_tools.context import BobContext, ToolResult
from services.marketing import audience as aud
from services.marketing import blocks as blockmod
from services.marketing import merge_fields as mf
from services.marketing import sending_config
from services.marketing import system_templates
from services.marketing import templates as tpl
from services.marketing.context import shell_for
from services.marketing.render import preview as preview_email
from services.marketing.studio import generate
from services.marketing.suppression import (
    REASON_MANUAL, REASON_UNSUBSCRIBE, release, suppress as write_suppression,
)


def _user_org(ctx: BobContext):
    user = ctx.load_user()
    org = user.organization if user else None
    if user is None or org is None:
        raise ToolError('No organization on this session.')
    return user, org


def _require_feature(org):
    from feature_flags import org_has_feature
    if not org_has_feature('EMAIL_CAMPAIGNS', org):
        raise ToolError('Email campaigns are not enabled for this organization.')


def guidelines(ctx: BobContext) -> ToolResult:
    _, org = _user_org(ctx)
    _require_feature(org)
    return ToolResult.success(
        'Block schema, merge fields, and content rules for marketing emails.',
        {
            'blocks': blockmod.describe_blocks_for_agent(),
            'merge_fields': [
                {
                    'key': f.key,
                    'label': f.label,
                    'description': f.description,
                    'example': f.example,
                    'default_fallback': f.default_fallback,
                }
                for f in mf.MERGE_FIELDS
            ],
            'schema': blockmod.ai_generation_schema(),
            'rules': [
                'Never write HTML. Produce blocks only.',
                'Hero must be first if used, and only one hero.',
                'Signature last.',
                'No Fair Housing preferences or proxies.',
                'There is no launch_campaign tool. Stage the campaign and give the agent the review URL.',
            ],
            'starter_templates': [
                {'key': t['key'], 'name': t['name'], 'category': t['category']}
                for t in system_templates.SYSTEM_TEMPLATES
            ],
        },
    )


def list_templates(ctx: BobContext, *, category: str | None = None) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    system_templates.seed_for_org(org.id, commit=True)
    query = tpl.visible_to(org.id, user.id)
    if category:
        query = query.filter(MarketingTemplate.category == category)
    rows = query.limit(50).all()
    return ToolResult.success(
        f'{len(rows)} templates.',
        {
            'templates': [
                {
                    'id': t.id,
                    'name': t.name,
                    'category': t.category,
                    'status': t.status,
                    'visibility': t.visibility,
                    'subject': t.subject,
                    'compliance_state': t.compliance_state,
                }
                for t in rows
            ],
        },
    )


def get_template(ctx: BobContext, *, template_id: int) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    try:
        template = tpl.get_visible(org.id, user.id, template_id)
    except tpl.TemplateError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult.success(
        template.name,
        {
            'id': template.id,
            'name': template.name,
            'subject': template.subject,
            'preheader': template.preheader,
            'blocks': template.blocks,
            'category': template.category,
            'status': template.status,
            'compliance_state': template.compliance_state,
            'compliance_findings': template.compliance_findings,
            'placeholders': blockmod.find_placeholders(
                template.blocks or [], template.subject, template.preheader or '',
            ),
        },
        record_url=f'/marketing/studio/{template.id}',
    )


def preview_template(ctx: BobContext, *, template_id: int) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    try:
        template = tpl.get_visible(org.id, user.id, template_id)
    except tpl.TemplateError as exc:
        raise ToolError(str(exc)) from exc
    ctx_shell = shell_for(org, user, preheader=template.preheader)
    subject, html = preview_email(template.blocks or [], ctx_shell, template.subject)
    return ToolResult.success(
        f'Preview of {template.name}',
        {'subject': subject, 'html': html},
        record_url=f'/marketing/studio/{template.id}',
    )


def create_template(ctx: BobContext, **kwargs) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    prompt = (kwargs.get('prompt') or '').strip()
    try:
        if prompt and not kwargs.get('blocks'):
            generated = generate(
                prompt,
                tone=kwargs.get('tone') or 'warm',
                category=kwargs.get('category'),
            )
            blocks = generated['blocks']
            subject = kwargs.get('subject') or generated['subject']
            preheader = kwargs.get('preheader') or generated['preheader']
            generated_by_ai = True
        else:
            blocks = kwargs.get('blocks') or []
            subject = kwargs.get('subject') or ''
            preheader = kwargs.get('preheader')
            generated_by_ai = False
        template = tpl.save(
            organization_id=org.id,
            user_id=user.id,
            org=org,
            agent=user,
            name=kwargs.get('name') or 'Untitled',
            subject=subject,
            preheader=preheader,
            blocks=blocks,
            description=kwargs.get('description'),
            category=kwargs.get('category') or 'other',
            visibility='org' if kwargs.get('share_with_org') else 'private',
            acknowledge_warnings=bool(kwargs.get('acknowledge_warnings')),
            generated_by_ai=generated_by_ai,
            prompt=prompt or None,
        )
    except tpl.TemplateError as exc:
        raise ToolError(str(exc)) from exc
    summary = f'Saved “{template.name}” as {template.status}.'
    if template.compliance_state == 'blocked':
        summary = (
            f'Draft saved but blocked by Fair Housing checks. '
            f'Fix the findings and update the template.'
        )
    return ToolResult.success(
        summary,
        {
            'id': template.id,
            'status': template.status,
            'compliance_state': template.compliance_state,
            'compliance_findings': template.compliance_findings,
            'placeholders': blockmod.find_placeholders(
                template.blocks or [], template.subject, template.preheader or '',
            ),
        },
        record_url=f'/marketing/studio/{template.id}',
    )


def update_template(ctx: BobContext, *, template_id: int, **kwargs) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    try:
        template = tpl.get_visible(org.id, user.id, template_id)
        saved = tpl.save(
            organization_id=org.id,
            user_id=user.id,
            org=org,
            agent=user,
            name=kwargs.get('name') or template.name,
            subject=kwargs.get('subject') or template.subject,
            preheader=kwargs.get('preheader', template.preheader),
            blocks=kwargs.get('blocks') or template.blocks,
            description=kwargs.get('description', template.description),
            category=kwargs.get('category') or template.category,
            visibility=kwargs.get('visibility') or template.visibility,
            template=template,
            acknowledge_warnings=bool(kwargs.get('acknowledge_warnings')),
            change_note=kwargs.get('change_note'),
        )
    except tpl.TemplateError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult.success(
        f'Updated “{saved.name}” to v{saved.version}.',
        {
            'id': saved.id,
            'version': saved.version,
            'status': saved.status,
            'compliance_state': saved.compliance_state,
            'compliance_findings': saved.compliance_findings,
        },
        record_url=f'/marketing/studio/{saved.id}',
    )


def estimate_audience(ctx: BobContext, **kwargs) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    try:
        estimate = aud.estimate(org.id, kwargs, user)
    except aud.AudienceError as exc:
        raise ToolError(str(exc)) from exc
    data = estimate.as_dict()
    return ToolResult.success(
        f'{estimate.sendable_count} can receive this ({estimate.matched} in the filter).',
        data,
    )


def list_audiences(ctx: BobContext) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    rows = (
        MarketingAudience.query
        .filter_by(organization_id=org.id, user_id=user.id, is_saved=True)
        .order_by(MarketingAudience.updated_at.desc())
        .limit(50)
        .all()
    )
    return ToolResult.success(
        f'{len(rows)} saved audiences.',
        {
            'audiences': [
                {'id': a.id, 'name': a.name, 'filter': a.filter, 'cached_count': a.cached_count}
                for a in rows
            ],
        },
    )


def list_campaigns(ctx: BobContext, *, status: str | None = None) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    query = MarketingCampaign.query.filter_by(organization_id=org.id, user_id=user.id)
    if status:
        query = query.filter_by(status=status)
    rows = query.order_by(MarketingCampaign.created_at.desc()).limit(30).all()
    return ToolResult.success(
        f'{len(rows)} campaigns.',
        {
            'campaigns': [
                {
                    'id': c.id,
                    'name': c.name,
                    'status': c.status,
                    'kind': c.kind,
                    'sent': c.sent_count,
                    'total': c.total_recipients,
                }
                for c in rows
            ],
        },
    )


def get_campaign(ctx: BobContext, *, campaign_id: int) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    campaign = MarketingCampaign.query.filter_by(
        id=campaign_id, organization_id=org.id,
    ).first()
    if campaign is None:
        raise ToolError('That campaign is not available.')
    if campaign.user_id != user.id and user.org_role not in ('owner', 'admin'):
        raise ToolError('That campaign is not available.')
    steps = (
        MarketingCampaignStep.query.filter_by(campaign_id=campaign.id)
        .order_by(MarketingCampaignStep.step_index.asc())
        .all()
    )
    return ToolResult.success(
        campaign.name,
        {
            'id': campaign.id,
            'name': campaign.name,
            'status': campaign.status,
            'kind': campaign.kind,
            'queued': campaign.queued_count,
            'sent': campaign.sent_count,
            'delivered': campaign.delivered_count,
            'bounced': campaign.bounced_count,
            'skipped': campaign.skipped_count,
            'steps': [
                {'index': s.step_index, 'template_id': s.template_id, 'delay_days': s.delay_days}
                for s in steps
            ],
        },
        record_url=f'/marketing/campaigns/{campaign.id}',
    )


def create_campaign(ctx: BobContext, **kwargs) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    readiness = sending_config.readiness_for(org)
    name = (kwargs.get('name') or '').strip()
    if not name:
        raise ToolError('Name the campaign.')
    template_id = kwargs.get('template_id')
    if not template_id:
        raise ToolError('Pick a template_id from list_email_templates.')
    try:
        template = tpl.get_visible(org.id, user.id, int(template_id))
    except tpl.TemplateError as exc:
        raise ToolError(str(exc)) from exc

    filt = aud.parse_filter({
        'groups': kwargs.get('groups') or [],
        'zips': kwargs.get('zips') or [],
        'cities': kwargs.get('cities') or [],
        'states': kwargs.get('states') or [],
        'owners': kwargs.get('owners') or [],
        'require_consent': bool(kwargs.get('require_consent')),
        'whole_org': bool(kwargs.get('whole_org')),
    })
    try:
        estimate = aud.estimate(org.id, filt, user)
    except aud.AudienceError as exc:
        raise ToolError(str(exc)) from exc

    audience = MarketingAudience(
        organization_id=org.id,
        user_id=user.id,
        name=kwargs.get('audience_name'),
        filter=filt.to_dict(),
        is_saved=bool(kwargs.get('save_audience')),
        cached_count=estimate.sendable_count,
    )
    db.session.add(audience)
    db.session.flush()

    kind = 'drip' if kwargs.get('kind') == 'drip' else 'one_time'
    campaign = MarketingCampaign(
        organization_id=org.id,
        user_id=user.id,
        name=name[:200],
        kind=kind,
        status='draft',
        audience_id=audience.id,
        timezone=kwargs.get('timezone') or 'America/Chicago',
        created_via='mcp' if ctx.surface == 'mcp' else 'bob',
    )
    db.session.add(campaign)
    db.session.flush()
    db.session.add(MarketingCampaignStep(
        organization_id=org.id,
        campaign_id=campaign.id,
        template_id=template.id,
        step_index=0,
        delay_days=0,
        send_hour_local=int(kwargs.get('send_hour') or 9),
    ))
    extra = kwargs.get('drip_steps') or []
    for index, step in enumerate(extra, start=1):
        if not isinstance(step, dict) or not step.get('template_id'):
            continue
        db.session.add(MarketingCampaignStep(
            organization_id=org.id,
            campaign_id=campaign.id,
            template_id=int(step['template_id']),
            step_index=index,
            delay_days=max(int(step.get('delay_days') or 3), 1),
            send_hour_local=int(step.get('send_hour') or 9),
        ))
        campaign.kind = 'drip'
    db.session.commit()

    quota = sending_config.quota_for(org)
    return ToolResult.success(
        f'Draft campaign “{campaign.name}” for {estimate.sendable_count} recipients. '
        'It has not been sent. Open the link to review and launch.',
        {
            'id': campaign.id,
            'status': campaign.status,
            'sendable': estimate.sendable_count,
            'excluded': estimate.excluded_count,
            'breakdown': estimate.breakdown(),
            'readiness_ok': readiness.ok,
            'readiness': readiness.message,
            'quota_remaining': quota.remaining,
            'launch_url': f'/marketing/campaigns/{campaign.id}',
        },
        record_url=f'/marketing/campaigns/{campaign.id}',
    )


def stage_campaign(ctx: BobContext, *, campaign_id: int) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    campaign = MarketingCampaign.query.filter_by(
        id=campaign_id, organization_id=org.id, user_id=user.id,
    ).first()
    if campaign is None:
        raise ToolError('That campaign is not available.')
    if campaign.status not in ('draft', 'pending_review'):
        raise ToolError('Only a draft can be staged for review.')
    campaign.status = 'pending_review'
    db.session.add(Notification(
        organization_id=org.id,
        user_id=user.id,
        category='marketing',
        title='Campaign ready to send',
        body=f'“{campaign.name}” is waiting for you to review and launch.',
        icon='fa-envelope',
        action_url=f'/marketing/campaigns/{campaign.id}',
    ))
    db.session.commit()
    return ToolResult.success(
        f'Staged “{campaign.name}” for review. Open the link and click Launch. '
        'Nothing has been emailed.',
        {
            'id': campaign.id,
            'status': campaign.status,
            'launch_url': f'/marketing/campaigns/{campaign.id}',
        },
        record_url=f'/marketing/campaigns/{campaign.id}',
    )


def add_suppression(ctx: BobContext, *, email: str, note: str | None = None) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    row = write_suppression(
        email, REASON_MANUAL,
        organization_id=org.id,
        created_by_id=user.id,
        note=note,
    )
    db.session.commit()
    return ToolResult.success(
        f'{email} will not receive marketing email from this org.',
        {'email': row.email if row else email, 'reason': 'manual'},
    )


def set_consent(ctx: BobContext, *, contact_id: int, marketing_consent: str) -> ToolResult:
    user, org = _user_org(ctx)
    _require_feature(org)
    if marketing_consent not in Contact.MARKETING_CONSENT_STATES:
        raise ToolError('marketing_consent must be unknown, opted_in, or opted_out.')
    contact = Contact.query.filter_by(
        id=contact_id, organization_id=org.id,
    ).first()
    if contact is None:
        raise ToolError('That contact is not available.')
    if contact.user_id != user.id and user.org_role not in ('owner', 'admin'):
        raise ToolError('That contact is not available.')
    contact.marketing_consent = marketing_consent
    contact.marketing_consent_source = 'manual'
    contact.marketing_consent_at = datetime.utcnow()
    if contact.email:
        if marketing_consent == 'opted_out':
            write_suppression(
                contact.email, REASON_UNSUBSCRIBE,
                organization_id=org.id,
                created_by_id=user.id,
            )
        else:
            release(contact.email, org.id, actor_id=user.id)
    db.session.commit()
    return ToolResult.success(
        f'Marketing consent for {contact.first_name} is now {marketing_consent}.',
        {'contact_id': contact.id, 'marketing_consent': marketing_consent},
        record_url=f'/contact/{contact.id}',
    )
