"""Marketing HTTP surface: flag gate and a happy-path overview."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import login
from models import db

from marketing_helpers import enable_campaigns, load_org_user
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
        assert 'Start from a template' in body
        assert 'Create your own' in body
        assert 'Describe the email' in body
        assert 'Write this email' in body
        assert 'Just checking in' in body
        assert 'data-controller="marketing-cover"' in body
        assert 'color-scheme' in body
        assert 'sandbox="allow-same-origin"' in body
        assert 'mkt-cover__viewport' in body

    def test_wizard_is_a_campaign_studio_with_preview(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.get('/marketing/campaigns/new', follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'Name and template' in body
        assert 'Who gets it' in body
        assert 'mkt-campaign' in body
        assert 'mkt-preview' in body
        assert 'mkt-pick' in body
        assert 'data-controller="marketing-cover"' in body
        assert 'bg-blue-600' not in body

    def test_sendgrid_admin_uses_crm_chrome(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.get('/marketing/templates', follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'bg-blue-600' not in body
        assert 'crm-btn' in body
        assert 'crm-surface' in body
        assert 'mkt-page' in body

    def test_empty_studio_redirects_to_library(self, owner_a_client, app, seed):
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()
        resp = owner_a_client.get('/marketing/studio', follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert '/marketing/library' in (resp.headers.get('Location') or '')

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
        resp = owner_a_client.post(
            '/marketing/studio',
            data={
                'action': 'save',
                'name': 'Just listed copy',
                'subject': spec['subject'],
                'preheader': spec['preheader'],
                'blocks': json.dumps(spec['blocks']),
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
