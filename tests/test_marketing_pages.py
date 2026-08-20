"""Marketing HTTP surface: flag gate and a happy-path overview."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import login
from models import db

from marketing_helpers import enable_campaigns, load_org_user, make_contact, ready_template
from services.marketing import system_templates as st


class TestMarketingPages:
    def test_overview_redirects_when_the_flag_is_off(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            flags = dict(org.feature_flags or {})
            flags['EMAIL_CAMPAIGNS'] = False
            org.feature_flags = flags
            db.session.commit()
        resp = owner_a_client.get('/marketing/overview', follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_overview_renders_when_the_flag_is_on(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.get('/marketing/overview', follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Campaigns' in body or 'campaign' in body.lower()

    def test_library_shows_starters_and_composer(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.get('/marketing/library', follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Pick a template' in body
        assert 'Create Template' in body
        assert 'Describe the email' in body
        assert 'Pick a template or write a new one.' in body
        assert 'Saved templates' in body
        assert 'My templates' in body
        assert 'Just checking in' in body
        assert 'data-controller="marketing-cover"' in body
        assert 'color-scheme' in body
        assert 'sandbox="allow-same-origin"' in body
        assert 'mkt-cover__viewport' in body

    def test_wizard_is_a_campaign_studio_with_preview(self, owner_a_client, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            ready_template(org, owner, name='Studio preview')
            db.session.commit()
        resp = owner_a_client.get('/marketing/campaigns/new', follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Name and template' in body
        assert 'Who gets it' in body
        assert 'Review and launch' in body
        assert 'My templates' in body
        assert 'Org templates' in body
        assert 'Search contacts' in body
        assert 'step_wait' in body
        assert 'send_hour' in body
        assert 'name="timezone"' in body
        assert 'name="from_name"' in body
        assert 'name="reply_to"' in body
        assert 'name="owners"' in body
        assert 'name="scheduled_at"' in body
        assert 'Pick a template, pick who gets it, then send.' in body
        assert 'mkt-campaign' in body
        assert 'mkt-preview' in body
        assert 'mkt-pick' in body
        assert 'data-controller="marketing-cover"' in body
        assert 'bg-blue-600' not in body

    def test_sendgrid_admin_is_not_the_marketing_ui(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.get('/marketing/templates', follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert '/marketing/library' in (resp.headers.get('Location') or '')
        landing = owner_a_client.get('/marketing/library', follow_redirects=True)
        body = landing.get_data(as_text=True)
        assert 'SendGrid Templates' not in body
        assert 'bg-blue-600' not in body

    def test_empty_studio_is_create_template(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.get('/marketing/studio', follow_redirects=False)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Save template' in body
        assert 'Active' in body
        assert 'Rewrite this email' in body
        assert 'Making the email' in body

    def test_start_from_template_opens_a_copy(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        listing = owner_a_client.get('/marketing/library', follow_redirects=True)
        assert listing.status_code == 200
        from models import MarketingTemplate
        with app.app_context():
            starter = MarketingTemplate.query.filter_by(source='system').first()
            assert starter is not None
            starter_id = starter.id
        resp = owner_a_client.get(f'/marketing/studio?from={starter_id}', follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Click any line in the email to edit it' in body
        assert 'data-mkt-edit' in body
        assert 'Sample data' in body
        assert 'Show sample data' in body
        assert 'data-marketing-template-studio-target="sampleToggle"' in body
        assert 'data-marketing-template-studio-target="sample"' in body
        assert 'data-key="contact.first_name"' in body
        assert 'mkt-sample-row is-used' in body
        assert 'placeholder="John"' in body
        assert 'value="John"' not in body
        assert 'Send test email' in body
        assert 'owner_a@test.com' in body
        assert f'/marketing/studio/{starter_id}' not in resp.request.path

    def test_send_test_posts_to_listed_addresses(self, owner_a_client, app, seed, monkeypatch):
        captured = []

        def fake_provider(**kwargs):
            captured.append(kwargs)
            return 'sg-test'

        monkeypatch.setattr(
            'services.marketing.send._provider_send', fake_provider,
        )
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.post(
            '/marketing/api/send-test',
            json={
                'subject': 'Checking in, {{contact.first_name|there}}',
                'preheader': 'Just a note',
                'blocks': [
                    {'type': 'paragraph', 'text': 'Hi {{contact.first_name|there}}.'},
                    {'type': 'signature'},
                ],
                'samples': {'contact.first_name': 'Chris'},
                'to': 'owner_a@test.com, extra@test.com',
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        payload = resp.get_json()
        assert payload['sent'] == ['owner_a@test.com', 'extra@test.com']
        assert payload['subject'].startswith('[Test] ')
        assert 'Chris' in payload['subject']
        assert len(captured) == 2

    def test_save_rejects_a_button_without_a_real_url(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        spec = st.definition('just_listed')
        blocks = []
        for block in spec['blocks']:
            item = dict(block)
            if item.get('url'):
                item['url'] = '[https://link-to-the-listing]'
            blocks.append(item)
        resp = owner_a_client.post(
            '/marketing/studio',
            data={
                'action': 'save',
                'name': 'Just listed copy',
                'subject': spec['subject'],
                'preheader': spec['preheader'],
                'blocks': json.dumps(blocks),
                'category': spec['category'],
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'See all the photos' in body
        assert 'before saving' in body
        assert 'Template saved.' not in body

    def test_save_accepts_a_button_with_a_real_url(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        spec = st.definition('just_listed')
        blocks = []
        for block in spec['blocks']:
            item = dict(block)
            if item.get('url'):
                item['url'] = 'https://example.com/listing'
            blocks.append(item)
        resp = owner_a_client.post(
            '/marketing/studio',
            data={
                'action': 'save',
                'name': 'Just listed copy',
                'subject': spec['subject'],
                'preheader': spec['preheader'],
                'blocks': json.dumps(blocks),
                'category': spec['category'],
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Template saved.' in body

    def test_wizard_lists_only_active_saved_templates(self, owner_a_client, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            saved = ready_template(org, owner, name='June check-in')
            db.session.commit()
            saved_id = saved.id
        listing = owner_a_client.get('/marketing/library', follow_redirects=True)
        assert listing.status_code == 200
        lib = listing.get_data(as_text=True)
        assert 'Just checking in' in lib
        resp = owner_a_client.get('/marketing/campaigns/new', follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'June check-in' in body
        assert f'data-template-id="{saved_id}"' in body
        assert 'Just checking in' not in body

    def test_campaign_post_keeps_the_form_on_error(self, owner_a_client, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            template = ready_template(org, owner, name='Keep me')
            db.session.commit()
            template_id = template.id
        resp = owner_a_client.post(
            '/marketing/campaigns/new',
            data={
                'name': 'Broken send',
                'template_id': str(template_id),
                'action': 'save',
                'kind': 'one_time',
                'send_hour': '10',
                'timezone': 'America/Chicago',
            },
            follow_redirects=False,
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Pick people or a filter' in body
        assert 'Broken send' in body
        assert f'value="{template_id}"' in body
        assert 'selected' in body

    def test_campaign_saves_week_and_month_steps(self, owner_a_client, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            first = ready_template(org, owner, name='Email one')
            second = ready_template(org, owner, name='Email two')
            third = ready_template(org, owner, name='Email three')
            jane = make_contact(
                org, owner, first='Jane', last='Kept',
                email='jane-kept@example.com',
            )
            db.session.commit()
            ids = (first.id, second.id, third.id, jane.id)
        resp = owner_a_client.post(
            '/marketing/campaigns/new',
            data={
                'name': 'Flexible drip',
                'template_id': str(ids[0]),
                'step_template_id': [str(ids[1]), str(ids[2])],
                'step_wait': ['week', 'month'],
                'contact_id': [str(ids[3])],
                'kind': 'drip',
                'send_hour': '11',
                'timezone': 'America/Denver',
                'from_name': 'Jane Agent',
                'reply_to': 'jane-agent@example.com',
                'action': 'save',
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        from models import MarketingCampaign, MarketingCampaignStep
        with app.app_context():
            campaign = MarketingCampaign.query.filter_by(name='Flexible drip').one()
            steps = (
                MarketingCampaignStep.query
                .filter_by(campaign_id=campaign.id)
                .order_by(MarketingCampaignStep.step_index)
                .all()
            )
            assert campaign.kind == 'drip'
            assert campaign.timezone == 'America/Denver'
            assert campaign.from_name == 'Jane Agent'
            assert campaign.reply_to == 'jane-agent@example.com'
            assert [step.delay_days for step in steps] == [0, 7, 37]
            assert [step.send_hour_local for step in steps] == [11, 11, 11]
            assert campaign.audience.filter['contact_ids'] == [ids[3]]

    def test_contact_search_returns_owned_contacts(self, owner_a_client, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.get('/marketing/api/contacts?q=Jane')
        assert resp.status_code == 200
        rows = resp.get_json()
        assert any('Jane' in (row.get('name') or '') for row in rows)

    def test_drips_tab_shows_scheduled_and_paused(self, owner_a_client, app, seed):
        from models import MarketingAudience, MarketingCampaign
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            audience = MarketingAudience(
                organization_id=org.id,
                user_id=owner.id,
                name='Drip audience',
                filter={'owners': [owner.id]},
                is_saved=False,
            )
            db.session.add(audience)
            db.session.flush()
            rows = (
                ('Scheduled drip', 'drip', 'scheduled'),
                ('Paused drip', 'drip', 'paused'),
                ('Live drip', 'drip', 'active'),
                ('Draft drip', 'drip', 'draft'),
                ('One-time blast', 'one_time', 'sending'),
            )
            for name, kind, status in rows:
                db.session.add(MarketingCampaign(
                    organization_id=org.id,
                    user_id=owner.id,
                    name=name,
                    kind=kind,
                    status=status,
                    audience_id=audience.id,
                    timezone='America/Chicago',
                    created_via='web',
                ))
            db.session.commit()
        resp = owner_a_client.get('/marketing/campaigns?status=active', follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Scheduled drip' in body
        assert 'Paused drip' in body
        assert 'Live drip' in body
        assert 'Draft drip' not in body
        assert 'One-time blast' not in body
