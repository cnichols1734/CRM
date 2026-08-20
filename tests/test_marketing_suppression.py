"""Suppression rules and the public unsubscribe endpoint.

The endpoint is the highest-risk surface in the feature: unauthenticated, it
mutates data, and getting it wrong is either a CAN-SPAM violation or a way for a
stranger to unsubscribe someone else.
"""
from datetime import datetime

import pytest

from models import (
    Contact, MarketingCampaign, MarketingCampaignStep, MarketingSend,
    MarketingSuppression, MarketingTemplate, Organization, User, db,
)
from services.marketing import suppression


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sends(app, seed):
    """One queued send in each org, addressed to that org's contact."""
    with app.app_context():
        created = {}
        for slug in ('test-realty-a', 'test-realty-b'):
            org = Organization.query.filter_by(slug=slug).first()
            user = User.query.filter_by(organization_id=org.id).first()
            contact = Contact.query.filter_by(organization_id=org.id).first()

            template = MarketingTemplate(
                organization_id=org.id, created_by_id=user.id,
                name='Checking in', category='check_in',
                subject='Checking in', blocks=[
                    {'type': 'paragraph', 'text': 'Hi {{contact.first_name}}'},
                ],
                status='ready',
            )
            db.session.add(template)
            db.session.flush()

            campaign = MarketingCampaign(
                organization_id=org.id, user_id=user.id,
                name='Spring check-in', kind='one_time', status='sending',
            )
            db.session.add(campaign)
            db.session.flush()

            step = MarketingCampaignStep(
                organization_id=org.id, campaign_id=campaign.id,
                template_id=template.id, step_index=0,
            )
            db.session.add(step)
            db.session.flush()

            send = MarketingSend(
                organization_id=org.id, campaign_id=campaign.id, step_id=step.id,
                contact_id=contact.id, template_id=template.id, user_id=user.id,
                to_email=contact.email, subject_rendered='Checking in',
                status='sent', sent_at=datetime.utcnow(),
                unsubscribe_token=suppression.issue_token(org.id),
            )
            db.session.add(send)
            db.session.flush()

            created[slug] = {
                'org_id': org.id, 'contact_id': contact.id,
                'send_id': send.id, 'token': send.unsubscribe_token,
                'email': send.to_email,
            }
        db.session.commit()

    yield created

    with app.app_context():
        MarketingSuppression.query.delete()
        MarketingSend.query.delete()
        MarketingCampaignStep.query.delete()
        MarketingCampaign.query.delete()
        MarketingTemplate.query.delete()
        for record in created.values():
            contact = db.session.get(Contact, record['contact_id'])
            if contact:
                contact.marketing_consent = 'unknown'
                contact.marketing_consent_source = None
                contact.marketing_consent_at = None
        db.session.commit()


@pytest.fixture()
def org_a(sends):
    return sends['test-realty-a']


@pytest.fixture()
def org_b(sends):
    return sends['test-realty-b']


# ---------------------------------------------------------------------------
# Addresses and scope
# ---------------------------------------------------------------------------

class TestNormalize:
    @pytest.mark.parametrize('raw,expected', [
        ('  Jane@Example.COM ', 'jane@example.com'),
        ('JANE@EXAMPLE.COM', 'jane@example.com'),
        (None, ''),
        ('', ''),
    ])
    def test_casefolds_and_trims(self, raw, expected):
        assert suppression.normalize(raw) == expected

    def test_leaves_plus_addressing_alone(self):
        # Stripping a +tag is a Gmail convention, not a standard. Rewriting the
        # address would suppress mail the recipient never asked to stop.
        assert suppression.normalize('jane+homes@example.com') == 'jane+homes@example.com'


class TestScope:
    @pytest.mark.parametrize('reason,scope', [
        ('unsubscribe', 'org'),
        ('manual', 'org'),
        ('invalid', 'org'),
        ('bounce', 'platform'),
        ('spam_report', 'platform'),
    ])
    def test_reputation_damage_is_platform_wide(self, reason, scope):
        assert suppression.scope_for(reason) == scope


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

