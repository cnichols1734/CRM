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
    Organization, db,
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
    MERGE_FIELDS, coerce_sample_values, short_label, studio_sample_values,
    used_keys,
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


def _starter_cards(org, templates):
    ctx = shell_for(org, current_user)
    order = {
        spec['name']: index
        for index, spec in enumerate(system_templates.SYSTEM_TEMPLATES)
    }
    starters = [t for t in templates if t.source == 'system']
    starters.sort(key=lambda t: order.get(t.name, 99))
    cards = []
    for template in starters:
        html = ''
        try:
            _, html = preview_email(template.blocks or [], ctx, template.subject or '')
        except (TemplateError, ValueError):
            html = ''
        cards.append({
            'template': template,
            'html': html,
            'kicker': CATEGORY_LABELS.get(template.category, template.category),
        })
    return cards


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
    if status:
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
    templates = tpl.visible_to(org.id, current_user.id).filter_by(status='ready').all()
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
        templates=templates,
        groups=groups,
        merge_fields=MERGE_FIELDS,
        can_org=aud.can_use_org_scope(current_user),
        readiness=sending_config.readiness_for(org),
        quota=sending_config.quota_for(org),
        campaign=None,
        nav='campaigns',
    )


def _build_campaign_from_form(org) -> MarketingCampaign:
    name = (request.form.get('name') or '').strip()
    if not name:
        raise ValueError('Name this campaign so you can find it later.')
    template_id = int(request.form.get('template_id') or 0)
    template = template_or_404(template_id)

    filt = aud.parse_filter({
        'groups': request.form.getlist('groups'),
        'zips': [z.strip() for z in (request.form.get('zips') or '').split(',') if z.strip()],
        'cities': [c.strip() for c in (request.form.get('cities') or '').split(',') if c.strip()],
        'states': [s.strip() for s in (request.form.get('states') or '').split(',') if s.strip()],
        'owners': request.form.getlist('owners'),
        'require_consent': bool(request.form.get('require_consent')),
        'whole_org': bool(request.form.get('whole_org')),
    })
    audience = MarketingAudience(
        organization_id=org.id,
        user_id=current_user.id,
        name=request.form.get('audience_name') or None,
        filter=filt.to_dict(),
        is_saved=bool(request.form.get('save_audience')),
    )
    db.session.add(audience)
    db.session.flush()

    kind = 'drip' if request.form.get('kind') == 'drip' else 'one_time'
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
        send_hour_local=int(request.form.get('send_hour') or 9),
    ))
    if kind == 'drip':
        extra_ids = request.form.getlist('drip_template_id')
        extra_delays = request.form.getlist('drip_delay_days')
        extra_hours = request.form.getlist('drip_send_hour')
        for index, raw_id in enumerate(extra_ids, start=1):
            if not raw_id:
                continue
            extra = template_or_404(int(raw_id))
            delay = int(extra_delays[index - 1] or 3) if index - 1 < len(extra_delays) else 3
            hour = int(extra_hours[index - 1] or 9) if index - 1 < len(extra_hours) else 9
            db.session.add(MarketingCampaignStep(
                organization_id=org.id,
                campaign_id=campaign.id,
                template_id=extra.id,
                step_index=index,
                name=f'Email {index + 1}',
                delay_days=max(delay, 1),
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
    saved = [t for t in templates if t.source != 'system']
    return render_template(
        'marketing/library.html',
        starters=_starter_cards(org, templates),
        saved=saved,
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
            )
            flash('Template saved.', 'success')
            return redirect(url_for('marketing.studio', template_id=saved.id))
        except (TemplateError, json.JSONDecodeError, ValueError) as exc:
            flash(str(exc), 'error')
            if action == 'generate' and template is None:
                return redirect(url_for('marketing.library'))
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
    return redirect(url_for('marketing.library'))


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
