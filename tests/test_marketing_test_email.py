"""Template tests use the sending agent's connected mailbox."""
import base64
import json
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from config import Config
from models import (
    MarketingAudience, MarketingCampaign, MarketingCampaignStep, MarketingEnrollment,
    MarketingSend, MarketingTemplate, MarketingTemplateVersion, UserEmailIntegration, db,
)
from services import gmail_service
from services.marketing import send as sendmod
from services.marketing import drip, launch as launchmod

from marketing_helpers import enable_campaigns, load_org_user, make_contact
from test_marketing_launch import _draft


@pytest.fixture(autouse=True)
def cleanup_connections(app, seed):
    with app.app_context():
        # Other suites bulk-delete parents without SQLite foreign-key cascades.
        for model in (
            MarketingSend, MarketingEnrollment, MarketingCampaignStep,
            MarketingCampaign, MarketingAudience, MarketingTemplateVersion, MarketingTemplate,
        ):
            model.query.delete(synchronize_session=False)
        db.session.commit()
        existing = [row.id for row in UserEmailIntegration.query.all()]
        org, _ = load_org_user(seed)
        enable_campaigns(org)
        db.session.commit()
    yield
    with app.app_context():
        UserEmailIntegration.query.filter(
            UserEmailIntegration.id.notin_(existing),
        ).delete(synchronize_session=False)
        db.session.commit()


def _connect(org, user, **overrides):
    values = dict(
        organization_id=org.id,
        user_id=user.id,
        provider='gmail',
        connected_email='connected@gmail.com',
        sync_enabled=True,
        access_token_encrypted='mock-access',
        refresh_token_encrypted='mock-refresh',
        oauth_scope_version=2,
        signature_html='<p>Extra Gmail signature</p>',
    )
    values.update(overrides)
    integration = UserEmailIntegration(**values)
    db.session.add(integration)
    db.session.flush()
    return integration


def _send(org, agent):
    return sendmod.send_test(
        org=org,
        agent=agent,
        subject='Checking in, {{contact.first_name|there}}',
        preheader='Just a note',
        blocks=[
            {'type': 'paragraph', 'text': 'Hi {{contact.first_name|there}}.'},
            {'type': 'signature'},
        ],
        to_emails=['preview@example.com', 'second@example.com'],
        sample_values={'contact.first_name': 'Sarah'},
    )


def test_connected_gmail_sends_personalized_draft_from_connected_address(
    app, seed, monkeypatch,
):
    service = MagicMock()
    send = service.users.return_value.messages.return_value.send
    send.return_value.execute.return_value = {'id': 'gmail-test', 'threadId': 'thread'}
    get_service = MagicMock(return_value=service)
    monkeypatch.setattr(gmail_service, '_get_gmail_service', get_service)
    from sendgrid import SendGridAPIClient
    sendgrid = MagicMock(side_effect=AssertionError('Must use connected Gmail'))
    monkeypatch.setattr(SendGridAPIClient, 'send', sendgrid)
    monkeypatch.setattr(Config, 'SENDGRID_API_KEY', '')

    with app.app_context():
        org, owner = load_org_user(seed)
        integration = _connect(org, owner)
        count = MarketingSend.query.count()
        result = _send(org, owner)
        assert MarketingSend.query.count() == count
        assert all(call.args == (integration,) for call in get_service.call_args_list)

    assert result['sent'] == ['preview@example.com', 'second@example.com']
    assert result['subject'] == '[Test] Checking in, Sarah'
    assert send.call_count == 2
    for call, recipient in zip(send.call_args_list, result['sent']):
        assert call.kwargs['userId'] == 'me'
        message = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(call.kwargs['body']['raw'])
        )
        assert parseaddr(message['From'])[1] == 'connected@gmail.com'
        assert message['Reply-To'] == 'connected@gmail.com'
        assert message['To'] == recipient
        assert message['Subject'] == result['subject']
        assert 'Hi Sarah.' in message.get_body(preferencelist=('plain',)).get_content()
        html = message.get_body(preferencelist=('html',)).get_content()
        assert 'Hi Sarah.' in html
        assert 'Extra Gmail signature' not in html
    sendgrid.assert_not_called()


