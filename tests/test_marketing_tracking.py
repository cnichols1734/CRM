"""Campaign delivery, public events and agent reporting against local SQLite."""
import base64
import importlib.util
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, inspect, text

from models import (MarketingCampaignStep, MarketingSend, MarketingTracking,
                    MarketingTrackingLink, MarketingTrackingEvent, db)
from services import gmail_service
from services.marketing import activity, launch, send, tracking
from marketing_helpers import enable_campaigns, load_org_user, make_contact, ready_template
from test_marketing_launch import _draft

pytestmark = pytest.mark.usefixtures('marketing_gmail')
HUMAN = {'User-Agent': 'Mozilla/5.0 Chrome/130.0'}


@pytest.fixture(autouse=True)
def clean_tracking(app):
    with app.app_context():
        for model in (MarketingTrackingEvent, MarketingTrackingLink, MarketingTracking):
            model.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        db.session.rollback()
        for model in (MarketingTrackingEvent, MarketingTrackingLink, MarketingTracking):
            model.query.delete()
        db.session.commit()


def campaign_with_send(seed, *, tracked=True):
    org, user = load_org_user(seed)
    enable_campaigns(org)
    contact = make_contact(org, user, first='Tracked', last='Recipient', email='tracked@example.com')
    campaign = _draft(org, user, ready_template(org, user), {'contact_ids': [contact.id]})
    launch.launch(campaign, org, user)
    row = MarketingSend.query.filter_by(campaign_id=campaign.id).one()
    row.status = 'sent'; row.sent_at = datetime.utcnow()
    campaign.sent_count = 1; campaign.queued_count = 0
    campaign.status = 'completed'
    if tracked:
        tracking.prepare(row, 'Check in', '<html><body><a href="https://example.com/listing?a=1&amp;b=2">View listing</a></body></html>', 'https://example.com/listing?a=1&b=2')
    db.session.commit()
    return campaign, row


def test_gmail_mime_to_events_to_campaign_ui(app, seed, monkeypatch, owner_a_client):
    gmail = MagicMock()
    gmail.users.return_value.messages.return_value.send.return_value.execute.return_value = {'id': 'gmail-tracked', 'threadId': 'thread'}
    monkeypatch.setattr(gmail_service, '_get_gmail_service', lambda *a, **kw: gmail)
    with app.app_context():
        campaign, row = campaign_with_send(seed, tracked=False)
        campaign.status = 'sending'; row.status = 'queued'; row.sent_at = None
        campaign.sent_count = 0; campaign.queued_count = 1
        monkeypatch.setattr(send, 'render_for_send', lambda *a: ('Hello', '<a href="https://example.com/listing">View listing</a>', 'https://example.com/listing'))
        send.deliver(row, persist_tracking=True)
        db.session.commit()
        cid, sid, step_id = campaign.id, row.id, row.step_id
        captured = gmail.users.return_value.messages.return_value.send.call_args.kwargs['body']['raw']
        mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(captured))
        html = mime.get_body(preferencelist=('html',)).get_content()
        plain = mime.get_body(preferencelist=('plain',)).get_content()
        tracked = MarketingTracking.query.filter_by(send_id=sid).one()
        link = tracked.links[0]
        open_path = urlsplit(tracking.public_url('open', tracked.token)).path
        click_path = urlsplit(tracking.public_url('click', link.token)).path
        assert open_path in html and click_path in html and click_path in plain
        assert mime['From'].endswith('<owner-google@example.com>')
        assert mime['Reply-To'] == 'owner-google@example.com'
        assert '/email/unsubscribe/' in mime['List-Unsubscribe']
        assert gmail_service.GMAIL_SCOPES == ['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/gmail.send', 'https://www.googleapis.com/auth/calendar.events']
    client = app.test_client()
    before = owner_a_client.get(f'/marketing/campaigns/{cid}/progress').json
    pixel = client.get(open_path, headers=HUMAN)
    assert pixel.status_code == 200 and pixel.mimetype == 'image/gif'
    assert pixel.headers['Cache-Control'] == 'no-store, max-age=0'
    redirect = client.get(click_path, headers=HUMAN)
    assert redirect.status_code == 302 and redirect.location == 'https://example.com/listing'
    assert redirect.headers['Referrer-Policy'] == 'no-referrer'
    body = owner_a_client.get(f'/marketing/campaigns/{cid}?step={step_id}').get_data(as_text=True)
    assert '1 recipient opened · 1 recipient clicked' in body
    assert '1 recorded opens for tracked@example.com' in body
    assert '1 recorded clicks for tracked@example.com' in body
    assert b'/email/track/' not in owner_a_client.get(f'/marketing/campaigns/{cid}/steps/{step_id}/preview').data
    assert b'Opens' in owner_a_client.get('/marketing/sent').data
    history = owner_a_client.get(f'/marketing/campaigns/{cid}/sends/{sid}/activity').get_data(as_text=True)
    assert 'View listing' in history and 'Open recorded' in history
    after = owner_a_client.get(f'/marketing/campaigns/{cid}/progress').json
    assert before['engagement_revision'] != after['engagement_revision']
    # Sender sees the frozen MIME again on a retry, with the same tokens.
    with app.app_context():
        row = db.session.get(MarketingSend, sid)
        original = MarketingTracking.query.filter_by(send_id=sid).one()
        assert tracking.prepare(row, 'Changed', '<p>Changed</p>', 'Changed').html_body == original.html_body
        assert MarketingTracking.query.filter_by(send_id=sid).count() == 1


