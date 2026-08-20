"""Sender identity, org readiness, and the monthly send quota."""
from datetime import datetime

import pytest

from models import (
    MarketingCampaign, MarketingCampaignStep, MarketingSend,
    MarketingTemplate, Organization, User, Contact, db,
)
from services.marketing import sending_config as sc
from services.marketing import suppression


class FakeOrg:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 1)
        self.name = kwargs.get('name', 'Origen Realty')
        self.broker_name = kwargs.get('broker_name', 'Origen Realty')
        self.broker_license_number = kwargs.get('broker_license_number', '9003104')
        self.broker_address = kwargs.get('broker_address', '1401 Common St')
        self.subscription_tier = kwargs.get('subscription_tier', 'pro')
        self.feature_flags = kwargs.get('feature_flags', {})
        self.is_platform_admin = kwargs.get('is_platform_admin', False)


class FakeAgent:
    def __init__(self, **kwargs):
        self.first_name = kwargs.get('first_name', 'Suzie')
        self.last_name = kwargs.get('last_name', 'Harrington')
        self.full_name = kwargs.get('full_name')
        self.email = kwargs.get('email', 'suzie@origenrealty.com')


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------

class TestSender:
    def test_sends_from_the_marketing_subdomain(self, app):
        # A campaign that draws complaints must not damage the domain that
        # carries password resets.
        sender = sc.sender_for(FakeAgent(), FakeOrg())
        assert sender.from_email == app.config['MARKETING_FROM_EMAIL']
        assert 'mail.' in sender.from_email

    def test_shows_the_agent_and_the_brokerage(self, app):
        sender = sc.sender_for(FakeAgent(), FakeOrg())
        assert sender.from_name == 'Suzie Harrington | Origen Realty'

    def test_replies_go_to_the_agent(self, app):
        # The from address is unmonitored; a reply has to reach a person.
        sender = sc.sender_for(FakeAgent(), FakeOrg())
        assert sender.reply_to == 'suzie@origenrealty.com'

    def test_explicit_reply_to_wins(self, app):
        sender = sc.sender_for(FakeAgent(), FakeOrg(), reply_to='team@x.com')
        assert sender.reply_to == 'team@x.com'

    def test_falls_back_to_the_org_name(self, app):
        org = FakeOrg(broker_name=None, name='Some Brokerage')
        sender = sc.sender_for(FakeAgent(first_name=None, last_name=None), org)
        assert sender.from_name == 'Some Brokerage'

    def test_survives_a_nameless_agent_and_org(self, app):
        org = FakeOrg(broker_name=None, name=None)
        sender = sc.sender_for(None, org)
        assert sender.from_name == app.config['MARKETING_FROM_NAME']


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

class TestReadiness:
    def test_complete_org_is_ready(self):
        assert sc.readiness_for(FakeOrg()).ok

    def test_missing_disclosure_blocks_sending(self):
        result = sc.readiness_for(FakeOrg(broker_license_number=None))
        assert not result.ok
        assert result.missing == ['brokerage license number']

    def test_message_names_one_missing_field(self):
        result = sc.readiness_for(FakeOrg(broker_address=None))
        assert result.message == (
            'Add your brokerage mailing address before sending marketing email.'
        )

    def test_message_lists_several(self):
        result = sc.readiness_for(
            FakeOrg(broker_name=None, broker_license_number=None)
        )
        assert result.message == (
            'Add your brokerage name and brokerage license number '
            'before sending marketing email.'
        )

    def test_ready_org_has_no_message(self):
        assert sc.readiness_for(FakeOrg()).message is None


# ---------------------------------------------------------------------------
# Quota limits
# ---------------------------------------------------------------------------

class TestMonthlyLimit:
    @pytest.mark.parametrize('tier,expected', [
        ('free', 0),
        ('pro', 2500),
        ('enterprise', 25000),
    ])
    def test_reads_the_tier_default(self, tier, expected):
        assert sc.monthly_limit(FakeOrg(subscription_tier=tier)) == expected

    def test_per_org_override_wins(self):
        org = FakeOrg(subscription_tier='pro', feature_flags={
            sc.QUOTA_OVERRIDE_KEY: 10000,
        })
        assert sc.monthly_limit(org) == 10000

    def test_override_can_zero_an_org_out(self):
        org = FakeOrg(subscription_tier='pro', feature_flags={
            sc.QUOTA_OVERRIDE_KEY: 0,
        })
        assert sc.monthly_limit(org) == 0

    def test_ignores_a_boolean_override(self):
        # The flags dict is mostly booleans; True must not read as a limit of 1.
        org = FakeOrg(subscription_tier='pro', feature_flags={
            sc.QUOTA_OVERRIDE_KEY: True,
        })
        assert sc.monthly_limit(org) == 2500

    def test_ignores_a_junk_override(self):
        org = FakeOrg(subscription_tier='pro', feature_flags={
            sc.QUOTA_OVERRIDE_KEY: 'lots',
        })
        assert sc.monthly_limit(org) == 2500

    def test_platform_admin_gets_the_top_limit(self):
        org = FakeOrg(subscription_tier='free', is_platform_admin=True)
        assert sc.monthly_limit(org) == 25000

    def test_unknown_tier_falls_back_to_free(self):
        assert sc.monthly_limit(FakeOrg(subscription_tier='legacy')) == 0


