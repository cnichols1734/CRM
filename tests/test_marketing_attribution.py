"""Webhook attribution and the bounce circuit breaker."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from models import MarketingSend, db
from services.marketing import attribution
from services.marketing import launch as launchmod
from services.marketing.suppression import is_suppressed

from marketing_helpers import enable_campaigns, load_org_user, ready_template
from test_marketing_launch import _draft


def _queued_send(app, seed, name='Webhook'):
    org, owner = load_org_user(seed)
    enable_campaigns(org)
    template = ready_template(org, owner, name=name)
    campaign = _draft(org, owner, template)
    launchmod.launch(campaign, org, owner)
    send = MarketingSend.query.filter_by(
        campaign_id=campaign.id, status='queued',
    ).first()
    send.status = 'sent'
    campaign.queued_count = max((campaign.queued_count or 1) - 1, 0)
    campaign.sent_count = (campaign.sent_count or 0) + 1
    db.session.flush()
    return campaign, send


class TestAttribution:
    def test_delivered_increments_once(self, app, seed):
        with app.app_context():
            campaign, send = _queued_send(app, seed, name='Delivered once')
            event = {
                'event': 'delivered',
                'send_id': send.id,
                'kind': 'marketing',
                'organization_id': campaign.organization_id,
            }
            assert attribution.apply_event(event) is True
            assert send.status == 'delivered'
            assert campaign.delivered_count == 1
            assert attribution.apply_event(event) is True
            assert campaign.delivered_count == 1

    def test_bounce_suppresses_and_counts(self, app, seed):
        with app.app_context():
            campaign, send = _queued_send(app, seed, name='Bounce me')
            event = {
                'event': 'bounce',
                'send_id': send.id,
                'kind': 'marketing',
                'reason': '500 unknown user',
                'organization_id': campaign.organization_id,
            }
            assert attribution.apply_event(event) is True
            assert send.status == 'bounced'
            assert campaign.bounced_count == 1
            assert is_suppressed(send.to_email, campaign.organization_id)
            attribution.apply_event(event)
            assert campaign.bounced_count == 1

    def test_opens_are_recorded_not_counted(self, app, seed):
        with app.app_context():
            campaign, send = _queued_send(app, seed, name='Open me')
            before = campaign.delivered_count
            attribution.apply_event({
                'event': 'open',
                'send_id': send.id,
                'kind': 'marketing',
            })
            assert send.opened_at is not None
            assert campaign.delivered_count == before

    def test_circuit_breaker_pauses_a_hot_campaign(self, app, seed, monkeypatch):
        with app.app_context():
            monkeypatch.setattr(Config, 'MARKETING_BOUNCE_PAUSE_MIN', 2)
            monkeypatch.setattr(Config, 'MARKETING_BOUNCE_PAUSE_RATE', 0.5)
            campaign, send = _queued_send(app, seed, name='Breaker')
            campaign.status = 'sending'
            campaign.delivered_count = 1
            campaign.bounced_count = 0
            attribution.apply_event({
                'event': 'bounce',
                'send_id': send.id,
                'kind': 'marketing',
                'reason': 'bounce',
            })
            assert campaign.status == 'paused'
            assert campaign.auto_paused_reason