class TestTokens:
    def test_carries_the_tenant(self):
        token = suppression.issue_token(42)
        assert suppression.org_id_from_token(token) == 42

    def test_tokens_are_unique(self):
        assert len({suppression.issue_token(1) for _ in range(200)}) == 200

    @pytest.mark.parametrize('bad', [
        None, '', 'nodot', '.', 'abc.def', '0.abc', '-1.abc', '7.',
    ])
    def test_rejects_malformed_tokens(self, bad):
        assert suppression.org_id_from_token(bad) is None

    def test_resolves_a_real_send(self, app, org_a):
        with app.app_context():
            send = suppression.find_send_by_token(org_a['token'])
            assert send is not None
            assert send.id == org_a['send_id']

    def test_unknown_secret_resolves_to_nothing(self, app, org_a):
        with app.app_context():
            forged = f"{org_a['org_id']}.notarealsecret"
            assert suppression.find_send_by_token(forged) is None


# ---------------------------------------------------------------------------
# Suppress / read
# ---------------------------------------------------------------------------

class TestSuppress:
    def test_org_scope_hides_from_that_org_only(self, app, org_a, org_b):
        with app.app_context():
            suppression.suppress(
                'shared@example.com', 'unsubscribe', organization_id=org_a['org_id'],
            )
            db.session.commit()

            assert suppression.is_suppressed('shared@example.com', org_a['org_id'])
            assert not suppression.is_suppressed('shared@example.com', org_b['org_id'])

    def test_platform_scope_hides_from_everyone(self, app, org_a, org_b):
        with app.app_context():
            suppression.suppress('complainer@example.com', 'spam_report')
            db.session.commit()

            assert suppression.is_suppressed('complainer@example.com', org_a['org_id'])
            assert suppression.is_suppressed('complainer@example.com', org_b['org_id'])

    def test_platform_rows_belong_to_no_org(self, app, org_a):
        with app.app_context():
            row = suppression.suppress(
                'bounced@example.com', 'bounce', organization_id=org_a['org_id'],
            )
            db.session.commit()
            assert row.scope == 'platform'
            assert row.organization_id is None

    def test_repeat_suppression_is_the_same_row(self, app, org_a):
        with app.app_context():
            first = suppression.suppress(
                'dupe@example.com', 'unsubscribe', organization_id=org_a['org_id'],
            )
            db.session.commit()
            second = suppression.suppress(
                'dupe@example.com', 'unsubscribe', organization_id=org_a['org_id'],
            )
            db.session.commit()

            assert first.id == second.id
            assert MarketingSuppression.query.filter_by(
                email='dupe@example.com'
            ).count() == 1

    def test_stores_the_normalized_address(self, app, org_a):
        with app.app_context():
            suppression.suppress(
                '  Mixed@Example.COM ', 'manual', organization_id=org_a['org_id'],
            )
            db.session.commit()
            assert suppression.is_suppressed('mixed@EXAMPLE.com', org_a['org_id'])

    def test_ignores_a_blank_address(self, app, org_a):
        with app.app_context():
            assert suppression.suppress('', 'manual', organization_id=org_a['org_id']) is None
            assert suppression.suppress(None, 'manual', organization_id=org_a['org_id']) is None

    def test_rejects_an_unknown_reason(self, app, org_a):
        with app.app_context():
            with pytest.raises(suppression.SuppressionError, match='Unknown'):
                suppression.suppress(
                    'x@example.com', 'because', organization_id=org_a['org_id'],
                )

    def test_org_scope_requires_an_org(self, app):
        with app.app_context():
            with pytest.raises(suppression.SuppressionError, match='needs an organization'):
                suppression.suppress('x@example.com', 'unsubscribe')