def _template(org, owner, name='Check-in'):
    template = MarketingTemplate(
        organization_id=org.id, created_by_id=owner.id,
        name=name, subject='Checking in', preheader='Just a note',
        category='check_in', visibility='org', status='ready',
        compliance_state='pass',
        blocks=[
            {'type': 'paragraph', 'text': 'Hi {{contact.first_name|there}}.'},
            {'type': 'signature'},
        ],
    )
    db.session.add(template)
    db.session.flush()
    return template


def _campaign(org, owner, **kwargs):
    enable_campaigns(org)
    contact = make_contact(
        org, owner, first='Gmail', last='Recipient', email='gmail-recipient@example.com',
    )
    return _draft(
        org, owner, _template(org, owner), filt={'contact_ids': [contact.id]}, **kwargs,
    )


def _queued(campaign):
    return MarketingSend.query.filter_by(campaign_id=campaign.id, status='queued').first()


@pytest.mark.parametrize('kind', ['one_time', 'scheduled', 'drip'])
def test_campaign_sends_from_creator_with_unsubscribe_headers(app, seed, monkeypatch, kind):
    service = MagicMock()
    request = service.users.return_value.messages.return_value.send
    request.return_value.execute.return_value = {'id': 'gmail-campaign', 'threadId': 'thread'}
    get_service = MagicMock(return_value=service)
    monkeypatch.setattr(gmail_service, '_get_gmail_service', get_service)
    with app.app_context():
        org, owner = load_org_user(seed)
        _, other = load_org_user(seed, user_key='agent_a')
        integration = _connect(org, owner)
        _connect(org, other, connected_email='other@gmail.com')
        campaign = _campaign(org, owner, kind='drip' if kind == 'drip' else 'one_time')
        campaign.from_name = 'Campaign Owner'
        campaign.reply_to = 'replies@example.com'
        now = datetime.utcnow()
        if kind == 'scheduled':
            campaign.scheduled_at = now + timedelta(days=1)
        if kind == 'drip':
            second = _template(org, owner, name='Follow-up')
            db.session.add(MarketingCampaignStep(
                organization_id=org.id, campaign_id=campaign.id,
                template_id=second.id, step_index=1, delay_days=3, send_hour_local=9,
            ))
            db.session.flush()
        launchmod.launch(campaign, org, owner, now=now, commit=False)
        send = _queued(campaign)
        assert send.user_id == owner.id
        if kind == 'scheduled':
            assert send.scheduled_for == campaign.scheduled_at
            now = campaign.scheduled_at
        if kind == 'drip':
            enrollment = MarketingEnrollment.query.filter_by(campaign_id=campaign.id).first()
            drip.advance_one(enrollment, now=now + timedelta(days=4))
            db.session.flush()
            send = MarketingSend.query.filter_by(campaign_id=campaign.id).order_by(
                MarketingSend.id.desc(),
            ).first()
            assert send.user_id == owner.id
        # An older queued row can identify someone other than the creator.
        send.user_id = other.id
        before = campaign.queued_count
        sendmod.deliver(send, now=now)
        get_service.assert_called_once_with(integration, commit_refresh=False)
        assert send.status == 'sent'
        assert send.provider_message_id == 'gmail-campaign'
        assert campaign.queued_count == before - 1
        assert campaign.sent_count == 1
        assert campaign.delivered_count == 0
        message = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(request.call_args.kwargs['body']['raw'])
        )
        assert parseaddr(message['From']) == ('Campaign Owner', 'connected@gmail.com')
        assert message['Reply-To'] == 'replies@example.com'
        assert send.unsubscribe_token in message['List-Unsubscribe']
        assert message['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'
        html = message.get_body(preferencelist=('html',)).get_content()
        assert f'{owner.first_name} {owner.last_name}' in html
        assert owner.email not in html
        assert org.broker_license_number not in html
        assert org.broker_address not in html
        assert other.email not in html
        assert send.unsubscribe_token in html
        assert 'Extra Gmail signature' not in html


def test_launch_requires_creators_connection_even_when_launcher_connected(app, seed):
    with app.app_context():
        org, owner = load_org_user(seed)
        _, admin = load_org_user(seed, user_key='admin_a')
        _connect(org, admin)
        campaign = _campaign(org, owner)
        with pytest.raises(launchmod.LaunchError, match='connect their Google'):
            launchmod.launch(campaign, org, admin, commit=False)
        assert campaign.status == 'draft'
        assert _queued(campaign) is None


def test_disconnected_campaign_pauses_then_resumes_after_reconnect(app, seed, monkeypatch):
    provider = MagicMock(return_value='gmail-campaign')
    monkeypatch.setattr(sendmod, '_provider_send', provider)
    with app.app_context():
        org, owner = load_org_user(seed)
        integration = _connect(org, owner)
        campaign = _campaign(org, owner)
        launchmod.launch(campaign, org, owner, commit=False)
        send = _queued(campaign)
        count = campaign.queued_count
        integration.sync_enabled = False
        sendmod.deliver(send)
        assert campaign.status == 'paused'
        assert 'connect their Google' in campaign.auto_paused_reason
        assert send.status == 'queued'
        assert campaign.queued_count == count
        provider.assert_not_called()
        with pytest.raises(launchmod.LaunchError, match='connect their Google'):
            launchmod.resume(campaign, commit=False)
        integration.sync_enabled = True
        launchmod.resume(campaign, commit=False)
        assert campaign.auto_paused_reason is None
        sendmod.deliver(send)
        assert send.status == 'sent'
        assert send.error is None


@pytest.mark.parametrize('status,reason,expected', [
    (401, 'authError', 'paused'),
    (403, 'insufficientPermissions', 'paused'),
    (403, 'userRateLimitExceeded', 'retry'),
    (429, 'rateLimitExceeded', 'retry'),
    (503, 'backendError', 'retry'),
    (403, 'domainPolicy', 'failed'),
])
def test_google_errors_pause_retry_or_fail_campaign_sends(
    app, seed, monkeypatch, status, reason, expected,
):
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute.side_effect = HttpError(
        Response({'status': status}),
        json.dumps({'error': {'message': reason, 'errors': [{'reason': reason}]}}).encode(),
    )
    monkeypatch.setattr(gmail_service, '_get_gmail_service', lambda *a, **kw: service)
    with app.app_context():
        org, owner = load_org_user(seed)
        _connect(org, owner)
        campaign = _campaign(org, owner)
        launchmod.launch(campaign, org, owner, commit=False)
        send = _queued(campaign)
        count = campaign.queued_count
        now = datetime.utcnow()
        sendmod.deliver(send, now=now)
        assert campaign.sent_count == 0
        if expected == 'paused':
            assert campaign.status == 'paused'
            assert 'reconnect' in campaign.auto_paused_reason
            assert send.status == 'queued'
            assert campaign.queued_count == count
        elif expected == 'retry':
            assert send.status == 'queued'
            assert send.scheduled_for > now
            assert campaign.queued_count == count
            send.attempt_count = MarketingSend.MAX_ATTEMPTS - 1
            sendmod.deliver(send, now=send.scheduled_for)
            assert send.status == 'failed'
            assert campaign.failed_count == 1
        else:
            assert send.status == 'failed'
            assert campaign.failed_count == 1
            assert campaign.queued_count == count - 1


@pytest.mark.parametrize('refresh_fails', [False, True])
def test_expired_google_token_does_not_commit_the_worker_transaction(
    app, seed, monkeypatch, refresh_fails,
):
    credentials = MagicMock(token='refreshed-access')
    if refresh_fails:
        credentials.refresh.side_effect = ValueError('Token revoked')
    monkeypatch.setattr(gmail_service, 'Credentials', MagicMock(return_value=credentials))
    monkeypatch.setattr(gmail_service, 'decrypt_token', lambda value: value)
    monkeypatch.setattr(gmail_service, 'encrypt_token', lambda value: value)
    service = MagicMock()
    request = service.users.return_value.messages.return_value.send
    request.return_value.execute.return_value = {'id': 'gmail-refreshed'}
    monkeypatch.setattr(gmail_service, 'build', MagicMock(return_value=service))
    with app.app_context():
        org, owner = load_org_user(seed)
        integration = _connect(org, owner, token_expires_at=datetime.utcnow() - timedelta(hours=1))
        campaign = _campaign(org, owner)
        launchmod.launch(campaign, org, owner, commit=False)
        send = _queued(campaign)
        commit = MagicMock(side_effect=AssertionError('Worker owns the transaction'))
        with monkeypatch.context() as patch:
            patch.setattr(db.session, 'commit', commit)
            sendmod.deliver(send)
        commit.assert_not_called()
        if refresh_fails:
            request.assert_not_called()
            assert campaign.status == 'paused'
            assert send.status == 'queued'
            assert 'reconnect' in send.error
        else:
            assert integration.access_token_encrypted == 'refreshed-access'
            assert send.status == 'sent'
            assert send.provider_message_id == 'gmail-refreshed'


@pytest.mark.parametrize('connection', ['none', 'disconnected', 'other_agent', 'other_org'])
def test_missing_agent_gmail_connection_blocks_test_email(
    app, seed, monkeypatch, connection,
):
    sendgrid = MagicMock(return_value='sg-test')
    gmail = MagicMock(side_effect=AssertionError('Must not use another mailbox'))
    monkeypatch.setattr(sendmod, '_provider_send', sendgrid)
    monkeypatch.setattr(gmail_service, 'send_email', gmail)
    with app.app_context():
        org, owner = load_org_user(seed)
        if connection == 'disconnected':
            _connect(org, owner, sync_enabled=False)
        elif connection == 'other_agent':
            _, other = load_org_user(seed, user_key='agent_a')
            _connect(org, other)
        elif connection == 'other_org':
            _connect(org, owner, organization_id=seed['org_b'])
        with pytest.raises(sendmod.SendError, match='connect their Google'):
            _send(org, owner)
    sendgrid.assert_not_called()
    gmail.assert_not_called()


@pytest.mark.parametrize('missing', [
    'connected_email', 'access_token_encrypted', 'refresh_token_encrypted',
])
def test_incomplete_connection_requires_reconnect(app, seed, monkeypatch, missing):
    sendgrid = MagicMock()
    gmail = MagicMock()
    monkeypatch.setattr(sendmod, '_provider_send', sendgrid)
    monkeypatch.setattr(gmail_service, 'send_email', gmail)
    with app.app_context():
        org, owner = load_org_user(seed)
        _connect(org, owner, **{missing: None})
        with pytest.raises(sendmod.SendError, match='reconnect their Gmail'):
            _send(org, owner)
    sendgrid.assert_not_called()
    gmail.assert_not_called()


def test_old_google_permissions_require_reconnect(app, seed, monkeypatch):
    sendgrid = MagicMock()
    get_service = MagicMock(side_effect=AssertionError('Must not contact Google'))
    monkeypatch.setattr(sendmod, '_provider_send', sendgrid)
    monkeypatch.setattr(gmail_service, '_get_gmail_service', get_service)
    with app.app_context():
        org, owner = load_org_user(seed)
        _connect(org, owner, oauth_scope_version=1)
        with pytest.raises(sendmod.SendError, match='reconnect their Gmail'):
            _send(org, owner)
    sendgrid.assert_not_called()
    get_service.assert_not_called()


@pytest.mark.parametrize('partial', [False, True])
def test_gmail_failure_is_reported_without_falling_back(app, seed, monkeypatch, partial):
    failure = {'success': False, 'error': 'Gmail API error: 403 - Forbidden'}
    gmail = MagicMock(side_effect=[{'success': True} if partial else failure, failure])
    sendgrid = MagicMock()
    monkeypatch.setattr(gmail_service, 'send_email', gmail)
    from sendgrid import SendGridAPIClient
    monkeypatch.setattr(SendGridAPIClient, 'send', sendgrid)
    with app.app_context():
        org, owner = load_org_user(seed)
        _connect(org, owner)
        with pytest.raises(sendmod.SendError, match='Gmail API error: 403') as exc:
            _send(org, owner)
        if partial:
            assert 'Sent to 1, but 1 failed.' in str(exc.value)
    sendgrid.assert_not_called()


def test_test_email_endpoint_explains_missing_google_connection(
    owner_a_client, app, seed, monkeypatch,
):
    provider = MagicMock(side_effect=AssertionError('No account is connected'))
    monkeypatch.setattr(sendmod, '_provider_send', provider)
    with app.app_context():
        org, _ = load_org_user(seed)
        enable_campaigns(org)
        db.session.commit()
    response = owner_a_client.post('/marketing/api/send-test', json={
        'to': 'preview@example.com',
        'subject': 'Checking in',
        'blocks': [{'type': 'paragraph', 'text': 'Hi there.'}],
    })
    assert response.status_code == 400
    assert 'connect their Google account in their profile' in response.get_json()['error']
    provider.assert_not_called()
