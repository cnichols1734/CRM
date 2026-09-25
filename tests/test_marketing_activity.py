"""Campaign progress, future recipients, deletion and personal contact boundaries."""
from datetime import datetime, timedelta

import pytest

from marketing_helpers import enable_campaigns, load_org_user, ready_template
from models import Contact, MarketingCampaign, MarketingCampaignStep, MarketingEnrollment, MarketingSend, MarketingTemplate, db
from services.marketing import activity, drip, launch, send
from test_marketing_launch import _draft

pytestmark = pytest.mark.usefixtures('marketing_gmail')


def sequence(org, user, contact_id):
    enable_campaigns(org)
    template = ready_template(org, user, 'First check-in')
    campaign = _draft(org, user, template, {'contact_ids': [contact_id]}, kind='drip')
    followup = ready_template(org, user, 'Next day update')
    followup.subject = 'An update for {{contact.first_name|you}}'
    step = MarketingCampaignStep(organization_id=org.id, campaign_id=campaign.id,
        template_id=followup.id, step_index=1, delay_days=1, send_hour_local=9)
    db.session.add(step)
    db.session.flush()
    launch.launch(campaign, org, user)
    row = MarketingSend.query.filter_by(campaign_id=campaign.id).one()
    row.status = 'sent'; row.sent_at = datetime.utcnow(); row.subject_rendered = 'First check-in'
    campaign.sent_count = 1; campaign.queued_count = 0
    launch.maybe_complete(campaign)
    db.session.commit()
    return campaign, step


def test_followup_is_visible_before_worker_queues_it(app, seed, owner_a_client):
    with app.app_context():
        org, user = load_org_user(seed)
        campaign, step = sequence(org, user, seed['contact_a'])
        cid, sid = campaign.id, step.id
        assert campaign.status == 'active'
        info = activity.snapshot(campaign)
        assert info['status_label'] == 'Waiting for follow-up'
        assert info['pending'] == 1
        assert info['cards'][0]['status'] == 'Sent'
        assert info['next_card']['step'].id == sid
        people = activity.recipient_page(campaign, step, info['steps'], org)
        assert people['rows'][0]['status'] == 'Scheduled'
        assert people['rows'][0]['when']
        assert '{{' not in people['rows'][0]['subject']
        revision = info['revision']
        campaign.status = 'paused'
        assert activity.snapshot(campaign)['revision'] != revision
        assert activity.snapshot(campaign)['next_card'] is None
        campaign.status = 'active'; db.session.commit()
    body = owner_a_client.get(f'/marketing/campaigns/{cid}').get_data(as_text=True)
    assert 'Waiting for follow-up' in body
    assert 'Recipients for Next day update' in body
    assert 'Preview for' in body
    assert owner_a_client.get(f'/marketing/campaigns/{cid}/steps/{sid}/preview?contact={seed["contact_a"]}').status_code == 200
    assert owner_a_client.get(f'/marketing/campaigns/{cid}/steps/{sid}/preview?contact={seed["contact_a2"]}').status_code == 404
    assert b'First check-in' in owner_a_client.get('/marketing/sent').data
    assert b'Next day update' in owner_a_client.get('/marketing/campaigns').data


def test_ownership_is_rechecked_before_queueing_and_delivery(app, seed, monkeypatch):
    with app.app_context():
        org, user = load_org_user(seed)
        campaign, step = sequence(org, user, seed['contact_a'])
        enrollment = MarketingEnrollment.query.filter_by(campaign_id=campaign.id).one()
        row = MarketingSend.query.filter_by(campaign_id=campaign.id).one()
        row.status = 'queued'; campaign.queued_count = 1
        contact = db.session.get(Contact, seed['contact_a'])
        contact.user_id = seed['agent_a']
        monkeypatch.setattr(send, '_provider_send', lambda **kw: pytest.fail('Must not send another agent’s contact'))
        send.deliver(row)
        assert row.status == 'skipped' and row.skip_reason == 'contact_not_owned'
        assert not drip.advance_one(enrollment, now=datetime.utcnow() + timedelta(days=2))
        assert enrollment.status == 'stopped'
        assert enrollment.stop_reason == 'contact_not_owned'
        assert activity.recipient_page(campaign, step, activity.snapshot(campaign)['steps'], org)['total'] == 0
        db.session.rollback()


def test_delete_only_own_unsent_drafts(app, seed, owner_a_client, agent_a_client):
    with app.app_context():
        org, user = load_org_user(seed); enable_campaigns(org)
        campaign = _draft(org, user, ready_template(org, user, 'Delete draft'))
        cid = campaign.id; db.session.commit()
    assert agent_a_client.post(f'/marketing/campaigns/{cid}/delete').status_code == 403
    assert owner_a_client.get(f'/marketing/campaigns/{cid}/delete').status_code == 405
    assert owner_a_client.post(f'/marketing/campaigns/{cid}/delete').status_code == 302
    with app.app_context():
        assert db.session.get(MarketingCampaign, cid) is None
        org, user = load_org_user(seed)
        campaign, _ = sequence(org, user, seed['contact_a']); cid = campaign.id
    assert owner_a_client.post(f'/marketing/campaigns/{cid}/delete').status_code == 403


