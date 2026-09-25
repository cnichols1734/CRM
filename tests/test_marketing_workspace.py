"""Marketing draft isolation, scheduling, recipient checks and private access."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from feature_flags import get_org_features, org_has_feature
from marketing_helpers import enable_campaigns, load_org_user, make_contact, ready_template
from models import MarketingCampaign, MarketingCampaignStep, MarketingTemplate, User, db
from services.marketing.workspace import local_schedule, form_for, steps_for

pytestmark = pytest.mark.usefixtures('marketing_gmail')


def email_form(**extra):
    return {'name':'My check-in', 'subject':'Hello there', 'flow':'campaign', 'action':'continue',
            'blocks':json.dumps([{'type':'paragraph','text':'Hi {{contact.first_name|there}}.'},{'type':'signature'}]), **extra}


def enable(app, seed):
    with app.app_context():
        org, user = load_org_user(seed)
        enable_campaigns(org)
        db.session.commit()


@pytest.mark.parametrize('tier,platform,allowed', [('free',False,False),('free',True,False),('pro',False,False),('enterprise',False,False),('enterprise',True,True)])
def test_private_rollout_ignores_tier_and_superadmin_overrides(app, monkeypatch, tier, platform, allowed):
    monkeypatch.setattr('feature_flags._current_user_is_super_admin', lambda:True)
    org=SimpleNamespace(subscription_tier=tier,is_platform_admin=platform,feature_flags={'EMAIL_CAMPAIGNS':True,'MARKETING':True})
    with app.test_request_context('/'):
        assert org_has_feature('EMAIL_CAMPAIGNS',org) is allowed
        assert org_has_feature('MARKETING',org) is allowed
        assert get_org_features(org)['EMAIL_CAMPAIGNS'] is allowed
        assert get_org_features(org)['MARKETING'] is allowed


def test_free_account_cannot_reach_marketing_even_with_override(owner_b_client, app, seed):
    with app.app_context():
        org,_=load_org_user(seed,'org_b','owner_b')
        org.subscription_tier='free';org.is_platform_admin=False
        org.feature_flags={'EMAIL_CAMPAIGNS':True,'MARKETING':True};db.session.commit()
    for path in ('overview','library','studio','campaigns/new','settings'):
        assert owner_b_client.get('/marketing/'+path).status_code in (302,403,404)
    for path in ('preview','send-test','estimate'):
        assert owner_b_client.post('/marketing/api/'+path,json={}).status_code in (302,403,404)
    body=owner_b_client.get('/dashboard').get_data(as_text=True)
    assert 'href="/marketing"' not in body


def test_draft_can_save_without_audience_and_resume(owner_a_client, app, seed):
    enable(app,seed)
    response=owner_a_client.post('/marketing/studio',data=email_form())
    assert response.status_code==302
    edit=response.headers['Location'];assert edit.endswith('/edit')
    body=owner_a_client.get(edit).get_data(as_text=True)
    assert 'Who should receive this?' in body and 'Review your campaign' in body
    with app.app_context():
        campaign=MarketingCampaign.query.filter_by(name='My check-in').order_by(MarketingCampaign.id.desc()).first()
        assert campaign.audience_id is None
        assert campaign.status=='draft'
        assert steps_for(campaign)[0].template.source=='campaign'
    saved=owner_a_client.post(edit,data={'name':'Renamed draft','action':'save'},follow_redirects=True)
    assert saved.status_code==200
    assert 'Draft saved.' in saved.get_data(as_text=True)


def test_empty_email_draft_can_be_saved(owner_a_client, app, seed):
    enable(app,seed)
    response=owner_a_client.post('/marketing/studio',data=email_form(subject='',action='save_draft',blocks=json.dumps([{'type':'paragraph','text':''}])) )
    assert response.status_code==302
    with app.app_context():
        campaign=MarketingCampaign.query.filter_by(name='My check-in').order_by(MarketingCampaign.id.desc()).first()
        template=steps_for(campaign)[0].template
        assert template.subject=='' and template.status=='draft'


def test_campaign_edit_does_not_modify_shared_template(owner_a_client, app, seed):
    enable(app,seed)
    with app.app_context():
        org,user=load_org_user(seed);template=ready_template(org,user,'Reusable');tid=template.id;db.session.commit()
    response=owner_a_client.post('/marketing/campaigns/new',data={'name':'Isolated','template_id':tid,'action':'save'})
    assert response.status_code==302
    with app.app_context():
        campaign=MarketingCampaign.query.filter_by(name='Isolated').one();cid=campaign.id
        assert steps_for(campaign)[0].template_id!=tid
    response=owner_a_client.post(f'/marketing/studio?campaign={cid}&step=0',data=email_form(subject='Only this campaign'))
    assert response.status_code==302
    with app.app_context():
        assert db.session.get(MarketingTemplate,tid).subject=='Checking in'
        assert steps_for(db.session.get(MarketingCampaign,cid))[0].template.subject=='Only this campaign'


def test_add_followup_preserves_recipients_and_edits_the_same_draft(owner_a_client, app, seed):
    enable(app,seed)
    owner_a_client.post('/marketing/studio',data=email_form())
    with app.app_context():
        org,user=load_org_user(seed);contact=make_contact(org,user,first='Pat',last='Client',email='pat@example.com')
        campaign=MarketingCampaign.query.filter_by(name='My check-in').order_by(MarketingCampaign.id.desc()).first();cid=campaign.id
        form=form_for(campaign);form.setlist('contact_id',[str(contact.id)]);form['action']='add_email';db.session.commit()
    added=owner_a_client.post(f'/marketing/campaigns/{cid}/edit',data=form)
    assert 'step=new' in added.headers['Location']
    saved=owner_a_client.post(f'/marketing/studio?campaign={cid}&step=new',data=email_form(name='Follow-up',subject='One more thing'))
    assert saved.status_code==302
    with app.app_context():
        campaign=db.session.get(MarketingCampaign,cid)
        assert campaign.kind=='drip' and campaign.audience.filter['contact_ids']
        assert len(steps_for(campaign))==2
        form=form_for(campaign);form.setlist('step_wait',['3']);form['action']='save'
    assert owner_a_client.post(f'/marketing/campaigns/{cid}/edit',data=form).status_code==302
    with app.app_context():
        assert [s.delay_days for s in steps_for(db.session.get(MarketingCampaign,cid))]==[0,3]
        assert MarketingCampaign.query.order_by(MarketingCampaign.id.desc()).first().id == cid


def test_cannot_edit_another_agents_draft_or_running_email(owner_a_client, agent_a_client, app, seed):
    enable(app,seed)
    owner_a_client.post('/marketing/studio',data=email_form())
    with app.app_context():
        campaign=MarketingCampaign.query.filter_by(name='My check-in').order_by(MarketingCampaign.id.desc()).first();cid=campaign.id;tid=steps_for(campaign)[0].template_id
    assert agent_a_client.get(f'/marketing/campaigns/{cid}/edit').status_code==403
    assert owner_a_client.get(f'/marketing/studio/{tid}?flow=template').status_code==403
    with app.app_context():
        db.session.get(MarketingCampaign,cid).status='scheduled';db.session.commit()
    assert owner_a_client.post(f'/marketing/studio?campaign={cid}',data=email_form()).status_code==403
    assert owner_a_client.get(f'/marketing/campaigns/{cid}/edit').status_code==403


def test_regular_agent_can_view_settings_but_cannot_change_brokerage(agent_a_client, app, seed):
    enable(app,seed)
    response=agent_a_client.get('/marketing/settings')
    assert response.status_code==200 and b'Your sending account' in response.data
    assert b'Email signature' in response.data
    assert b'License number' not in response.data
    assert b'Mailing address' not in response.data
    assert agent_a_client.post('/marketing/settings',data={'broker_name':'Changed'}).status_code==405


def test_estimate_lists_recipients_and_exclusions(owner_a_client, app, seed):
    enable(app,seed)
    with app.app_context():
        org,user=load_org_user(seed)
        first=make_contact(org,user,first='Pat',last='Ready',email='pat@example.com')
        other=make_contact(org,user,first='Sam',last='Missing',email='')
        ids=[first.id,other.id];db.session.commit()
    result=owner_a_client.post('/marketing/api/estimate',json={'contact_ids':ids}).get_json()
    assert result['sendable']==1 and result['excluded']==1
    assert {r['reason'] for r in result['recipients']}=={'Ready','no email'}


def test_schedule_converts_chicago_wall_time_to_utc():
    assert local_schedule('2030-09-23T09:00','America/Chicago',now=datetime(2030,9,1))==datetime(2030,9,23,14)


@pytest.mark.parametrize('value,zone', [('2030-03-10T02:30','America/Chicago'),('2030-11-03T01:30','America/Chicago'),('2030-09-23T09:00','Bad/Zone'),('2020-01-01T09:00','America/Chicago')])
def test_schedule_rejects_ambiguous_missing_or_past_times(value,zone):
    with pytest.raises(ValueError):local_schedule(value,zone,now=datetime(2030,1,1))


def test_form_round_trips_selected_timezone(owner_a_client, app, seed):
    enable(app,seed)
    owner_a_client.post('/marketing/studio',data=email_form())
    with app.app_context():
        campaign=MarketingCampaign.query.filter_by(name='My check-in').order_by(MarketingCampaign.id.desc()).first();cid=campaign.id
        form=form_for(campaign);form.update({'scheduled_at':'2030-09-23T09:00','timezone':'America/Chicago','action':'save'})
    assert owner_a_client.post(f'/marketing/campaigns/{cid}/edit',data=form).status_code==302
    with app.app_context():
        campaign=db.session.get(MarketingCampaign,cid)
        assert campaign.scheduled_at==datetime(2030,9,23,14)
        assert form_for(campaign)['scheduled_at']=='2030-09-23T09:00'


def test_template_copy_keeps_campaign_and_email_brand(owner_a_client, app, seed):
    enable(app, seed)
    response = owner_a_client.post('/marketing/studio', data=email_form(action='save_template', name='Reusable check-in'))
    assert response.status_code == 302
    with app.app_context():
        campaign = MarketingCampaign.query.filter_by(name='Reusable check-in').one()
        reusable = MarketingTemplate.query.filter_by(name='Reusable check-in', source='manual').one()
        assert steps_for(campaign)[0].template_id != reusable.id
        org, user = load_org_user(seed)
        from services.marketing.context import shell_for
        from services.marketing.render import preview
        org.broker_name = 'Origen Realty'
        _, html = preview(reusable.blocks, shell_for(org, user), reusable.subject)
        assert 'Origen Realty' in html
        assert 'AgentFlow' not in html


def test_draft_recovery_receipt_only_after_success(owner_a_client, app, seed):
    enable(app, seed)
    with app.app_context():
        org, user = load_org_user(seed)
        key = f'marketing:{org.id}:{user.id}:email:new:0'
    response = owner_a_client.post('/marketing/studio', data=email_form(_draft_recovery_key=key), follow_redirects=True)
    assert f'data-marketing-saved-draft="{key}"' in response.get_data(as_text=True)
    response = owner_a_client.post('/marketing/studio', data=email_form(_draft_recovery_key=key, subject=''), follow_redirects=True)
    assert 'data-marketing-saved-draft=' not in response.get_data(as_text=True)


def test_empty_paragraph_draft_reopens_with_editable_body(owner_a_client, app, seed):
    enable(app, seed)
    response = owner_a_client.post('/marketing/studio', data=email_form(subject='', action='save_draft', blocks=json.dumps([{'type':'paragraph','text':''}])), follow_redirects=True)
    assert response.status_code == 200
    assert 'data-mkt-edit' in response.get_data(as_text=True)


def test_later_followups_use_gap_from_previous_email():
    from services.marketing.launch import _advance_enrollment_pointer
    steps = [SimpleNamespace(step_index=i, delay_days=days, send_hour_local=9) for i, days in enumerate([0,7,14])]
    enrollment = SimpleNamespace(current_step_index=1)
    _advance_enrollment_pointer(enrollment, steps, datetime(2030,1,8,15), 'America/Chicago')
    assert enrollment.current_step_index == 2
    assert enrollment.next_send_at == datetime(2030,1,15,15)


def test_test_email_uses_reviewed_sender_details(owner_a_client, app, seed, monkeypatch):
    enable(app, seed)
    with app.app_context():
        org, user = load_org_user(seed)
        template = ready_template(org, user, 'Sender preview')
        tid = template.id
        db.session.commit()
    sent = []
    monkeypatch.setattr('services.marketing.send._provider_send', lambda **kwargs: sent.append(kwargs))
    response = owner_a_client.post('/marketing/api/send-test', json={'template_id':tid, 'from_name':'My display name', 'reply_to':'replies@example.com'})
    assert response.status_code == 200
    assert sent[0]['sender'].from_name == 'My display name'
    assert sent[0]['sender'].reply_to == 'replies@example.com'


def test_template_sections_have_previews_and_preserve_followup_context(owner_a_client, app, seed):
    import re
    from html import unescape
    enable(app, seed)
    with app.app_context():
        org, owner = load_org_user(seed)
        mine = ready_template(org, owner, 'My reusable email')
        _, colleague = load_org_user(seed, user_key='agent_a')
        shared = ready_template(org, colleague, 'Team reusable email')
        private = ready_template(org, colleague, 'Hidden colleague email')
        private.visibility = 'private'
        mine_id, shared_id = mine.id, shared.id
        db.session.commit()
    owner_a_client.post('/marketing/studio', data=email_form(name='Preview campaign'))
    with app.app_context():
        cid = MarketingCampaign.query.filter_by(name='Preview campaign').one().id
    page = owner_a_client.get(f'/marketing/library?flow=campaign&campaign={cid}&step=new')
    body = unescape(page.get_data(as_text=True))
    assert 'Add a follow-up email' in body
    assert f'/marketing/campaigns/{cid}/edit?panel=review' in body
    for section_id, name, tid in [('saved-templates', 'My reusable email', mine_id), ('shared-templates', 'Team reusable email', shared_id)]:
        section = re.search(r'<section[^>]*aria-labelledby="' + section_id + r'">(.*?)</section>', body, re.S).group(1)
        card = next(card for card in re.findall(r'<article class="mkt-template-card">(.*?)</article>', section, re.S) if name in card)
        assert '<iframe' in card and 'srcdoc="<!' in card
        href = re.search(r'<a class="mkt-cover" href="([^"]+)"', card).group(1)
        assert f'from={tid}' in href and f'campaign={cid}' in href and 'step=new' in href
    assert 'Hidden colleague email' not in body
    assert '<iframe' in re.search(r'<section[^>]*aria-labelledby="base-templates">(.*?)</section>', body, re.S).group(1)


def test_save_review_returns_to_review_and_launch_error_keeps_it(owner_a_client, app, seed):
    enable(app, seed)
    owner_a_client.post('/marketing/studio', data=email_form(name='Keep review open'))
    with app.app_context():
        campaign = MarketingCampaign.query.filter_by(name='Keep review open').one()
        cid = campaign.id
        form = form_for(campaign)
        form['panel'] = 'review'
        form['action'] = 'save'
    saved = owner_a_client.post(f'/marketing/campaigns/{cid}/edit', data=form)
    assert 'panel=review' in saved.headers['Location']
    form['action'] = 'launch'
    failed = owner_a_client.post(f'/marketing/campaigns/{cid}/edit', data=form)
    assert failed.status_code == 200
    assert 'data-initial-panel="3"' in failed.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(MarketingCampaign, cid).status == 'draft'
