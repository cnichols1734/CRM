"""Marketing MCP / B.O.B. tools: stage only, never launch."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import MarketingCampaign, MarketingTemplate, Notification, db
from services.bob_tools import BobContext, dispatch
from services.bob_tools.registry import (
    MARKETING_TOOL_NAMES, TOOLS_BY_NAME, select_tools,
)
from services.mcp.prompts import get_prompt, list_prompts

from marketing_helpers import enable_campaigns, load_org_user, ready_template


def _ctx(user, surface='mcp'):
    return BobContext.from_user(user, surface=surface)


class TestToolSurface:
    def test_there_is_no_launch_tool(self):
        assert 'launch_campaign' not in TOOLS_BY_NAME
        assert 'launch_campaign' not in MARKETING_TOOL_NAMES

    def test_hidden_when_the_flag_is_off(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            flags = dict(org.feature_flags or {})
            flags.pop('EMAIL_CAMPAIGNS', None)
            org.feature_flags = flags
            db.session.flush()
            names = {tool.name for tool in select_tools(_ctx(owner))}
            assert not (names & MARKETING_TOOL_NAMES)

    def test_present_when_the_flag_is_on(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            names = {tool.name for tool in select_tools(_ctx(owner))}
            assert MARKETING_TOOL_NAMES <= names


class TestHandlers:
    def test_list_and_get_templates(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            ctx = _ctx(owner)
            listed = dispatch('list_email_templates', {}, ctx)
            assert listed.ok
            assert listed.data['templates']
            template_id = listed.data['templates'][0]['id']
            got = dispatch('get_email_template', {'template_id': template_id}, ctx)
            assert got.ok
            assert got.data['blocks']
            assert got.record_url

    def test_create_template_from_blocks(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            result = dispatch('create_email_template', {
                'name': 'MCP check-in',
                'subject': 'Just saying hi',
                'preheader': 'No pitch',
                'blocks': [
                    {'type': 'paragraph', 'text': 'Hi {{contact.first_name|there}}.'},
                    {'type': 'signature'},
                ],
                'category': 'check_in',
            }, _ctx(owner))
            assert result.ok
            row = db.session.get(MarketingTemplate, result.data['id'])
            assert row is not None
            assert row.status == 'ready'
            assert row.source == 'manual'

    def test_create_and_stage_a_campaign(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            template = ready_template(org, owner, name='MCP campaign tpl')
            ctx = _ctx(owner)
            created = dispatch('create_campaign', {
                'name': 'MCP draft',
                'template_id': template.id,
                'groups': [seed['group_a1']],
            }, ctx)
            assert created.ok
            assert created.data['status'] == 'draft'
            assert created.data['launch_url']
            assert 'not been sent' in created.summary.lower() or 'has not been sent' in created.summary
            campaign_id = created.data['id']

            staged = dispatch('stage_campaign_for_review', {
                'campaign_id': campaign_id,
            }, ctx)
            assert staged.ok
            campaign = db.session.get(MarketingCampaign, campaign_id)
            assert campaign.status == 'pending_review'
            assert campaign.created_via == 'mcp'
            assert Notification.query.filter_by(
                user_id=owner.id, category='marketing',
            ).count() >= 1
            assert staged.record_url == f'/marketing/campaigns/{campaign_id}'

    def test_refuses_without_the_flag(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            flags = dict(org.feature_flags or {})
            flags['EMAIL_CAMPAIGNS'] = False
            org.feature_flags = flags
            db.session.flush()
            result = dispatch('list_email_templates', {}, _ctx(owner))
            assert not result.ok
            assert 'not enabled' in result.error

    def test_consent_and_suppression(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            ctx = _ctx(owner)
            suppressed = dispatch('add_marketing_suppression', {
                'email': 'leave-me-alone@example.com',
                'note': 'Asked in person',
            }, ctx)
            assert suppressed.ok
            consent = dispatch('set_contact_marketing_consent', {
                'contact_id': seed['contact_a'],
                'marketing_consent': 'opted_out',
            }, ctx)
            assert consent.ok
            from models import Contact
            contact = db.session.get(Contact, seed['contact_a'])
            assert contact.marketing_consent == 'opted_out'
            dispatch('set_contact_marketing_consent', {
                'contact_id': seed['contact_a'],
                'marketing_consent': 'unknown',
            }, ctx)


class TestPrompts:
    def test_prompts_are_registered(self):
        names = {item['name'] for item in list_prompts()}
        assert 'build_email_campaign' in names
        assert 'create_email_template' in names
        campaign = get_prompt('build_email_campaign', {'goal': 'open house'})
        assert 'Do not send' in campaign['messages'][0]['content']['text']
        assert 'no launch tool' in campaign['messages'][0]['content']['text'].lower()
