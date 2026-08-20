"""Agent-facing marketing pages: overview, campaigns, templates, studio."""
from __future__ import annotations

import json
from datetime import datetime

from flask import (
    abort, current_app, flash, jsonify, redirect, render_template, request,
    url_for,
)
from flask_login import current_user, login_required

from feature_flags import feature_required
from models import (
    Contact, MarketingAudience, MarketingCampaign, MarketingCampaignStep,
    MarketingSend, MarketingSuppression, MarketingTemplate, Notification,
    Organization, User, db,
)
from routes.marketing import marketing
from routes.marketing.access import campaign_or_404, require_campaigns, template_or_404
from services.marketing import audience as aud
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
            {'type': 'paragraph', 'text': ''},
            {'type': 'signature'},
        ],
        'name': '',
        'category': 'other',
        'findings': [],
        'placeholders': [],
    }


def _require_campaign_template(template):
    if not tpl.is_active(template):
        raise ValueError(f'"{template.name}" is not active.')
    return template


WAIT_DAYS = {'week': 7, 'month': 30}


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
            if not aud.can_use_org_scope(current_user):
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
        'templates': templates,
        'template_cards': _template_cards(org, templates),
        'mine_cards': _template_cards(org, mine),
        'org_cards': _template_cards(org, org_saved),
        'groups': groups,
        'merge_fields': MERGE_FIELDS,
        'can_org': aud.can_use_org_scope(current_user),
        'readiness': sending_config.readiness_for(org),
        'quota': sending_config.quota_for(org),
        'campaign': None,
        'nav': 'campaigns',
        'posted': posted,
        'picked_contacts': picked,
        'extra_steps': extra_steps,
        'org_users': _org_users(org) if aud.can_use_org_scope(current_user) else [],
    }


def _org_users(org):
    return (
        User.query.filter_by(organization_id=org.id)
        .order_by(User.first_name.asc(), User.last_name.asc())
        .all()
    )


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
    extra.setdefault('nav', 'templates')
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
    preview = _studio_preview(org, draft, samples, fill_samples=False)
    extra.setdefault('merge_groups', _merge_groups(samples, used))
    extra.setdefault('preview_filled_subject', '')
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
    org = _enable_flag_seed()
    quota = sending_config.quota_for(org)
    readiness = sending_config.readiness_for(org)
    campaigns = (
        org_query(MarketingCampaign)
        .filter_by(user_id=current_user.id)
        .order_by(MarketingCampaign.created_at.desc())
        .limit(20)
        .all()
    )
    active = org_query(MarketingCampaign).filter(
        MarketingCampaign.user_id == current_user.id,
        MarketingCampaign.status.in_(('sending', 'active', 'scheduled')),
    ).count()
    return render_template(
        'marketing/overview.html',
        quota=quota,
        readiness=readiness,
        campaigns=campaigns,
        active_count=active,
        nav='overview',
    )


@marketing.route('/marketing/campaigns')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaigns_list():
    _enable_flag_seed()
    status = request.args.get('status') or ''
    query = org_query(MarketingCampaign).filter_by(user_id=current_user.id)
    if status in ('active', 'drip'):
        query = query.filter(
            MarketingCampaign.kind == 'drip',
            MarketingCampaign.status.in_(('active', 'scheduled', 'paused')),
        )
    elif status:
        query = query.filter_by(status=status)
    campaigns = query.order_by(MarketingCampaign.created_at.desc()).all()
    return render_template(
        'marketing/campaigns.html',
        campaigns=campaigns,
        status=status,
        nav='campaigns',
    )