class TestQuotaArithmetic:
    def quota(self, limit, used):
        return sc.Quota(limit=limit, used=used, period_start=sc.period_start())

    def test_reports_what_is_left(self):
        assert self.quota(2500, 400).remaining == 2100

    def test_never_reports_negative_remaining(self):
        assert self.quota(100, 260).remaining == 0

    def test_allows_a_send_that_fits_exactly(self):
        assert self.quota(100, 40).allows(60)

    def test_refuses_one_over(self):
        assert not self.quota(100, 40).allows(61)

    def test_refusal_names_both_numbers(self):
        # "Over your limit" with no figures leaves the agent guessing how many
        # recipients to cut.
        message = self.quota(2500, 2400).refusal(312)
        assert '312' in message
        assert '100' in message

    def test_refusal_formats_thousands(self):
        assert '1,200' in self.quota(2500, 2400).refusal(1200)

    def test_no_plan_says_so_instead_of_showing_zero(self):
        assert self.quota(0, 0).refusal(10) == (
            'Marketing email is not included in your plan.'
        )

    def test_an_allowed_send_has_no_refusal(self):
        assert self.quota(2500, 0).refusal(312) is None

    def test_reports_how_far_over(self):
        assert self.quota(100, 90).shortfall(30) == 20

    def test_exhausted_when_nothing_remains(self):
        assert self.quota(100, 100).is_exhausted
        assert not self.quota(100, 99).is_exhausted


class TestPeriod:
    def test_starts_at_the_first_of_the_month(self):
        start = sc.period_start(datetime(2026, 8, 19, 14, 32, 5))
        assert start == datetime(2026, 8, 1, 0, 0, 0)


# ---------------------------------------------------------------------------
# Usage against real send rows
# ---------------------------------------------------------------------------

@pytest.fixture()
def usage(app, seed):
    """A campaign with one send row per status, so counting can be checked."""
    with app.app_context():
        org = Organization.query.filter_by(slug='test-realty-a').first()
        user = User.query.filter_by(organization_id=org.id).first()
        contact = Contact.query.filter_by(organization_id=org.id).first()
        MarketingSend.query.filter_by(organization_id=org.id).delete()
        db.session.flush()

        template = MarketingTemplate(
            organization_id=org.id, created_by_id=user.id, name='Quota probe',
            category='other', subject='Hi', blocks=[
                {'type': 'paragraph', 'text': 'Hi'},
            ], status='ready',
        )
        db.session.add(template)
        db.session.flush()

        campaign = MarketingCampaign(
            organization_id=org.id, user_id=user.id, name='Quota probe',
            kind='one_time', status='sending',
        )
        db.session.add(campaign)
        db.session.flush()

        step = MarketingCampaignStep(
            organization_id=org.id, campaign_id=campaign.id,
            template_id=template.id, step_index=0,
        )
        db.session.add(step)
        db.session.flush()

        for status in ('delivered', 'sent', 'bounced', 'queued', 'skipped', 'skipped'):
            db.session.add(MarketingSend(
                organization_id=org.id, campaign_id=campaign.id, step_id=step.id,
                contact_id=contact.id, template_id=template.id, user_id=user.id,
                to_email=contact.email, status=status,
                skip_reason='suppressed' if status == 'skipped' else None,
                created_at=datetime.utcnow(),
                unsubscribe_token=suppression.issue_token(org.id),
            ))
        db.session.commit()
        org_id = org.id

    yield org_id

    with app.app_context():
        MarketingSend.query.delete()
        MarketingCampaignStep.query.delete()
        MarketingCampaign.query.delete()
        MarketingTemplate.query.delete()
        db.session.commit()


class TestUsage:
    def test_counts_only_emails_that_left(self, app, usage):
        # A skipped recipient never reached a mailbox provider, so charging the
        # org for it would punish good suppression hygiene.
        with app.app_context():
            assert sc.used_this_month(usage) == 4

    def test_ignores_last_month(self, app, usage):
        with app.app_context():
            row = MarketingSend.query.first()
            row.created_at = datetime(2020, 1, 15)
            db.session.commit()
            assert sc.used_this_month(usage) == 3

    def test_is_scoped_to_the_org(self, app, usage):
        with app.app_context():
            other = Organization.query.filter_by(slug='test-realty-b').first()
            assert sc.used_this_month(other.id) == 0

    def test_quota_combines_limit_and_usage(self, app, usage):
        with app.app_context():
            org = db.session.get(Organization, usage)
            quota = sc.quota_for(org)
            assert quota.limit == 2500
            assert quota.used == 4
            assert quota.remaining == 2496
