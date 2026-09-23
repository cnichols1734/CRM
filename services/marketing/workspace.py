"""Editable campaign drafts and local-time scheduling for the marketing workspace."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from werkzeug.datastructures import MultiDict

from models import MarketingAudience, MarketingCampaign, MarketingCampaignStep, db
from services.marketing import audience as aud, templates as tpl


def local_schedule(value, zone, *, now=None):
    if not value:
        return None
    try:
        tz = ZoneInfo(zone)
        local = datetime.fromisoformat(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError('Choose a valid date, time and time zone.')
    if local.tzinfo is not None:
        raise ValueError('Enter the time in the selected time zone.')
    aware = local.replace(tzinfo=tz)
    utc = aware.astimezone(timezone.utc)
    if utc.astimezone(tz).replace(tzinfo=None) != local:
        raise ValueError('That time does not exist when the clocks change. Choose another time.')
    if local.replace(tzinfo=tz, fold=1).utcoffset() != aware.utcoffset():
        raise ValueError('That time occurs twice when the clocks change. Choose a different hour.')
    result = utc.replace(tzinfo=None)
    if result <= (now or datetime.utcnow()):
        raise ValueError('Choose a send time in the future, or select Send now.')
    return result


def steps_for(campaign):
    return MarketingCampaignStep.query.filter_by(campaign_id=campaign.id).order_by(
        MarketingCampaignStep.step_index.asc(),
    ).all()


def form_for(campaign):
    form = MultiDict()
    for key in ('name', 'timezone', 'from_name', 'reply_to'):
        form[key] = getattr(campaign, key) or ''
    if campaign.scheduled_at:
        form['scheduled_at'] = campaign.scheduled_at.replace(tzinfo=timezone.utc).astimezone(
            ZoneInfo(campaign.timezone)).strftime('%Y-%m-%dT%H:%M')
    filt = (campaign.audience.filter if campaign.audience else {}) or {}
    for key in ('groups', 'owners', 'contact_ids'):
        form.setlist('contact_id' if key == 'contact_ids' else key, [str(v) for v in filt.get(key, [])])
    for key in ('zips', 'cities', 'states'):
        form[key] = ', '.join(filt.get(key, []))
    for key in ('whole_org', 'require_consent'):
        if filt.get(key):
            form[key] = '1'
    steps = steps_for(campaign)
    if steps:
        form['template_id'] = str(steps[0].template_id)
        form['send_hour'] = str(steps[0].send_hour_local)
        form.setlist('step_template_id', [str(s.template_id) for s in steps[1:]])
        form.setlist('step_wait', [str(s.delay_days - steps[i].delay_days) for i, s in enumerate(steps[1:])])
    return form


def save_form(org, user, form, campaign=None):
    """Save an incomplete draft. Launch performs the sendability checks."""
    if campaign is not None and (campaign.user_id != user.id or not campaign.is_editable):
        raise ValueError('Only your unsent drafts can be edited.')
    template_id = int(form.get('template_id') or 0)
    template = tpl.get_visible(org.id, user.id, template_id) if template_id else None
    campaign = campaign or MarketingCampaign(organization_id=org.id, user_id=user.id, status='draft', created_via='web')
    campaign.name = (form.get('name') or (template.name if template else 'Untitled email')).strip()[:200] or 'Untitled email'
    campaign.timezone = form.get('timezone') or 'America/Chicago'
    try:
        ZoneInfo(campaign.timezone)
    except ZoneInfoNotFoundError:
        raise ValueError('Choose a valid time zone.')
    campaign.scheduled_at = local_schedule(form.get('scheduled_at'), campaign.timezone)
    campaign.from_name = (form.get('from_name') or '').strip()[:200] or None
    campaign.reply_to = (form.get('reply_to') or '').strip()[:200] or None
    filt = aud.parse_filter({
        'groups': form.getlist('groups'), 'owners': form.getlist('owners'),
        'contact_ids': form.getlist('contact_id'),
        **{key: [v.strip() for v in (form.get(key) or '').split(',') if v.strip()] for key in ('zips', 'cities', 'states')},
        'whole_org': bool(form.get('whole_org')), 'require_consent': bool(form.get('require_consent')),
    })
    # Resolve authorization before persisting the selection, even for a draft.
    aud.matching_contacts(org.id, filt, user)
    audience = campaign.audience
    if audience is None or audience.is_saved:
        audience = MarketingAudience(organization_id=org.id, user_id=user.id, is_saved=False)
        db.session.add(audience)
    audience.filter = filt.to_dict()
    campaign.audience = audience
    db.session.add(campaign)
    db.session.flush()
    ids = ([template.id] if template else []) + [int(v) for v in form.getlist('step_template_id') if v]
    if len(ids) > 10:
        raise ValueError('Use up to ten emails in a follow-up sequence.')
    waits = form.getlist('step_wait')
    hour = int(form.get('send_hour') or 9)
    if not 0 <= hour <= 23:
        raise ValueError('Choose a follow-up time between midnight and 11 PM.')
    existing = steps_for(campaign)
    delay = 0
    for index, tid in enumerate(ids):
        email = tpl.get_visible(org.id, user.id, tid)
        shared_elsewhere = MarketingCampaignStep.query.filter(
            MarketingCampaignStep.template_id == email.id,
            MarketingCampaignStep.campaign_id != campaign.id,
        ).first()
        if email.source != 'campaign' or shared_elsewhere:
            email = tpl.save(organization_id=org.id, user_id=user.id, org=org, agent=user,
                name=email.name, subject=email.subject, preheader=email.preheader, blocks=email.blocks,
                category=email.category, source='campaign', visibility='private',
                acknowledge_warnings=bool(email.compliance_ack_at), active=email.status == 'ready', commit=False)
        if index:
            raw = waits[index - 1] if index - 1 < len(waits) else '7'
            wait = {'week': 7, 'month': 30}.get(raw)
            wait = wait if wait is not None else int(raw)
            if not 1 <= wait <= 365:
                raise ValueError('Wait between 1 and 365 days between emails.')
            delay += wait
        step = existing[index] if index < len(existing) else MarketingCampaignStep(
            organization_id=org.id, campaign_id=campaign.id, step_index=index)
        step.template_id = email.id
        step.name = f'Email {index + 1}'
        step.delay_days = delay
        step.send_hour_local = hour
        db.session.add(step)
    for step in existing[len(ids):]:
        db.session.delete(step)
    campaign.kind = 'drip' if len(ids) > 1 else 'one_time'
    campaign.updated_at = datetime.utcnow()
    db.session.flush()
    return campaign