@marketing.route('/marketing/campaigns/new', methods=['GET', 'POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_new():
    org = _enable_flag_seed()
    templates = tpl.campaign_pickable(org.id, current_user.id).all()
    groups = aud.group_choices(org.id, current_user)
    if request.method == 'POST':
        try:
            campaign = _build_campaign_from_form(org)
            action = request.form.get('action') or 'save'
            if action == 'launch':
                launchmod.launch(campaign, org, current_user)
                flash('Campaign is sending.', 'success')
            else:
                db.session.commit()
                flash('Draft saved.', 'success')
            return redirect(url_for('marketing.campaign_detail', campaign_id=campaign.id))
        except (launchmod.LaunchError, aud.AudienceError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return render_template(
                'marketing/wizard.html',
                **_wizard_context(org, templates, groups, posted=request.form),
            )
    return render_template(
        'marketing/wizard.html',
        **_wizard_context(org, templates, groups),
    )


def _build_campaign_from_form(org) -> MarketingCampaign:
    name = (request.form.get('name') or '').strip()
    if not name:
        raise ValueError('Name this campaign so you can find it later.')
    template_id = int(request.form.get('template_id') or 0)
    if not template_id:
        raise ValueError('Pick a template.')
    template = _require_campaign_template(template_or_404(template_id))

    contact_ids = request.form.getlist('contact_id')
    filt = aud.parse_filter({
        'groups': request.form.getlist('groups'),
        'zips': [z.strip() for z in (request.form.get('zips') or '').split(',') if z.strip()],
        'cities': [c.strip() for c in (request.form.get('cities') or '').split(',') if c.strip()],
        'states': [s.strip() for s in (request.form.get('states') or '').split(',') if s.strip()],
        'owners': request.form.getlist('owners'),
        'contact_ids': contact_ids,
        'require_consent': bool(request.form.get('require_consent')),
        'whole_org': bool(request.form.get('whole_org')),
    })
    if not filt.has_selection():
        raise ValueError('Pick people or a filter. An empty list sends to nobody.')
    audience = MarketingAudience(
        organization_id=org.id,
        user_id=current_user.id,
        name=request.form.get('audience_name') or None,
        filter=filt.to_dict(),
        is_saved=bool(request.form.get('save_audience')),
    )
    db.session.add(audience)
    db.session.flush()

    extra_ids = [raw for raw in request.form.getlist('step_template_id') if raw]
    extra_waits = request.form.getlist('step_wait')
    kind = 'drip' if extra_ids or request.form.get('kind') == 'drip' else 'one_time'
    if not extra_ids:
        kind = 'one_time'
    hour = int(request.form.get('send_hour') or 9)
    campaign = MarketingCampaign(
        organization_id=org.id,
        user_id=current_user.id,
        name=name[:200],
        kind=kind,
        status='draft',
        audience_id=audience.id,
        timezone=request.form.get('timezone') or 'America/Chicago',
        created_via='web',
        from_name=request.form.get('from_name') or None,
        reply_to=request.form.get('reply_to') or current_user.email,
    )
    scheduled = (request.form.get('scheduled_at') or '').strip()
    if scheduled:
        try:
            campaign.scheduled_at = datetime.fromisoformat(scheduled)
        except ValueError:
            raise ValueError('That send time is not a valid date.')
    db.session.add(campaign)
    db.session.flush()

    db.session.add(MarketingCampaignStep(
        organization_id=org.id,
        campaign_id=campaign.id,
        template_id=template.id,
        step_index=0,
        name='Email 1',
        delay_days=0,
        send_hour_local=hour,
    ))
    delay = 0
    for index, raw_id in enumerate(extra_ids, start=1):
        extra = _require_campaign_template(template_or_404(int(raw_id)))
        wait = extra_waits[index - 1] if index - 1 < len(extra_waits) else 'week'
        delay += WAIT_DAYS.get(wait, 7)
        db.session.add(MarketingCampaignStep(
            organization_id=org.id,
            campaign_id=campaign.id,
            template_id=extra.id,
            step_index=index,
            name=f'Email {index + 1}',
            delay_days=delay,
            send_hour_local=hour,
        ))
    db.session.flush()
    return campaign


@marketing.route('/marketing/campaigns/<int:campaign_id>')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_detail(campaign_id):
    campaign = campaign_or_404(campaign_id)
    sends = (
        MarketingSend.query.filter_by(campaign_id=campaign.id)
        .order_by(MarketingSend.created_at.desc())
        .limit(200)
        .all()
    )
    steps = (
        MarketingCampaignStep.query.filter_by(campaign_id=campaign.id)
        .order_by(MarketingCampaignStep.step_index.asc())
        .all()
    )
    return render_template(
        'marketing/campaign_detail.html',
        campaign=campaign,
        sends=sends,
        steps=steps,
        nav='campaigns',
    )


@marketing.route('/marketing/campaigns/<int:campaign_id>/launch', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def campaign_launch(campaign_id):
    campaign = campaign_or_404(campaign_id)
    try:
        launchmod.launch(campaign, _org(), current_user)
        flash('Campaign is sending.', 'success')
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
    return jsonify({
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


@marketing.route('/marketing/library')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def library():
    org = _enable_flag_seed()
    templates = tpl.visible_to(org.id, current_user.id).all()
    saved = [t for t in templates if tpl.is_saved(t)]
    mine, org_saved = tpl.split_saved(saved, current_user.id)
    return render_template(
        'marketing/library.html',
        starters=_starter_cards(org, templates),
        saved=saved,
        mine_cards=_template_cards(org, mine),
        org_cards=_template_cards(org, org_saved),
        prompt=request.args.get('prompt') or '',
        **_studio_chrome(),
    )


@marketing.route('/marketing/studio', methods=['GET', 'POST'])
@marketing.route('/marketing/studio/<int:template_id>', methods=['GET', 'POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def studio(template_id=None):
    org = _enable_flag_seed()
    template = template_or_404(template_id) if template_id else None
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
                name=request.form.get('name') or 'Untitled',
                subject=request.form.get('subject') or '',
                preheader=request.form.get('preheader') or '',
                blocks=json.loads(request.form.get('blocks') or '[]'),
                description=request.form.get('description') or '',
                category=request.form.get('category') or 'other',
                visibility='org' if request.form.get('share') else 'private',
                template=template,
                acknowledge_warnings=bool(request.form.get('acknowledge')),
                generated_by_ai=bool(request.form.get('generated_by_ai')),
                prompt=request.form.get('prompt') or None,
                active=(
                    request.form.getlist('active')[-1] in ('1', 'true', 'on')
                    if request.form.getlist('active') else None
                ),
            )
            flash('Template saved.', 'success')
            return redirect(url_for('marketing.studio', template_id=saved.id))
        except (TemplateError, json.JSONDecodeError, ValueError) as exc:
            flash(str(exc), 'error')
            if action == 'generate' and template is None:
                return _render_studio(
                    org, None, _blank_draft(),
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
            'name': template.name,
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
        )
        return jsonify(result)
    except sendmod.SendError as exc:
        message = str(exc)
        if 'SENDGRID_API_KEY' in message:
            message = 'Email sending is not configured yet.'
        return jsonify({'error': message}), 400
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
        return jsonify(estimate.as_dict())
    except aud.AudienceError as exc:
        return jsonify({'error': str(exc)}), 400


@marketing.route('/marketing/api/contacts')
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def api_contacts():
    require_campaigns()
    query_text = (request.args.get('q') or '').strip()
    query = Contact.query.filter_by(organization_id=current_user.organization_id)
    if not aud.can_use_org_scope(current_user):
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
        if not aud.can_use_org_scope(current_user):
            contact_query = contact_query.filter_by(user_id=current_user.id)
        contact = contact_query.first()
        if contact is None:
            return jsonify({'error': 'That contact is not available.'}), 404
    org = _org()
    values = {
        key: value or ''
        for key, value in resolve_values(contact, current_user, org).items()
    }
    ctx = shell_for(org, current_user, preheader=template.preheader)
    try:
        subject, html = preview_email(
            template.blocks or [], ctx, template.subject or '',
            fill_samples=bool(contact),
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


@marketing.route('/marketing/settings', methods=['GET', 'POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def settings():
    org = _org()
    if current_user.org_role not in ('owner', 'admin') and current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        org.broker_name = (request.form.get('broker_name') or '').strip() or None
        org.broker_license_number = (request.form.get('broker_license_number') or '').strip() or None
        org.broker_address = (request.form.get('broker_address') or '').strip() or None
        db.session.commit()
        flash('Brokerage details saved.', 'success')
        return redirect(url_for('marketing.settings'))
    suppressions = (
        MarketingSuppression.query
        .filter_by(organization_id=org.id, scope='org')
        .order_by(MarketingSuppression.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template(
        'marketing/settings.html',
        org=org,
        readiness=sending_config.readiness_for(org),
        quota=sending_config.quota_for(org),
        suppressions=suppressions,
        nav='settings',
    )


@marketing.route('/marketing/settings/suppressions/<int:suppression_id>/release', methods=['POST'])
@login_required
@feature_required('EMAIL_CAMPAIGNS')
def release_suppression(suppression_id):
    if current_user.org_role not in ('owner', 'admin') and current_user.role != 'admin':
        abort(403)
    row = org_query(MarketingSuppression).filter_by(id=suppression_id).first_or_404()
    supp.release(row.email, current_user.organization_id, actor_id=current_user.id)
    db.session.commit()
    flash(f'{row.email} can receive marketing email again.', 'success')
    return redirect(url_for('marketing.settings'))
