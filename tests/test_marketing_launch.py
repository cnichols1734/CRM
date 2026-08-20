"""Launch snapshot, drip advancement, and mocked delivery."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import (
    MarketingCampaign, MarketingCampaignStep, MarketingEnrollment,
    MarketingSend, db,
)
from services.marketing import drip
from services.marketing import launch as launchmod
from services.marketing import send as sendmod

from marketing_helpers import (
    enable_campaigns, load_org_user, make_contact, ready_template,
)


def _audience(org, user, filt=None):
    from models import MarketingAudience
    row = MarketingAudience(
        organization_id=org.id,
        user_id=user.id,
        name='Test audience',
        filter=filt or {},
        is_saved=False,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _draft(org, user, template, filt=None, kind='one_time'):
    audience = _audience(org, user, filt)
    campaign = MarketingCampaign(
        organization_id=org.id,
        user_id=user.id,
        name='Spring check-in',
        kind=kind,
        status='draft',
        audience_id=audience.id,
        timezone='America/Chicago',
        created_via='web',
    )
    db.session.add(campaign)
    db.session.flush()
    db.session.add(MarketingCampaignStep(
        organization_id=org.id,
        campaign_id=campaign.id,
        template_id=template.id,
        step_index=0,
        delay_days=0,
        send_hour_local=9,
    ))
    db.session.flush()
    return campaign


class TestLaunch:
    def test_queues_sendable_and_skips_the_rest(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            template = ready_template(org, owner)
            make_contact(
                org, owner, first='No', last='Mail', email=None,
            )
            campaign = _draft(org, owner, template)
            result = launchmod.launch(campaign, org, owner)
            assert result.sendable >= 1
            assert result.skipped >= 1
            assert campaign.status == 'sending'
            queued = MarketingSend.query.filter_by(
                campaign_id=campaign.id, status='queued',
            ).count()
            skipped = MarketingSend.query.filter_by(
                campaign_id=campaign.id, status='skipped',
            ).count()
            assert queued == result.sendable
            assert skipped == result.skipped
            assert MarketingEnrollment.query.filter_by(
                campaign_id=campaign.id,
            ).count() == result.sendable + result.skipped

    def test_refuses_without_broker_disclosure(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org, broker=False)
            org.broker_name = None
            org.broker_license_number = None
            org.broker_address = None
            template = ready_template(org, owner, name='No broker')
            campaign = _draft(org, owner, template)
            try:
                launchmod.launch(campaign, org, owner)
            except launchmod.LaunchError as exc:
                assert 'brokerage' in str(exc)
            else:
                raise AssertionError('expected LaunchError')
            assert campaign.status == 'draft'

    def test_refuses_unfilled_placeholders(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            template = ready_template(org, owner, name='Needs address')
            template.subject = 'Open house at [123 Main St]'
            db.session.flush()
            campaign = _draft(org, owner, template)
            try:
                launchmod.launch(campaign, org, owner)
            except launchmod.LaunchError as exc:
                assert 'unfilled' in str(exc).lower() or 'bracket' in str(exc).lower() or '[' in str(exc)
            else:
                raise AssertionError('expected LaunchError')

    def test_pause_resume_cancel(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            template = ready_template(org, owner, name='Pause me')
            campaign = _draft(org, owner, template)
            launchmod.launch(campaign, org, owner)
            launchmod.pause(campaign)
            assert campaign.status == 'paused'
            launchmod.resume(campaign)
            assert campaign.status in ('sending', 'active', 'completed')
            if campaign.status != 'completed':
                launchmod.cancel(campaign)
                assert campaign.status == 'cancelled'
                leftover = MarketingSend.query.filter_by(
                    campaign_id=campaign.id, status='queued',
                ).count()
                assert leftover == 0


class TestDrip:
    def test_first_step_queues_and_points_at_the_next(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            first = ready_template(org, owner, name='Drip one')
            second = ready_template(org, owner, name='Drip two')
            campaign = _draft(org, owner, first, kind='drip')
            db.session.add(MarketingCampaignStep(
                organization_id=org.id,
                campaign_id=campaign.id,
                template_id=second.id,
                step_index=1,
                delay_days=3,
                send_hour_local=9,
            ))
            db.session.flush()
            launchmod.launch(campaign, org, owner)
            assert campaign.kind == 'drip'
            assert campaign.status == 'active'
            enrollment = MarketingEnrollment.query.filter_by(
                campaign_id=campaign.id, status='active',
            ).first()
            assert enrollment is not None
            assert enrollment.current_step_index == 1
            assert enrollment.next_send_at is not None
            first_sends = MarketingSend.query.filter_by(
                campaign_id=campaign.id, status='queued',
            ).count()
            assert first_sends >= 1

    def test_advance_queues_the_next_step(self, app, seed):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            first = ready_template(org, owner, name='Advance one')
            second = ready_template(org, owner, name='Advance two')
            campaign = _draft(org, owner, first, kind='drip')
            db.session.add(MarketingCampaignStep(
                organization_id=org.id,
                campaign_id=campaign.id,
                template_id=second.id,
                step_index=1,
                delay_days=3,
                send_hour_local=9,
            ))
            db.session.flush()
            launchmod.launch(campaign, org, owner)
            enrollment = MarketingEnrollment.query.filter_by(
                campaign_id=campaign.id, status='active',
            ).first()
            enrollment.next_send_at = datetime.utcnow() - timedelta(minutes=1)
            queued_before = MarketingSend.query.filter_by(
                campaign_id=campaign.id, status='queued',
            ).count()
            assert drip.advance_one(enrollment) is True
            queued_after = MarketingSend.query.filter_by(
                campaign_id=campaign.id, status='queued',
            ).count()
            assert queued_after == queued_before + 1
            assert enrollment.status == 'completed'


class TestDeliver:
    def test_marks_sent_when_provider_accepts(self, app, seed, monkeypatch):
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            template = ready_template(org, owner, name='Send me')
            campaign = _draft(org, owner, template)
            launchmod.launch(campaign, org, owner)
            send = MarketingSend.query.filter_by(
                campaign_id=campaign.id, status='queued',
            ).first()
            monkeypatch.setattr(
                sendmod, '_provider_send',
                lambda **kwargs: 'sg-test-1',
            )
            sendmod.deliver(send)
            assert send.status == 'sent'
            assert send.provider_message_id == 'sg-test-1'
            assert campaign.sent_count >= 1


class TestSendTest:
    def test_parse_recipients_splits_commas_and_dedupes(self):
        emails = sendmod.parse_test_recipients(
            'Owner_A@test.com, extra@test.com, owner_a@test.com'
        )
        assert emails == ['owner_a@test.com', 'extra@test.com']

    def test_parse_recipients_rejects_junk(self):
        try:
            sendmod.parse_test_recipients('not-an-email')
        except sendmod.SendError as exc:
            assert 'not a valid email' in str(exc)
        else:
            raise AssertionError('expected SendError')

    def test_parse_recipients_caps_the_list(self):
        raw = ', '.join(f'a{i}@test.com' for i in range(6))
        try:
            sendmod.parse_test_recipients(raw)
        except sendmod.SendError as exc:
            assert 'at most' in str(exc)
        else:
            raise AssertionError('expected SendError')

    def test_send_test_uses_sample_data_and_prefixes_subject(
        self, app, seed, monkeypatch,
    ):
        captured = []

        def fake_provider(**kwargs):
            captured.append(kwargs)
            return 'sg-test'

        monkeypatch.setattr(sendmod, '_provider_send', fake_provider)
        with app.app_context():
            org, owner = load_org_user(seed)
            enable_campaigns(org)
            result = sendmod.send_test(
                org=org,
                agent=owner,
                subject='Checking in, {{contact.first_name|there}}',
                preheader='Just a note',
                blocks=[
                    {'type': 'paragraph', 'text': 'Hi {{contact.first_name|there}}.'},
                    {'type': 'signature'},
                ],
                to_emails=['owner_a@test.com', 'partner@test.com'],
                sample_values={'contact.first_name': 'Sarah'},
            )
        assert result['sent'] == ['owner_a@test.com', 'partner@test.com']
        assert result['subject'].startswith('[Test] ')
        assert 'Sarah' in result['subject']
        assert len(captured) == 2
        assert captured[0]['to_email'] == 'owner_a@test.com'
        assert captured[0]['subject'].startswith('[Test] ')
        assert 'Sarah' in captured[0]['html']
        assert captured[0]['custom_args']['kind'] == 'marketing_test'
