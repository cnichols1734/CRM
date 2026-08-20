"""Regression: register and org invite must never reach live SendGrid."""
from unittest.mock import MagicMock

from models import OrganizationInvite, User, db
from services.email_send_guard import (
    is_fixture_recipient,
    outbound_send_block_reason,
)


class TestFixtureRecipientGuard:
    def test_blocks_known_fixture_domains(self):
        assert is_fixture_recipient('nina@test.com')
        assert is_fixture_recipient('sam.seeded@test.com')
        assert is_fixture_recipient('newinvite@test.com')
        assert is_fixture_recipient('browser-test@example.com')
        assert is_fixture_recipient('user@localhost')
        assert is_fixture_recipient('agent@mail.test.com')

    def test_allows_real_product_domains(self):
        assert not is_fixture_recipient('agent@origentechnolog.com')
        assert not is_fixture_recipient('hello@gmail.com')
        assert not is_fixture_recipient('')
        assert not is_fixture_recipient(None)


class TestTestingFlagGuard:
    def test_testing_blocks_even_real_looking_addresses(self, app):
        with app.app_context():
            assert app.config['TESTING'] is True
            assert outbound_send_block_reason('agent@origentechnolog.com') == 'TESTING'

    def test_production_mode_allows_real_recipient(self, app):
        with app.app_context():
            app.config['TESTING'] = False
            try:
                assert outbound_send_block_reason(
                    'agent@origentechnolog.com'
                ) is None
                assert outbound_send_block_reason('nina@test.com') == 'fixture_recipient'
            finally:
                app.config['TESTING'] = True


class TestRegisterAndInviteNeverCallSendGrid:
    def test_nina_style_register_does_not_call_sendgrid(
        self, client, app, seed, block_live_email,
    ):
        send_stub = block_live_email['sendgrid_send']
        assert isinstance(send_stub, MagicMock)

        resp = client.post('/register', data={
            'company_name': 'No Send Realty',
            'first_name': 'Nina',
            'last_name': 'Guard',
            'email': 'nina.nosend@test.com',
            'password': 'supersecure123',
            'confirm_password': 'supersecure123',
        }, follow_redirects=True)

        assert resp.status_code == 200
        send_stub.assert_not_called()
        with app.app_context():
            assert User.query.filter_by(email='nina.nosend@test.com').first()

    def test_newinvite_style_invite_does_not_call_sendgrid(
        self, owner_a_client, app, seed, block_live_email,
    ):
        send_stub = block_live_email['sendgrid_send']
        assert isinstance(send_stub, MagicMock)

        resp = owner_a_client.post('/org/members/invite', data={
            'email': 'newinvite.nosend@test.com',
            'role': 'agent',
        }, follow_redirects=True)

        assert resp.status_code == 200
        send_stub.assert_not_called()
        with app.app_context():
            invite = OrganizationInvite.query.filter_by(
                email='newinvite.nosend@test.com',
            ).first()
            assert invite is not None

    def test_send_helpers_stay_stubbed_even_with_fake_api_key(
        self, client, app, seed, block_live_email, monkeypatch,
    ):
        monkeypatch.setenv('SENDGRID_API_KEY', 'SG.fake-pytest-key-not-real')
        app.config['SENDGRID_API_KEY'] = 'SG.fake-pytest-key-not-real'
        send_stub = block_live_email['sendgrid_send']

        resp = client.post('/register', data={
            'company_name': 'Leaked Key Realty',
            'first_name': 'Sam',
            'last_name': 'Seeded',
            'email': 'sam.nosend@test.com',
            'password': 'supersecure123',
            'confirm_password': 'supersecure123',
        }, follow_redirects=True)

        assert resp.status_code == 200
        send_stub.assert_not_called()


class TestSendHelpersShortCircuit:
    def test_send_html_returns_false_in_testing(self, app, seed, block_live_email):
        from services.sendgrid_outbound import _send_html

        with app.app_context():
            assert _send_html('nina@test.com', 'Hello', '<p>Hi</p>') is False
        block_live_email['sendgrid_send'].assert_not_called()

    def test_email_service_invite_returns_false_in_testing(
        self, app, seed, block_live_email,
    ):
        from services.email_service import EmailService

        with app.app_context():
            owner = db.session.get(User, seed['owner_a'])
            org = owner.organization
            service = EmailService(api_key='SG.fake-pytest-key-not-real')
            assert service.send_team_invite(
                org, owner, 'newinvite@test.com', 'https://localhost/invite',
            ) is False
        block_live_email['sendgrid_send'].assert_not_called()
        block_live_email['gmail_send'].assert_not_called()