def test_repeated_requests_bots_sender_and_head(app, seed, owner_a_client):
    with app.app_context():
        campaign, row = campaign_with_send(seed)
        cid, sid = campaign.id, row.id
        tracked = MarketingTracking.query.filter_by(send_id=sid).one()
        path = urlsplit(tracking.public_url('open', tracked.token)).path
    anon = app.test_client()
    assert anon.head(path, headers=HUMAN).status_code == 200
    anon.get(path, headers=HUMAN); anon.get(path, headers=HUMAN)
    anon.get(path, headers={'User-Agent':'Proofpoint scanner'})
    anon.get(path, headers={'User-Agent':'Mozilla/5.0', 'Sec-Purpose':'prefetch'})
    owner_a_client.get(path, headers=HUMAN)
    with app.app_context():
        metric = tracking.per_send([sid], seed['org_a'])[sid]
        assert metric['opens'] == 1
        assert metric['excluded'] == 3
        assert MarketingTrackingEvent.query.count() == 4
        tracked = MarketingTracking.query.filter_by(send_id=sid).one()
        with app.test_request_context(headers=HUMAN):
            tracking.record(tracked, 'open', now=datetime.utcnow()+timedelta(minutes=1))
            db.session.commit()
        assert tracking.per_send([sid], seed['org_a'])[sid]['opens'] == 2


def test_recipient_filters_legacy_and_scheduled_are_not_zero(app, seed, owner_a_client):
    with app.app_context():
        campaign, row = campaign_with_send(seed)
        cid, sid = campaign.id, row.id
        tracked = MarketingTracking.query.filter_by(send_id=sid).one()
        path = urlsplit(tracking.public_url('click', tracked.links[0].token)).path
        legacy, old_send = campaign_with_send(seed, tracked=False)
        old_cid = legacy.id
    assert b'No activity recorded' in owner_a_client.get(f'/marketing/campaigns/{cid}?engagement=none').data
    assert b'No recipients match this filter' in owner_a_client.get(f'/marketing/campaigns/{cid}?engagement=clicked').data
    app.test_client().get(path, headers=HUMAN)
    assert b'tracked@example.com' in owner_a_client.get(f'/marketing/campaigns/{cid}?engagement=clicked').data
    assert b'No recipients match this filter' in owner_a_client.get(f'/marketing/campaigns/{cid}?engagement=none').data
    assert b'Tracking unavailable' in owner_a_client.get(f'/marketing/campaigns/{old_cid}').data
    assert b'No recipients match this filter' in owner_a_client.get(f'/marketing/campaigns/{old_cid}?engagement=none').data
    with app.app_context():
        row = db.session.get(MarketingSend, sid); row.sent_at = None; row.status = 'queued'
        db.session.commit()
    assert b'Tracking starts after sending' in owner_a_client.get(f'/marketing/campaigns/{cid}').data