class TestBatchLookup:
    def test_returns_only_suppressed_addresses(self, app, org_a):
        with app.app_context():
            suppression.suppress(
                'out@example.com', 'unsubscribe', organization_id=org_a['org_id'],
            )
            db.session.commit()

            found = suppression.suppressed_reasons(
                ['out@example.com', 'in@example.com'], org_a['org_id'],
            )
            assert found == {'out@example.com': 'unsubscribe'}

    def test_normalizes_the_input(self, app, org_a):
        with app.app_context():
            suppression.suppress(
                'out@example.com', 'unsubscribe', organization_id=org_a['org_id'],
            )
            db.session.commit()
            found = suppression.suppressed_reasons([' OUT@Example.com '], org_a['org_id'])
            assert 'out@example.com' in found

    def test_platform_reason_wins_over_org_reason(self, app, org_a):
        # An agent shown "unsubscribed" for a spam complaint would try to fix
        # it; the stronger fact has to be the one reported.
        with app.app_context():
            suppression.suppress(
                'both@example.com', 'unsubscribe', organization_id=org_a['org_id'],
            )
            suppression.suppress('both@example.com', 'spam_report')
            db.session.commit()

            found = suppression.suppressed_reasons(['both@example.com'], org_a['org_id'])
            assert found['both@example.com'] == 'spam_report'

    def test_empty_input_does_not_query(self, app, org_a):
        with app.app_context():
            assert suppression.suppressed_reasons([], org_a['org_id']) == {}
            assert suppression.suppressed_reasons([None, ''], org_a['org_id']) == {}

    def test_handles_more_addresses_than_one_query_allows(self, app, org_a):
        with app.app_context():
            addresses = [f'bulk{i}@example.com' for i in range(1500)]
            for address in addresses[:3]:
                suppression.suppress(
                    address, 'unsubscribe', organization_id=org_a['org_id'],
                )
            db.session.commit()

            found = suppression.suppressed_reasons(addresses, org_a['org_id'])
            assert set(found) == set(addresses[:3])


class TestRelease:
    def test_undoes_an_unsubscribe(self, app, org_a):
        with app.app_context():
            suppression.suppress(
                'back@example.com', 'unsubscribe', organization_id=org_a['org_id'],
            )
            db.session.commit()

            assert suppression.release('back@example.com', org_a['org_id'])
            db.session.commit()
            assert not suppression.is_suppressed('back@example.com', org_a['org_id'])

    def test_refuses_to_clear_a_spam_complaint(self, app, org_a):
        # Letting one tenant clear a complaint would let them burn the shared
        # sending domain for everyone else.
        with app.app_context():
            suppression.suppress('complained@example.com', 'spam_report')
            db.session.commit()

            assert not suppression.release('complained@example.com', org_a['org_id'])
            db.session.commit()
            assert suppression.is_suppressed('complained@example.com', org_a['org_id'])

    def test_refuses_to_clear_a_bounce(self, app, org_a):
        with app.app_context():
            suppression.suppress('dead@example.com', 'bounce')
            db.session.commit()
            assert not suppression.release('dead@example.com', org_a['org_id'])

    def test_will_not_clear_another_orgs_row(self, app, org_a, org_b):
        with app.app_context():
            suppression.suppress(
                'theirs@example.com', 'unsubscribe', organization_id=org_a['org_id'],
            )
            db.session.commit()

            assert not suppression.release('theirs@example.com', org_b['org_id'])
            db.session.commit()
            assert suppression.is_suppressed('theirs@example.com', org_a['org_id'])

    def test_releasing_nothing_reports_no_change(self, app, org_a):
        with app.app_context():
            assert not suppression.release('never@example.com', org_a['org_id'])


class TestRecordUnsubscribe:
    def test_suppresses_and_marks_the_contact(self, app, org_a):
        with app.app_context():
            send = db.session.get(MarketingSend, org_a['send_id'])
            row = suppression.record_unsubscribe(send)
            db.session.commit()

            assert row.reason == 'unsubscribe'
            assert row.source_send_id == send.id
            contact = db.session.get(Contact, org_a['contact_id'])
            assert contact.marketing_consent == 'opted_out'
            assert contact.marketing_consent_source == 'unsubscribe_link'
            assert contact.marketing_consent_at is not None
            assert not contact.can_receive_marketing

    def test_resubscribe_returns_consent_to_unknown(self, app, org_a):
        # Taking back an opt-out is not the same as affirmatively agreeing.
        with app.app_context():
            send = db.session.get(MarketingSend, org_a['send_id'])
            suppression.record_unsubscribe(send)
            db.session.commit()

            assert suppression.resubscribe(send)
            db.session.commit()

            contact = db.session.get(Contact, org_a['contact_id'])
            assert contact.marketing_consent == 'unknown'
            assert not suppression.is_suppressed(send.to_email, send.organization_id)


