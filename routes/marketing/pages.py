"""Agent-facing marketing pages: overview, campaigns, templates, studio."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from werkzeug.datastructures import MultiDict

from flask import (
    abort, current_app, flash, jsonify, redirect, render_template, request,
    url_for, session,
)
from flask_login import current_user, login_required

from feature_flags import feature_required
from models import (
    Contact, MarketingAudience, MarketingCampaign, MarketingCampaignStep,
    MarketingSend, MarketingSuppression, MarketingTemplate, Notification,
    Organization, User, MarketingEnrollment, db,
)
from routes.marketing import marketing
from routes.marketing.access import campaign_or_404, require_campaigns, template_or_404
from services.marketing import workspace, activity
from services.marketing import audience as aud
from services.marketing import compliance
from services.marketing import launch as launchmod
from services.marketing import sending_config
from services.marketing import studio as studio_mod
from services.marketing import suppression as supp
from services.marketing import system_templates
from services.marketing import templates as tpl
from services.marketing.context import shell_for
from services.marketing.blocks import insert_before_signature
from services.marketing.merge_fields import (
    MERGE_FIELDS, coerce_sample_values, resolve_values, short_label,
    studio_sample_values, used_keys,
)
from services.marketing.render import preview as preview_email
from services.marketing import send as sendmod
from services.marketing.templates import TemplateError
from services.tenant_service import org_query


def _org():
    return current_user.organization


def _enable_flag_seed():
    """Ensure the starter library exists the first time an org opens Marketing."""
    org = _org()
    if org is None:
        abort(404)
    system_templates.seed_for_org(org.id, commit=True)
    return org


@marketing.context_processor
def marketing_context():
    org = _org() if current_user.is_authenticated else None
    return {
        'saved_marketing_draft': session.pop('saved_marketing_draft', None),
        'brokerage_name': (getattr(org, 'broker_name', None) or getattr(org, 'name', None) or 'Your brokerage'),
        'can_manage_marketing': bool(current_user.is_authenticated and (
            current_user.org_role in ('owner', 'admin') or current_user.role == 'admin')),
    }


def _confirm_draft_save():
    key = request.form.get('_draft_recovery_key', '')
    if key.startswith(f'marketing:{current_user.organization_id}:{current_user.id}:') and len(key) < 250:
        session['saved_marketing_draft'] = key


def _editable_campaign(campaign_id):
    campaign = campaign_or_404(campaign_id)
    if campaign.user_id != current_user.id or not campaign.is_editable:
        abort(403)
    if request.method == 'POST':
        campaign = MarketingCampaign.query.filter_by(id=campaign.id).with_for_update().one()
        if not campaign.is_editable:
            abort(409)
    return campaign


def _kept_images(raw) -> list:
    try:
        items = json.loads(raw or '[]')
    except json.JSONDecodeError:
        return []
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get('image_url'):
            continue
        out.append({
            'type': 'image',
            'image_url': item['image_url'],
            'alt': item.get('alt') or 'Photo',
        })
    return out


CATEGORY_LABELS = {
    'check_in': 'Check in',
    'open_house': 'Open house',
    'market_update': 'Market update',
    'just_listed': 'Just listed',
    'just_sold': 'Just sold',
    'holiday': 'Seasonal',
    'newsletter': 'Newsletter',
    'other': 'Other',
}

AUDIENCE_LABELS = {
    'past_clients': 'Past clients',
    'buyers': 'Buyers',
    'sellers': 'Sellers',
    'neighbors': 'Neighbors',
}


def _draft_name(form) -> str:
    named = (form.get('name') or '').strip()
    if named:
        return named[:200]
    prompt = (form.get('prompt') or '').strip()
    if prompt:
        first = prompt.split('\n', 1)[0].strip()
        return (first[:72] + '…') if len(first) > 72 else first
    return 'Untitled'


def _generate_extra(form) -> str:
    bits = []
    length = (form.get('length') or 'regular').strip()
    if length == 'short':
        bits.append(
            'Keep it to two or three short paragraphs. Skip the hero unless '
            'the prompt asks for one.'
        )
    elif length == 'full':
        bits.append(
            'Use a hero if it fits, then several blocks. A callout or stat '
            'row is welcome when there are numbers.'
        )
    audience = (form.get('audience') or '').strip()
    if audience in AUDIENCE_LABELS:
        bits.append(f'Write as if this is going to {AUDIENCE_LABELS[audience].lower()}.')
    if form.get('include_cta'):
        bits.append(
            'Include exactly one button. Use a bracketed URL placeholder if '
            'the real link is unknown.'
        )
    else:
        bits.append('Do not include a button unless the prompt names a specific link.')
    if form.get('include_hero'):
        bits.append('Start with a hero block.')
    else:
        bits.append('Do not use a hero block.')
    return '\n'.join(bits)


def _cover_html(html: str) -> str:
    """Keep thumbnail iframes on a light canvas inside dark AM chrome."""
    if not html:
        return ''
    if 'color-scheme' not in html:
        html = html.replace(
            '<head>',
            '<head><meta name="color-scheme" content="light">',
            1,
        )
    return html


def _preview_card(org, template, ctx=None):
    ctx = ctx or shell_for(org, current_user)
    html = ''
    try:
        _, html = preview_email(template.blocks or [], ctx, template.subject or '')
    except (TemplateError, ValueError):
        html = ''
    return {
        'template': template,
        'html': _cover_html(html),
        'kicker': CATEGORY_LABELS.get(template.category, template.category),
        'subject': template.subject or '',
    }


def _starter_cards(org, templates):
    ctx = shell_for(org, current_user)
    order = {
        spec['name']: index
        for index, spec in enumerate(system_templates.SYSTEM_TEMPLATES)
    }
    starters = [t for t in templates if t.source == 'system']
    starters.sort(key=lambda t: order.get(t.name, 99))
    return [_preview_card(org, template, ctx) for template in starters]


def _template_cards(org, templates):
    ctx = shell_for(org, current_user)
    return [_preview_card(org, template, ctx) for template in templates]


def _blank_draft() -> dict:
    return {
        'subject': '',
        'preheader': '',
        'blocks': [
            {'type': 'paragraph', 'text': '[Write your message here]'},
            {'type': 'signature'},
        ],
        'name': '',
        'category': 'other',
        'findings': [],
        'placeholders': [],
    }


def _finding_dicts(findings) -> list[dict]:
    payload = []
    for finding in findings:
        if hasattr(finding, 'to_dict'):
            payload.append(finding.to_dict())
            continue
        payload.append({
            'severity': getattr(finding, 'severity', ''),
            'field': getattr(finding, 'field', None),
            'matched_text': getattr(finding, 'matched_text', None),
            'message': getattr(finding, 'message', ''),
            'protected_class': getattr(finding, 'protected_class', None),
            'block_index': getattr(finding, 'block_index', None),
        })
    return payload


def _apply_restored_compliance(draft: dict) -> None:
    """Re-lint restored copy the same way a normal studio load does."""
    try:
        prepared = tpl.prepare(
            draft.get('subject') or '',
            draft.get('preheader') or '',
            draft.get('blocks') or [],
            acknowledge_warnings=False,
        )
    except TemplateError:
        blocks = draft.get('blocks') or []
        if not isinstance(blocks, list):
            return
        try:
            findings = tpl.scan_template(
                draft.get('subject') or '',
                draft.get('preheader') or '',
                blocks,
            )
        except (TypeError, AttributeError, ValueError):
            return
        draft['findings'] = _finding_dicts(findings)
        draft['compliance_state'] = compliance.state_for(findings)
        return
    draft['subject'] = prepared['subject']
    draft['preheader'] = prepared['preheader'] or ''
    draft['blocks'] = prepared['blocks']
    draft['findings'] = prepared['findings']
    draft['compliance_state'] = prepared['compliance_state']
    draft['placeholders'] = prepared['placeholders']


def _restore_draft(form) -> dict | None:
    """Keep the email on screen when a rewrite fails."""
    try:
        blocks = json.loads(form.get('current_blocks') or '[]')
    except json.JSONDecodeError:
        blocks = []
    if not isinstance(blocks, list):
        blocks = []
    subject = (form.get('current_subject') or '').strip()
    preheader = (form.get('current_preheader') or '').strip()
    name = (form.get('current_name') or '').strip()
    if not blocks and not subject and not name:
        return None
    draft = {
        'subject': subject,
        'preheader': preheader,
        'blocks': blocks,
        'name': name,
        'category': form.get('category') or 'other',
        'findings': [],
        'placeholders': [],
    }
    model = (form.get('current_model') or '').strip()
    if model:
        draft['model'] = model
    generation_prompt = (form.get('current_generation_prompt') or '').strip()
    if generation_prompt:
        draft['prompt'] = generation_prompt
    _apply_restored_compliance(draft)
    return draft


def _require_campaign_template(template):
    if not tpl.is_active(template):
        raise ValueError(f'"{template.name}" is not active.')
    return template


WAIT_DAYS = {'week': 7, 'month': 30}


def _sending_mailbox(org, user_id):
    try:
        integration = sending_config.gmail_for(user_id, org.id)
        return {'sending_email': integration.connected_email, 'gmail_error': None}
    except sending_config.GmailConnectionError as exc:
        return {'sending_email': None, 'gmail_error': str(exc)}


def _wizard_context(org, templates, groups, posted=None):
    mine, org_saved = tpl.split_saved(templates, current_user.id)
    posted = posted or {}
    picked = []
    raw_ids = posted.getlist('contact_id') if hasattr(posted, 'getlist') else []
    if raw_ids:
        ids = []
        for raw in raw_ids:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if ids:
            query = Contact.query.filter(
                Contact.organization_id == org.id,
                Contact.id.in_(ids),
            )
            query = query.filter_by(user_id=current_user.id)
            by_id = {contact.id: contact for contact in query.all()}
            picked = [by_id[i] for i in ids if i in by_id]
    extra_steps = []
    if hasattr(posted, 'getlist'):
        extra_ids = posted.getlist('step_template_id')
        extra_waits = posted.getlist('step_wait')
        for index, raw_id in enumerate(extra_ids):
            extra_steps.append({
                'template_id': raw_id,
                'wait': extra_waits[index] if index < len(extra_waits) else 'week',
            })
    return {
        **_sending_mailbox(org, current_user.id),
        'templates': templates,
        'template_cards': _template_cards(org, templates),
        'mine_cards': _template_cards(org, mine),
        'org_cards': _template_cards(org, org_saved),
        'groups': groups,
        'merge_fields': MERGE_FIELDS,
        'quota': sending_config.quota_for(org),
        'campaign': None,
        'nav': 'campaigns',
        'posted': posted,
        'picked_contacts': picked,
        'extra_steps': extra_steps,
    }


def _merge_groups(samples: dict, used: set | None = None):
    groups = (
        ('Contact', 'contact.'),
        ('You', 'agent.'),
        ('Organization', 'org.'),
    )
    used = used or set()
    out = []
    for title, prefix in groups:
        fields = []
        for field in MERGE_FIELDS:
            if not field.key.startswith(prefix):
                continue
            fields.append({
                'key': field.key,
                'label': short_label(field),
                'example': samples.get(field.key, field.example),
                'fallback': field.default_fallback or '',
                'used': field.key in used,
            })
        if fields:
            out.append({'title': title, 'fields': fields})
    return out


def _studio_preview(org, draft, samples=None, fill_samples=False) -> dict:
    empty = {'html': '', 'subject': '', 'preheader': ''}
    if not draft or not (draft.get('blocks') or draft.get('subject')):
        return empty
    try:
        ctx = shell_for(org, current_user, preheader=draft.get('preheader') or '')
        values = samples if samples is not None else studio_sample_values(
            current_user, org,
        )
        subject, html = preview_email(
            draft.get('blocks') or [], ctx, draft.get('subject') or '',
            editable=True,
            fill_samples=fill_samples,
            sample_values=values,
        )
        return {
            'html': html,
            'subject': subject,
            'preheader': draft.get('preheader') or '',
        }
    except (TemplateError, ValueError):
        return {
            **empty,
            'subject': draft.get('subject') or '',
            'preheader': draft.get('preheader') or '',
        }


def _studio_chrome(**extra):
    extra.setdefault('merge_fields', MERGE_FIELDS)
    extra.setdefault('nav', 'templates' if request.values.get('flow') == 'template' else '')
    extra.setdefault('flow', 'campaign' if request.values.get('campaign', type=int) else request.values.get('flow') or 'campaign')
    extra.setdefault('workspace_campaign_id', request.values.get('campaign', type=int))
    extra.setdefault('workspace_step', request.values.get('step') or '0')
    extra.setdefault('category_choices', list(CATEGORY_LABELS.items()))
    extra.setdefault('tone_choices', (
        ('warm', 'Warm'),
        ('direct', 'Direct'),
        ('formal', 'Formal'),
    ))
    extra.setdefault('length_choices', (
        ('short', 'Short'),
        ('regular', 'Regular'),
        ('full', 'Full'),
    ))
    extra.setdefault('audience_choices', list(AUDIENCE_LABELS.items()))
    return extra


def _render_studio(org, template, draft, prompt='', **extra):
    samples = extra.pop('samples', None) or studio_sample_values(current_user, org)
    used = used_keys(
        (draft or {}).get('subject') or '',
        (draft or {}).get('preheader') or '',
        (draft or {}).get('blocks') or [],
    )
    preview = _studio_preview(org, draft, samples, fill_samples=True)
    extra.setdefault('merge_groups', _merge_groups(samples, used))
    extra.setdefault('preview_filled_subject', '')
    extra.update(_sending_mailbox(org, current_user.id))
    return render_template(
        'marketing/studio.html',
        template=template,
        draft=draft,
        preview_html=preview['html'],
        preview_subject=preview['subject'],
        preview_preheader=preview['preheader'],
        prompt=prompt,
        **_studio_chrome(**extra),
    )


@marketing.route('/marketing/overview')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def overview():
    return redirect(url_for('marketing.campaigns_list'))


@marketing.route('/marketing/campaigns')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaigns_list():
    org = _enable_flag_seed()
    view = request.args.get('status', 'active')
    if view not in ('active', 'draft', 'completed'):
        view = 'active'
    search = request.args.get('q', '').strip()[:200]
    states = {'draft': ('draft', 'pending_review'),
              'completed': ('completed', 'cancelled')}.get(
                  view, ('scheduled', 'sending', 'active', 'paused', 'failed'))
    query = org_query(MarketingCampaign).filter(
        MarketingCampaign.user_id == current_user.id,
        MarketingCampaign.status.in_(states),
    )
    if search:
        query = query.filter(MarketingCampaign.name.icontains(search, autoescape=True))
    pagination = query.order_by(MarketingCampaign.updated_at.desc(), MarketingCampaign.id.desc()).paginate(
        page=request.args.get('page', 1, type=int), per_page=20, error_out=False)
    campaigns = pagination.items
    return render_template(
        'marketing/campaigns.html', campaigns=campaigns,
        activity_by_id=activity.snapshots(campaigns), pagination=pagination,
        status=view, search=search, local_time=activity.local_time,
        nav={'draft': 'drafts', 'completed': 'finished'}.get(view, 'campaigns'),
        **_sending_mailbox(org, current_user.id),
    )


@marketing.route('/marketing/sent')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def sent_emails():
    from sqlalchemy.orm import joinedload
    pagination = org_query(MarketingSend).join(MarketingCampaign).filter(
        MarketingCampaign.user_id == current_user.id,
        MarketingSend.sent_at.isnot(None),
        MarketingSend.contact.has(Contact.user_id == current_user.id),
    ).options(joinedload(MarketingSend.campaign), joinedload(MarketingSend.step)).order_by(
        MarketingSend.sent_at.desc(), MarketingSend.id.desc(),
    ).paginate(page=request.args.get('page', 1, type=int), per_page=50, error_out=False)
    from services.marketing import tracking
    metrics = tracking.per_send([s.id for s in pagination.items], current_user.organization_id)
    return render_template('marketing/sent.html', pagination=pagination,
                           metrics=metrics, local_time=activity.local_time, nav='sent')


@marketing.route('/marketing/campaigns/new', methods=['GET', 'POST'])
@marketing.route('/marketing/campaigns/<int:campaign_id>/edit', methods=['GET', 'POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_new(campaign_id=None):
    org = _enable_flag_seed()
    campaign = _editable_campaign(campaign_id) if campaign_id else None
    if request.method == 'GET' and campaign is None:
        return redirect(url_for('marketing.library', flow='campaign'))
    posted = workspace.form_for(campaign) if campaign else MultiDict()
    if request.method == 'POST':
        posted = request.form
        try:
            campaign = _build_campaign_from_form(org, campaign)
            action = request.form.get('action') or 'save'
            if action == 'launch':
                launchmod.launch(campaign, org, current_user)
                _confirm_draft_save()
                flash('Campaign scheduled.' if campaign.status == 'scheduled' else 'Your campaign is queued to send.', 'success')
                return redirect(url_for('marketing.campaign_detail', campaign_id=campaign.id))
            db.session.commit()
            _confirm_draft_save()
            if action == 'add_email':
                return redirect(url_for('marketing.library', flow='campaign', campaign=campaign.id, step='new'))
            if action.startswith('edit_email:'):
                return redirect(url_for('marketing.studio', flow='campaign', campaign=campaign.id, step=action.split(':')[1]))
            flash('Draft saved. You can continue editing anytime.', 'success')
            return redirect(url_for('marketing.campaign_new', campaign_id=campaign.id,
                                    panel='review' if request.form.get('panel') == 'review' else None))
        except (launchmod.LaunchError, aud.AudienceError, TemplateError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), 'error')
    templates = tpl.campaign_pickable(org.id, current_user.id).all()
    ids = [posted.get('template_id')] + posted.getlist('step_template_id')
    for raw in ids:
        if raw and str(raw).isdigit():
            email = template_or_404(int(raw))
            if email not in templates:
                templates.append(email)
    context = _wizard_context(org, templates, aud.group_choices(org.id, current_user), posted=posted)
    context['campaign'] = campaign
    context['selected_email'] = next((t for t in templates if str(t.id) == posted.get('template_id')), None)
    return render_template('marketing/wizard.html', **context)


def _build_campaign_from_form(org, campaign=None):
    return workspace.save_form(org, current_user, request.form, campaign)


@marketing.route('/marketing/campaigns/<int:campaign_id>')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_detail(campaign_id):
    campaign = campaign_or_404(campaign_id)
    from services.marketing import tracking
    details = activity.snapshot(campaign)
    engagement = request.args.get('engagement', 'all')
    if engagement not in tracking.FILTERS:
        engagement = 'all'
    metrics = tracking.campaign_summary(campaign)
    details['engagement_revision'] = tracking.revision(campaign)
    step_id = request.args.get('step', type=int)
    selected = next((s for s in details['steps'] if s.id == step_id), None)
    if step_id is not None and selected is None:
        abort(404)
    if selected is None:
        selected = (details['next_card']['step'] if details['next_card']
                    else next(iter(details['steps']), None))
    people = activity.recipient_page(campaign, selected, details['steps'], _org(),
                                    request.args.get('page', 1, type=int), engagement=engagement) if selected else None
    return render_template(
        'marketing/campaign_detail.html', campaign=campaign, details=details,
        selected_step=selected, people=people, metrics=metrics, engagement_filter=engagement,
        nav='finished' if campaign.status in ('completed', 'cancelled') else 'drafts' if campaign.is_editable else 'campaigns',
        **_sending_mailbox(_org(), campaign.user_id),
    )


@marketing.route('/marketing/campaigns/<int:campaign_id>/steps/<int:step_id>/preview')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_step_preview(campaign_id, step_id):
    campaign = campaign_or_404(campaign_id)
    step = MarketingCampaignStep.query.filter_by(
        id=step_id, campaign_id=campaign.id, organization_id=campaign.organization_id,
    ).first_or_404()
    contact_id = request.args.get('contact', type=int)
    values = studio_sample_values(campaign.owner, _org())
    if contact_id is not None:
        enrollment = MarketingEnrollment.query.filter_by(
            campaign_id=campaign.id, organization_id=campaign.organization_id,
            contact_id=contact_id,
        ).first_or_404()
        if enrollment.contact.user_id != current_user.id:
            abort(404)
        values = resolve_values(enrollment.contact, campaign.owner, _org())
    template = step.template
    if template is None:
        abort(404)
    _, html = preview_email(template.blocks or [], shell_for(
        _org(), campaign.owner, preheader=template.preheader,
        eyebrow=(template.category or '').replace('_', ' ') or None,
    ), template.subject, sample_values=values)
    from flask import make_response
    response = make_response(html)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src https: data:; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com"
    return response


@marketing.route('/marketing/campaigns/<int:campaign_id>/delete', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_delete(campaign_id):
    campaign = _editable_campaign(campaign_id)
    if campaign.sends.count() or campaign.enrollments.count():
        abort(409)
    db.session.delete(campaign)
    db.session.commit()
    flash('Draft campaign deleted.', 'success')
    return redirect(url_for('marketing.campaigns_list', status='draft'))


@marketing.route('/marketing/templates/<int:template_id>/delete', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def template_delete(template_id):
    template = template_or_404(template_id)
    if template.created_by_id != current_user.id or not tpl.is_saved(template):
        abort(403)
    # Retain references in existing campaigns and sent history.
    template.status = 'archived'
    db.session.commit()
    flash('Saved template deleted.', 'success')
    return redirect(url_for('marketing.library', flow='template'))


@marketing.route('/marketing/campaigns/<int:campaign_id>/launch', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_launch(campaign_id):
    campaign = campaign_or_404(campaign_id)
    try:
        launchmod.launch(campaign, _org(), current_user)
        flash('Campaign scheduled.' if campaign.status == 'scheduled' else 'Your campaign is queued to send.', 'success')
    except launchmod.LaunchError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('marketing.campaign_detail', campaign_id=campaign.id))


@marketing.route('/marketing/campaigns/<int:campaign_id>/pause', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_pause(campaign_id):
    campaign = campaign_or_404(campaign_id)
    try:
        launchmod.pause(campaign)
        flash('Campaign paused.', 'success')
    except launchmod.LaunchError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('marketing.campaign_detail', campaign_id=campaign.id))


@marketing.route('/marketing/campaigns/<int:campaign_id>/resume', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_resume(campaign_id):
    campaign = campaign_or_404(campaign_id)
    try:
        launchmod.resume(campaign)
        flash('Campaign resumed.', 'success')
    except launchmod.LaunchError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('marketing.campaign_detail', campaign_id=campaign.id))


@marketing.route('/marketing/campaigns/<int:campaign_id>/cancel', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_cancel(campaign_id):
    campaign = campaign_or_404(campaign_id)
    try:
        launchmod.cancel(campaign)
        flash('Campaign cancelled.', 'success')
    except launchmod.LaunchError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('marketing.campaign_detail', campaign_id=campaign.id))


@marketing.route('/marketing/campaigns/<int:campaign_id>/progress')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_progress(campaign_id):
    campaign = campaign_or_404(campaign_id)
    details = activity.snapshot(campaign)
    from services.marketing import tracking
    return jsonify({
        'engagement_revision': tracking.revision(campaign),
        'revision': details['revision'],
        'status_label': details['status_label'],
        'status': campaign.status,
        'queued': campaign.queued_count,
        'sent': campaign.sent_count,
        'delivered': campaign.delivered_count,
        'bounced': campaign.bounced_count,
        'failed': campaign.failed_count,
        'skipped': campaign.skipped_count,
        'unsubscribed': campaign.unsubscribed_count,
        'total': campaign.total_recipients,
        'auto_paused_reason': campaign.auto_paused_reason,
    })


def _render_library(org, **extra):
    templates = tpl.visible_to(org.id, current_user.id).all()
    saved = [t for t in templates if tpl.is_saved(t)]
    mine, org_saved = tpl.split_saved(saved, current_user.id)
    extra.setdefault('prompt', request.args.get('prompt') or '')
    extra.setdefault('create_error', '')
    return render_template(
        'marketing/library.html',
        starters=_starter_cards(org, templates),
        saved=saved,
        mine_cards=_template_cards(org, mine),
        org_cards=_template_cards(org, org_saved),
        **_studio_chrome(**extra),
    )


@marketing.route('/marketing/library')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def library():
    org = _enable_flag_seed()
    return _render_library(org)


@marketing.route('/marketing/studio', methods=['GET', 'POST'])
@marketing.route('/marketing/studio/<int:template_id>', methods=['GET', 'POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def studio(template_id=None):
    org = _enable_flag_seed()
    flow = request.values.get('flow') or 'campaign'
    campaign_id = request.values.get('campaign', type=int)
    campaign = _editable_campaign(campaign_id) if campaign_id else None
    step_key = request.values.get('step') or '0'
    steps = workspace.steps_for(campaign) if campaign else []
    step = next((s for s in steps if str(s.step_index) == step_key), None)
    if campaign and step_key != 'new' and step is None:
        abort(404)
    template = template_or_404(template_id) if template_id else (step.template if step else None)
    if template and template.source == 'campaign' and (campaign is None or step is None or step.template_id != template.id):
        abort(403)
    if campaign and template_id and (step is None or step.template_id != template_id):
        abort(403)
    if campaign:
        flow = 'campaign'
    if flow == 'campaign' and template and template.source != 'campaign':
        # Old shared templates are copied when saved into a campaign.
        template_id = None
    if request.method == 'POST':
        action = request.form.get('action') or 'save'
        try:
            if action == 'generate':
                generated = studio_mod.generate(
                    request.form.get('prompt') or '',
                    tone=request.form.get('tone') or 'warm',
                    category=request.form.get('category') or None,
                    extra_instructions=_generate_extra(request.form),
                )
                kept = _kept_images(request.form.get('keep_images'))
                if kept:
                    generated['blocks'] = insert_before_signature(
                        generated.get('blocks') or [], kept,
                    )
                generated['name'] = _draft_name(request.form)
                generated['category'] = request.form.get('category') or 'other'
                return _render_studio(
                    org, template, generated,
                    prompt=request.form.get('prompt') or '',
                )
            saved = tpl.save(
                organization_id=org.id,
                user_id=current_user.id,
                org=org,
                agent=current_user,
                name=request.form.get('name') or request.form.get('subject') or 'Untitled email',
                subject=request.form.get('subject') or '',
                preheader=request.form.get('preheader') or '',
                blocks=json.loads(request.form.get('blocks') or '[]'),
                description=request.form.get('description') or '',
                category=request.form.get('category') or 'other',
                visibility='org' if request.form.get('share') else 'private',
                template=template if flow == 'template' or (template and template.source == 'campaign') else None,
                source='campaign' if flow == 'campaign' else None,
                commit=False,
                acknowledge_warnings=bool(request.form.get('acknowledge')),
                generated_by_ai=bool(request.form.get('generated_by_ai')),
                prompt=request.form.get('prompt') or None,
                active=action != 'save_draft',
            )
            if flow == 'campaign':
                if campaign is None:
                    campaign = MarketingCampaign(organization_id=org.id, user_id=current_user.id,
                        name=saved.name, status='draft', kind='one_time', created_via='web')
                    db.session.add(campaign)
                    db.session.flush()
                if step is None:
                    if len(steps) >= 10:
                        raise ValueError('Use up to ten emails in a sequence.')
                    step = MarketingCampaignStep(organization_id=org.id, campaign_id=campaign.id,
                        step_index=len(steps), name=f'Email {len(steps) + 1}',
                        delay_days=(steps[-1].delay_days + 7 if steps else 0), send_hour_local=9)
                    db.session.add(step)
                step.template_id = saved.id
                campaign.kind = 'drip' if step.step_index else campaign.kind
                if step.step_index == 0:
                    campaign.name = saved.name
                campaign.updated_at = datetime.utcnow()
                if action == 'save_template':
                    tpl.save(organization_id=org.id, user_id=current_user.id, org=org, agent=current_user,
                        name=saved.name, subject=saved.subject, preheader=saved.preheader, blocks=saved.blocks,
                        category=saved.category, source='manual', visibility='private', commit=False,
                        acknowledge_warnings=bool(saved.compliance_ack_at))
                db.session.commit()
                _confirm_draft_save()
                if action in ('save_draft', 'save_template'):
                    flash('Draft and reusable template saved.' if action == 'save_template' else 'Draft saved.', 'success')
                    return redirect(url_for('marketing.studio', flow='campaign', campaign=campaign.id, step=step.step_index))
                return redirect(url_for('marketing.campaign_new', campaign_id=campaign.id, panel='review' if campaign_id else None))
            db.session.commit()
            _confirm_draft_save()
            flash('Template saved.', 'success')
            return redirect(url_for('marketing.studio', template_id=saved.id, flow='template'))
        except (TemplateError, json.JSONDecodeError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            if action == 'generate' and request.form.get('from_library'):
                return _render_library(
                    org,
                    prompt=request.form.get('prompt') or '',
                    create_error=str(exc),
                )
            if action == 'generate':
                return _render_studio(
                    org, template, _restore_draft(request.form) or _blank_draft(),
                    prompt=request.form.get('prompt') or '',
                )
            if action != 'generate':
                try:
                    posted_blocks = json.loads(request.form.get('blocks') or '[]')
                except json.JSONDecodeError:
                    posted_blocks = []
                draft = {
                    'subject': request.form.get('subject') or '',
                    'preheader': request.form.get('preheader') or '',
                    'blocks': posted_blocks,
                    'name': request.form.get('name') or '',
                    'category': request.form.get('category') or 'other',
                    'findings': [],
                    'placeholders': [],
                }
                return _render_studio(
                    org, template, draft,
                    prompt=request.form.get('prompt') or '',
                )
    if template:
        draft = {
            'subject': template.subject,
            'preheader': template.preheader,
            'blocks': template.blocks,
            'name': campaign.name if campaign and step and step.step_index == 0 else template.name,
            'category': template.category,
            'compliance_state': template.compliance_state,
            'findings': template.compliance_findings,
            'placeholders': [],
        }
        return _render_studio(org, template, draft)
    from_id = request.args.get('from', type=int)
    if from_id:
        source = template_or_404(from_id)
        draft = {
            'subject': source.subject,
            'preheader': source.preheader,
            'blocks': source.blocks,
            'name': source.name,
            'category': source.category,
            'compliance_state': source.compliance_state,
            'findings': source.compliance_findings,
            'placeholders': [],
        }
        return _render_studio(org, None, draft)
    return _render_studio(org, None, _blank_draft())


@marketing.route('/marketing/api/preview', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def api_preview():
    require_campaigns()
    payload = request.get_json(silent=True) or {}
    try:
        blocks = payload.get('blocks') or []
        subject = payload.get('subject') or ''
        preheader = payload.get('preheader') or ''
        ctx = shell_for(_org(), current_user, preheader=preheader)
        fill_samples = bool(payload.get('fill_samples'))
        values = coerce_sample_values(
            payload.get('samples'),
            studio_sample_values(current_user, _org()),
        )
        filled_subject, html = preview_email(
            blocks, ctx, subject, editable=True,
            fill_samples=fill_samples,
            sample_values=values,
        )
        prepared = tpl.prepare(
            subject, preheader, blocks,
            acknowledge_warnings=False,
            allow_incomplete=True,
        )
        return jsonify({
            'subject': filled_subject,
            'html': html,
            'used_keys': prepared['merge_fields_used'],
            'compliance_state': prepared['compliance_state'],
            'findings': prepared['findings'],
            'placeholders': prepared['placeholders'],
        })
    except (TemplateError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400


@marketing.route('/marketing/api/send-test', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def api_send_test():
    require_campaigns()
    payload = request.get_json(silent=True) or {}
    try:
        if payload.get('template_id'):
            email = template_or_404(int(payload['template_id']))
            payload = {**payload, 'subject': email.subject, 'preheader': email.preheader, 'blocks': email.blocks}
            mailbox = sending_config.gmail_for(current_user.id, _org().id)
            payload['to'] = mailbox.connected_email
        to_emails = sendmod.parse_test_recipients(payload.get('to') or '')
        values = coerce_sample_values(
            payload.get('samples'),
            studio_sample_values(current_user, _org()),
        )
        result = sendmod.send_test(
            org=_org(),
            agent=current_user,
            subject=payload.get('subject') or '',
            preheader=payload.get('preheader') or '',
            blocks=payload.get('blocks') or [],
            to_emails=to_emails,
            sample_values=values,
            category=payload.get('category') or '',
            from_name=payload.get('from_name') or None,
            reply_to=payload.get('reply_to') or None,
        )
        db.session.commit()
        return jsonify(result)
    except sendmod.SendError as exc:
        return jsonify({'error': str(exc)}), 400
    except (TemplateError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400


@marketing.route('/marketing/api/estimate', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def api_estimate():
    require_campaigns()
    payload = request.get_json(silent=True) or {}
    try:
        estimate = aud.estimate(
            current_user.organization_id, payload, current_user,
        )
        result = estimate.as_dict()
        offset = max(0, min(int(payload.get('offset') or 0), estimate.matched))
        rows = [dict(id=r.contact.id, name=f'{r.contact.first_name or ""} {r.contact.last_name or ""}'.strip(), email=r.email, reason='Ready') for r in estimate.sendable]
        rows += [dict(id=r.contact.id, name=f'{r.contact.first_name or ""} {r.contact.last_name or ""}'.strip(), email=r.email or '', reason=r.reason.replace('_', ' ')) for r in estimate.excluded]
        result['recipients'] = rows[offset:offset + 50]
        result['has_more'] = offset + 50 < len(rows)
        return jsonify(result)
    except (aud.AudienceError, ValueError, TypeError) as exc:
        return jsonify({'error': str(exc)}), 400


@marketing.route('/marketing/api/contacts')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def api_contacts():
    require_campaigns()
    query_text = (request.args.get('q') or '').strip()
    query = Contact.query.filter_by(organization_id=current_user.organization_id)
    query = query.filter_by(user_id=current_user.id)
    if query_text:
        like = f'%{query_text}%'
        query = query.filter(
            db.or_(
                Contact.first_name.ilike(like),
                Contact.last_name.ilike(like),
                Contact.email.ilike(like),
            )
        )
    contacts = query.order_by(Contact.last_name, Contact.first_name).limit(20).all()
    return jsonify([
        {
            'id': contact.id,
            'name': f'{contact.first_name or ""} {contact.last_name or ""}'.strip(),
            'email': contact.email or '',
        }
        for contact in contacts
    ])


@marketing.route('/marketing/api/preview-as', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def api_preview_as():
    require_campaigns()
    payload = request.get_json(silent=True) or {}
    try:
        template = template_or_404(int(payload.get('template_id') or 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Pick a template.'}), 400
    contact = None
    raw_id = payload.get('contact_id')
    if raw_id:
        contact_query = Contact.query.filter_by(
            id=int(raw_id),
            organization_id=current_user.organization_id,
        )
        contact_query = contact_query.filter_by(user_id=current_user.id)
        contact = contact_query.first()
        if contact is None:
            return jsonify({'error': 'That contact is not available.'}), 404
    org = _org()
    values = ({key: value or '' for key, value in resolve_values(contact, current_user, org).items()}
              if contact else studio_sample_values(current_user, org))
    ctx = shell_for(org, current_user, preheader=template.preheader)
    try:
        subject, html = preview_email(
            template.blocks or [], ctx, template.subject or '',
            fill_samples=True,
            sample_values=values,
        )
    except (TemplateError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'html': _cover_html(html), 'subject': subject})


@marketing.route('/marketing/api/upload', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def api_upload():
    require_campaigns()
    upload = request.files.get('file')
    if upload is None:
        return jsonify({'error': 'Choose an image.'}), 400
    from services.marketing.assets import AssetError, upload as store
    try:
        result = store(
            upload.read(),
            content_type=upload.mimetype or '',
            organization_id=current_user.organization_id,
            original_name=upload.filename or '',
        )
        return jsonify(result)
    except (AssetError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400


@marketing.route('/marketing/settings')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def settings():
    org = _org()
    suppressions = (
        MarketingSuppression.query
        .filter_by(organization_id=org.id, scope='org')
        .filter(MarketingSuppression.email.in_(db.session.query(db.func.lower(Contact.email)).filter_by(organization_id=org.id, user_id=current_user.id)))
        .order_by(MarketingSuppression.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template(
        'marketing/settings.html',
        org=org,
        quota=sending_config.quota_for(org),
        suppressions=suppressions,
        nav='settings',
        **_sending_mailbox(org, current_user.id),
    )


@marketing.route('/marketing/settings/suppressions/<int:suppression_id>/release', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def release_suppression(suppression_id):
    if current_user.org_role not in ('owner', 'admin') and current_user.role != 'admin':
        abort(403)
    row = org_query(MarketingSuppression).filter_by(id=suppression_id).first_or_404()
    if not Contact.query.filter_by(organization_id=current_user.organization_id, user_id=current_user.id).filter(db.func.lower(Contact.email) == row.email).first():
        abort(404)
    supp.release(row.email, current_user.organization_id, actor_id=current_user.id)
    db.session.commit()
    flash(f'{row.email} can receive marketing email again.', 'success')
    return redirect(url_for('marketing.settings'))


@marketing.route('/marketing/campaigns/<int:campaign_id>/sends/<int:send_id>/activity')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def send_activity(campaign_id, send_id):
    from models import MarketingTracking, MarketingTrackingEvent
    from sqlalchemy.orm import joinedload
    campaign = campaign_or_404(campaign_id)
    send = MarketingSend.query.filter_by(id=send_id, campaign_id=campaign.id,
        organization_id=campaign.organization_id).first_or_404()
    if not send.contact or send.contact.user_id != current_user.id:
        abort(404)
    tracked = MarketingTracking.query.filter_by(send_id=send.id,
        organization_id=campaign.organization_id).first_or_404()
    events = MarketingTrackingEvent.query.options(joinedload(MarketingTrackingEvent.link)).filter_by(
        tracking_id=tracked.id, organization_id=campaign.organization_id,
    ).order_by(MarketingTrackingEvent.occurred_at.desc(), MarketingTrackingEvent.id.desc()).limit(101).all()
    response = current_app.make_response(render_template('marketing/_tracking_activity.html',
        events=events[:100], more=len(events) > 100, campaign=campaign, local_time=activity.local_time))
    response.headers['Cache-Control'] = 'private, no-store'
    return response