def test_summary_deduplicates_address_across_followups(app, seed):
    with app.app_context():
        campaign, row = campaign_with_send(seed)
        step = MarketingCampaignStep(organization_id=row.organization_id, campaign_id=campaign.id,
            template_id=row.template_id, step_index=1, delay_days=1, send_hour_local=9)
        db.session.add(step); db.session.flush()
        second = MarketingSend(organization_id=row.organization_id, campaign_id=campaign.id,
            step_id=step.id, contact_id=row.contact_id, template_id=row.template_id,
            user_id=row.user_id, to_email=row.to_email.upper(), status='sent', sent_at=datetime.utcnow(),
            unsubscribe_token='second-tracked-send')
        db.session.add(second); db.session.flush()
        tracking.prepare(second, 'Follow-up', '<p>Hello</p>', 'Hello')
        for target in [row, second]:
            tracked = MarketingTracking.query.filter_by(send_id=target.id).one()
            with app.test_request_context(headers=HUMAN):
                tracking.record(tracked, 'open')
        db.session.flush()
        stats = tracking.campaign_summary(campaign)
        assert stats['summary']['opened'] == 1 and stats['summary']['opens'] == 2
        assert stats['steps'][row.step_id]['opened'] == 1
        assert stats['steps'][step.id]['opened'] == 1
        db.session.rollback()


def test_tracking_preserves_markup_exclusions_and_plain_text(app, seed):
    with app.app_context():
        _, row = campaign_with_send(seed, tracked=False)
        html = '''<!DOCTYPE html><html><body><!--[if mso]><table><![endif]--><a style="color:red" href="https://example.com/x?a=1&amp;b=2">Listing</a><a href="mailto:a@example.com">Mail</a><a href="tel:555">Call</a><a href="https://example.com/email/unsubscribe/token">Unsubscribe</a></body></html>'''
        record = tracking.prepare(row, 'Test', html, 'See https://example.com/x?a=1&b=2. Unsubscribe: https://example.com/email/unsubscribe/token')
        assert '<!--[if mso]><table><![endif]-->' in record.html_body
        assert 'style="color:red"' in record.html_body
        assert 'href="mailto:a@example.com"' in record.html_body and 'href="tel:555"' in record.html_body
        assert 'href="https://example.com/email/unsubscribe/token"' in record.html_body
        assert record.text_body.endswith('https://example.com/email/unsubscribe/token')
        assert len(record.links) == 1 and record.links[0].destination == 'https://example.com/x?a=1&b=2'
        assert '.gif' in record.html_body and '.gif' not in record.text_body
        db.session.rollback()


@pytest.mark.parametrize('destination', ['javascript:alert(1)', '//evil.example', 'https://good.example\\@evil.example', 'https://user:pass@example.com', 'https://example.com\r\nLocation: https://evil.example'])
def test_unsafe_destinations_not_tracked(destination):
    assert not tracking.safe_destination(destination)


def test_invalid_token_and_agent_tenant_boundaries(app, seed, owner_a_client, agent_a_client, owner_b_client):
    with app.app_context():
        campaign, row = campaign_with_send(seed)
        cid, sid = campaign.id, row.id
        org_b, _ = load_org_user(seed, 'org_b', 'owner_b')
        enable_campaigns(org_b)
        db.session.commit()
        tracked = MarketingTracking.query.filter_by(send_id=sid).one()
        token = tracked.links[0].token
    anon = app.test_client()
    assert anon.get('/email/track/click/1.invalid').status_code == 404
    assert anon.get(f'/email/track/click/{seed["org_b"]}.{token.split(".")[1]}').status_code == 404
    assert anon.get(f'/marketing/campaigns/{cid}/sends/{sid}/activity').status_code == 302
    assert agent_a_client.get(f'/marketing/campaigns/{cid}/sends/{sid}/activity').status_code == 403
    assert owner_b_client.get(f'/marketing/campaigns/{cid}/sends/{sid}/activity').status_code in (403,404)
    assert owner_a_client.get(f'/marketing/campaigns/{cid}/sends/{sid}/activity').status_code == 200
    # The query string cannot override the stored redirect destination.
    response = anon.get(f'/email/track/click/{token}?url=https://evil.example', headers=HUMAN)
    assert response.location == 'https://example.com/listing?a=1&b=2'