class TestHeaders:
    def test_promises_one_click(self, app):
        headers = suppression.unsubscribe_headers('https://app.example/u/7.abc')
        assert headers['List-Unsubscribe'] == '<https://app.example/u/7.abc>'
        assert headers['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'

    def test_includes_mailto_when_given(self, app):
        headers = suppression.unsubscribe_headers(
            'https://app.example/u/7.abc', mailto='unsub@example.com',
        )
        assert headers['List-Unsubscribe'] == (
            '<https://app.example/u/7.abc>, <mailto:unsub@example.com>'
        )


# ---------------------------------------------------------------------------
# Public endpoint
# ---------------------------------------------------------------------------

class TestUnsubscribeEndpoint:
    def test_get_asks_instead_of_acting(self, app, client, org_a):
        # Mail clients and security appliances prefetch links. A scanner must
        # not be able to opt a recipient out.
        response = client.get(f"/email/unsubscribe/{org_a['token']}")
        assert response.status_code == 200
        assert b'Unsubscribe' in response.data

        with app.app_context():
            assert not suppression.is_suppressed(org_a['email'], org_a['org_id'])

    def test_post_unsubscribes(self, app, client, org_a):
        response = client.post(f"/email/unsubscribe/{org_a['token']}")
        assert response.status_code == 200

        with app.app_context():
            assert suppression.is_suppressed(org_a['email'], org_a['org_id'])

    def test_one_click_post_returns_no_page(self, app, client, org_a):
        # Gmail posts this unattended. Nobody is looking at a rendered page.
        response = client.post(
            f"/email/unsubscribe/{org_a['token']}",
            data={'List-Unsubscribe': 'One-Click'},
        )
        assert response.status_code == 200
        assert response.mimetype == 'text/plain'

        with app.app_context():
            assert suppression.is_suppressed(org_a['email'], org_a['org_id'])

    def test_second_post_still_succeeds(self, app, client, org_a):
        client.post(f"/email/unsubscribe/{org_a['token']}")
        response = client.post(f"/email/unsubscribe/{org_a['token']}")
        assert response.status_code == 200

    def test_get_reports_an_existing_opt_out(self, app, client, org_a):
        client.post(f"/email/unsubscribe/{org_a['token']}")
        response = client.get(f"/email/unsubscribe/{org_a['token']}")
        assert b'already unsubscribed' in response.data

    def test_undo_puts_them_back(self, app, client, org_a):
        client.post(f"/email/unsubscribe/{org_a['token']}")
        response = client.post(f"/email/unsubscribe/{org_a['token']}/undo")
        assert response.status_code == 200

        with app.app_context():
            assert not suppression.is_suppressed(org_a['email'], org_a['org_id'])

    def test_shows_the_brokerage_not_our_brand(self, app, client, org_a):
        response = client.get(f"/email/unsubscribe/{org_a['token']}")
        assert b'Test Realty A' in response.data

    @pytest.mark.parametrize('token', [
        'garbage', '1.wrongsecret', '999999.abc', 'nodot',
    ])
    def test_unknown_token_is_a_404(self, client, token):
        assert client.get(f'/email/unsubscribe/{token}').status_code == 404
        assert client.post(f'/email/unsubscribe/{token}').status_code == 404

    def test_a_token_only_unsubscribes_its_own_address(self, app, client, org_a, org_b):
        client.post(f"/email/unsubscribe/{org_a['token']}")

        with app.app_context():
            assert not suppression.is_suppressed(org_b['email'], org_b['org_id'])

    def test_needs_no_session(self, app, client, org_a):
        # Recipients are not our users. The route sets RLS context from the
        # token because set_tenant_context returns early for anonymous callers.
        response = client.post(f"/email/unsubscribe/{org_a['token']}")
        assert response.status_code == 200
        assert 'login' not in response.headers.get('Location', '')