def test_delete_template_keeps_campaign_copy(app, seed, owner_a_client, agent_a_client):
    with app.app_context():
        org, user = load_org_user(seed); enable_campaigns(org)
        template = ready_template(org, user, 'Remove saved template')
        template.visibility = 'org'; tid = template.id
        campaign = _draft(org, user, template); cid = campaign.id
        db.session.commit()
    assert agent_a_client.post(f'/marketing/templates/{tid}/delete').status_code == 403
    assert owner_a_client.post(f'/marketing/templates/{tid}/delete').status_code == 302
    with app.app_context():
        assert db.session.get(MarketingTemplate, tid).status == 'archived'
        assert db.session.get(MarketingCampaign, cid).steps.first().template is not None
    assert b'Remove saved template' not in owner_a_client.get('/marketing/library?flow=template').data


def test_admin_cannot_open_other_agents_campaign(app, seed, owner_a_client):
    with app.app_context():
        org, user = load_org_user(seed, user_key='agent_a'); enable_campaigns(org)
        campaign = _draft(org, user, ready_template(org, user, 'Other agent campaign'))
        cid = campaign.id; db.session.commit()
    assert owner_a_client.get(f'/marketing/campaigns/{cid}').status_code == 403


def test_admin_recipient_search_and_preview_only_use_owned_contacts(app, seed, owner_a_client):
    with app.app_context():
        org, user = load_org_user(seed); enable_campaigns(org)
        template = ready_template(org, user, 'Private recipient preview'); tid = template.id
        db.session.commit()
    result = owner_a_client.get('/marketing/api/contacts').get_json()
    assert seed['contact_a'] in {row['id'] for row in result}
    assert seed['contact_a2'] not in {row['id'] for row in result}
    assert seed['contact_b'] not in {row['id'] for row in result}
    assert owner_a_client.post('/marketing/api/preview-as', json={
        'template_id': tid, 'contact_id': seed['contact_a2'],
    }).status_code == 404
    result = owner_a_client.post('/marketing/api/estimate', json={
        'contact_ids': [seed['contact_a2']],
    }).get_json()
    assert result['sendable'] == 0


@pytest.mark.parametrize('state', ['completed', 'cancelled'])
def test_finished_campaign_keeps_emails_grouped_and_correct_navigation(app, seed, owner_a_client, state):
    import re
    from html import unescape
    with app.app_context():
        org, user = load_org_user(seed)
        campaign, step = sequence(org, user, seed['contact_a'])
        campaign.name = f'History grouping {state}'
        campaign.status = state
        cid, sid = campaign.id, step.id
        db.session.commit()
    page = owner_a_client.get('/marketing/campaigns?status=completed')
    body = unescape(page.get_data(as_text=True))
    entry = next(row for row in re.findall(r'<article class="mkt-campaign-entry">(.*?)</article>', body, re.S) if f'History grouping {state}' in row)
    emails = re.findall(r'<li>(.*?)</li>', entry, re.S)
    assert len(emails) == 2
    assert 'First check-in' in emails[0]
    assert 'Next day update' in emails[1]
    assert f'/marketing/campaigns/{cid}?step={sid}#recipients' in emails[1]
    assert 'aria-current="page">Finished campaigns</a>' in body
    active = owner_a_client.get('/marketing/campaigns').get_data(as_text=True)
    assert f'History grouping {state}' not in active
    detail = owner_a_client.get(f'/marketing/campaigns/{cid}').get_data(as_text=True)
    assert 'status=completed" class="crm-back' in detail
    assert 'aria-current="page">Finished campaigns</a>' in detail


def test_campaign_search_is_literal_and_private(app, seed, owner_a_client):
    import re
    from html import unescape
    with app.app_context():
        org, user = load_org_user(seed)
        enable_campaigns(org)
        for name in ['Polish 100% sent', 'Polish 1000 sent']:
            campaign = _draft(org, user, ready_template(org, user))
            campaign.name, campaign.status = name, 'completed'
        _, other = load_org_user(seed, user_key='agent_a')
        campaign = _draft(org, other, ready_template(org, other))
        campaign.name, campaign.status = 'Private 100% sent', 'completed'
        db.session.commit()
    page = owner_a_client.get('/marketing/campaigns', query_string={'status':'completed', 'q':'100%'})
    body = page.get_data(as_text=True)
    entries = re.findall(r'<article class="mkt-campaign-entry">(.*?)</article>', body, re.S)
    assert len(entries) == 1 and 'Polish 100% sent' in entries[0]
    assert 'Private 100%' not in body
    page = owner_a_client.get('/marketing/campaigns?status=invalid')
    assert 'aria-current="page">Active campaigns</a>' in page.get_data(as_text=True)