def test_click_redirect_survives_event_write_failure(app, seed, monkeypatch):
    from sqlalchemy.exc import OperationalError
    with app.app_context():
        _, row = campaign_with_send(seed)
        path = urlsplit(tracking.public_url('click', row.tracking.links[0].token)).path
    monkeypatch.setattr(tracking, 'record', MagicMock(side_effect=OperationalError('insert', {}, Exception('failed'))))
    assert app.test_client().get(path, headers=HUMAN).location == 'https://example.com/listing?a=1&b=2'


def test_additive_sqlite_migration_is_repeatable(tmp_path):
    path = Path(__file__).parents[1] / 'migrations/versions/add_marketing_tracking.py'
    spec = importlib.util.spec_from_file_location('tracking_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE organizations (id INTEGER PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE marketing_sends (id INTEGER PRIMARY KEY, subject TEXT)'))
        conn.execute(text("INSERT INTO marketing_sends VALUES (1, 'Existing send')"))
        for _ in range(2):
            for statement in migration.statements('sqlite'):
                conn.execute(text(statement))
        assert conn.execute(text('SELECT subject FROM marketing_sends')).scalar() == 'Existing send'
        assert set(migration.TABLES).issubset(inspect(conn).get_table_names())
        assert 'token' in {c['name'] for c in inspect(conn).get_columns('marketing_tracking')}
    postgres = '\n'.join(migration.statements('postgresql'))
    assert postgres.count('FORCE ROW LEVEL SECURITY') == 3
    assert postgres.count('REVOKE ALL ON TABLE') == 3
    assert 'ALTER TABLE marketing_sends' not in postgres


def test_worker_publishes_tracking_before_provider_and_handles_unknown_result(app, seed, monkeypatch):
    from jobs.marketing_outbox_worker import run_marketing_outbox_worker
    with app.app_context():
        campaign, row = campaign_with_send(seed, tracked=False)
        campaign.status = 'sending'; row.status = 'queued'; row.sent_at = None
        row.scheduled_for = datetime.utcnow() - timedelta(minutes=1)
        campaign.sent_count = 0; campaign.queued_count = 1
        sid = row.id
        db.session.commit()
        def provider(**kwargs):
            # A different DB connection must see the link before delivery.
            with db.engine.connect() as connection:
                assert connection.execute(text('SELECT count(*) FROM marketing_tracking WHERE send_id=:id'), {'id':sid}).scalar() == 1
            raise RuntimeError('Unexpected provider response')
        monkeypatch.setattr(send, '_provider_send', provider)
        result = run_marketing_outbox_worker(org_id=seed['org_a'])
        assert result['errors'] >= 1
        db.session.expire_all()
        row = db.session.get(MarketingSend, sid)
        assert row.status == 'failed'
        assert 'Check Gmail Sent' in row.error


def test_worker_recovers_interrupted_send_without_resending(app, seed, monkeypatch):
    from jobs.marketing_outbox_worker import run_marketing_outbox_worker
    with app.app_context():
        campaign, row = campaign_with_send(seed)
        sid = row.id
        campaign.status = 'sending'; campaign.queued_count = 1; campaign.sent_count = 0
        row.status = 'sending'; row.sent_at = None
        row.last_attempt_at = datetime.utcnow() - timedelta(hours=2)
        db.session.commit()
        monkeypatch.setattr(send, '_provider_send', lambda **kwargs: pytest.fail('Do not resend an uncertain delivery'))
        run_marketing_outbox_worker(org_id=seed['org_a'])
        row = db.session.get(MarketingSend, sid)
        assert row.status == 'failed' and 'Check Gmail Sent' in row.error
        assert row.campaign.failed_count == 1
