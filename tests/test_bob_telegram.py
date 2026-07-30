"""Tests for B.O.B. on Telegram.

Covers webhook auth, update idempotency, binding tokens, silence for unbound
senders, Confirm/Cancel/Undo callbacks, cross-tenant refusal, and rate limits.

Run with: python -m pytest tests/test_bob_telegram.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (
    AgentMessagingChannel,
    BobAction,
    MessagingInboundUpdate,
    MessagingLinkToken,
    Organization,
    User,
    db,
)
from services.bob_tools import BobContext, dispatch
from services.messaging.binding import (
    BindingError,
    constant_time_secret_match,
    disconnect_channel,
    find_channel_by_external_id,
    get_active_channel,
    issue_link_token,
    redeem_link_token,
)
from services.messaging.conversation import (
    PER_USER_DAILY_LIMIT,
    RateLimitExceeded,
    _bump_daily_count,
    handle_callback_query,
    handle_inbound_message,
)
from services.messaging.telegram import (
    FakeTransport,
    markdown_to_telegram_html,
    set_transport_override,
)


WEBHOOK_PATH = 'test-webhook-path'
WEBHOOK_SECRET = 'test-webhook-secret'


@pytest.fixture(autouse=True)
def _telegram_config(app):
    app.config['TELEGRAM_BOT_TOKEN'] = '123456:TESTTOKEN'
    app.config['TELEGRAM_BOT_USERNAME'] = 'BobTestBot'
    app.config['TELEGRAM_WEBHOOK_SECRET'] = WEBHOOK_SECRET
    app.config['TELEGRAM_WEBHOOK_PATH'] = WEBHOOK_PATH
    app.config['APP_BASE_URL'] = 'https://example.test'
    yield


@pytest.fixture(autouse=True)
def _fake_transport():
    transport = FakeTransport()
    set_transport_override(transport)
    yield transport
    set_transport_override(None)


@pytest.fixture(autouse=True)
def _enable_telegram_feature(app, seed):
    with app.app_context():
        org = db.session.get(Organization, seed['org_a'])
        flags = dict(org.feature_flags or {})
        flags['BOB_TELEGRAM'] = True
        org.feature_flags = flags
        db.session.commit()
    yield


@pytest.fixture()
def linked_channel(app, seed, _fake_transport):
    """Agent A in Org A linked to a Telegram identity."""
    with app.app_context():
        channel = AgentMessagingChannel(
            user_id=seed['agent_a'],
            organization_id=seed['org_a'],
            provider='telegram',
            external_id='9001',
            chat_id='9001',
            linked_at=datetime.utcnow(),
        )
        db.session.add(channel)
        db.session.commit()
        data = {
            'id': channel.id,
            'user_id': channel.user_id,
            'organization_id': channel.organization_id,
            'external_id': channel.external_id,
            'chat_id': channel.chat_id,
        }
    yield data
    with app.app_context():
        AgentMessagingChannel.query.filter_by(id=data['id']).delete()
        MessagingInboundUpdate.query.filter_by(provider='telegram').delete()
        MessagingLinkToken.query.filter_by(user_id=seed['agent_a']).delete()
        BobAction.query.filter_by(
            user_id=seed['agent_a'], surface='bob_telegram',
        ).delete()
        db.session.commit()


def _webhook(client, payload, *, secret=WEBHOOK_SECRET, path=WEBHOOK_PATH):
    return client.post(
        f'/webhooks/telegram/{path}',
        json=payload,
        headers={'X-Telegram-Bot-Api-Secret-Token': secret},
    )


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

class TestMarkdownToHtml:
    def test_escapes_then_applies_bold(self):
        assert '<b>hi</b>' in markdown_to_telegram_html('say **hi**')
        assert '&lt;script&gt;' in markdown_to_telegram_html('<script>')

    def test_code_stays_literal(self):
        out = markdown_to_telegram_html('use `a < b`')
        assert '<code>a &lt; b</code>' in out


# ---------------------------------------------------------------------------
# Secret comparison
# ---------------------------------------------------------------------------

class TestSecretMatch:
    def test_accepts_exact_match(self):
        assert constant_time_secret_match('abc', 'abc') is True

    def test_rejects_mismatch_and_missing(self):
        assert constant_time_secret_match('abc', 'abd') is False
        assert constant_time_secret_match('abc', None) is False
        assert constant_time_secret_match('', 'abc') is False


# ---------------------------------------------------------------------------
# Webhook auth + idempotency
# ---------------------------------------------------------------------------

class TestWebhookAuth:
    def test_bad_secret_rejected(self, app, seed):
        client = app.test_client()
        resp = _webhook(client, {'update_id': 1, 'message': {}}, secret='wrong')
        assert resp.status_code == 403

    def test_bad_path_rejected(self, app, seed):
        client = app.test_client()
        resp = client.post(
            '/webhooks/telegram/not-the-path',
            json={'update_id': 1},
            headers={'X-Telegram-Bot-Api-Secret-Token': WEBHOOK_SECRET},
        )
        assert resp.status_code == 404

    def test_duplicate_update_id_processed_once(self, app, seed, linked_channel):
        client = app.test_client()
        payload = {
            'update_id': 424242,
            'message': {
                'message_id': 7,
                'from': {'id': int(linked_channel['external_id'])},
                'chat': {'id': int(linked_channel['chat_id'])},
                'text': 'hello',
            },
        }
        with patch('routes.bob_telegram.enqueue_telegram_message') as enq:
            assert _webhook(client, payload).status_code == 200
            assert _webhook(client, payload).status_code == 200
            assert enq.call_count == 1

    def test_unbound_sender_is_silent(self, app, seed, _fake_transport):
        client = app.test_client()
        payload = {
            'update_id': 55,
            'message': {
                'message_id': 1,
                'from': {'id': 999999},
                'chat': {'id': 999999},
                'text': 'hello?',
            },
        }
        with patch('routes.bob_telegram.enqueue_telegram_message') as enq:
            assert _webhook(client, payload).status_code == 200
            enq.assert_not_called()
        assert _fake_transport.sent == []


# ---------------------------------------------------------------------------
# Binding tokens
# ---------------------------------------------------------------------------

class TestBinding:
    def test_token_works_once_and_fails_on_reuse(self, app, seed):
        with app.app_context():
            raw = issue_link_token(seed['agent_a'], seed['org_a'])
            channel = redeem_link_token(
                raw, external_id='7001', chat_id='7001',
            )
            assert channel.user_id == seed['agent_a']
            assert get_active_channel(seed['agent_a']) is not None

            with pytest.raises(BindingError):
                redeem_link_token(raw, external_id='7001', chat_id='7001')

            disconnect_channel(seed['agent_a'])
            AgentMessagingChannel.query.filter_by(
                user_id=seed['agent_a'], provider='telegram',
            ).delete()
            db.session.commit()

    def test_expired_token_rejected(self, app, seed):
        with app.app_context():
            raw = issue_link_token(seed['agent_a'], seed['org_a'])
            row = MessagingLinkToken.query.filter_by(
                user_id=seed['agent_a'], used_at=None,
            ).first()
            row.expires_at = datetime.utcnow() - timedelta(minutes=1)
            db.session.commit()

            with pytest.raises(BindingError):
                redeem_link_token(raw, external_id='7002', chat_id='7002')

            MessagingLinkToken.query.filter_by(user_id=seed['agent_a']).delete()
            db.session.commit()

    def test_start_with_token_links_via_webhook(self, app, seed, _fake_transport):
        client = app.test_client()
        with app.app_context():
            raw = issue_link_token(seed['agent_a'], seed['org_a'])

        payload = {
            'update_id': 88,
            'message': {
                'message_id': 2,
                'from': {'id': 8001},
                'chat': {'id': 8001},
                'text': f'/start {raw}',
            },
        }
        assert _webhook(client, payload).status_code == 200

        with app.app_context():
            channel = find_channel_by_external_id('8001')
            assert channel is not None
            assert channel.user_id == seed['agent_a']
            disconnect_channel(seed['agent_a'])
            AgentMessagingChannel.query.filter_by(
                user_id=seed['agent_a'],
            ).delete()
            MessagingLinkToken.query.filter_by(user_id=seed['agent_a']).delete()
            db.session.commit()

        assert any('Linked' in (m.get('text') or '') for m in _fake_transport.sent)


# ---------------------------------------------------------------------------
# Confirm / reject / undo callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:
    def test_confirm_executes_pending_action(
        self, app, seed, linked_channel, _fake_transport,
    ):
        with app.app_context():
            ctx = BobContext(
                user_id=seed['agent_a'],
                organization_id=seed['org_a'],
                org_role='agent',
                surface='bob_telegram',
            )
            # Create a contact the agent owns, then request a high-risk update.
            from models import Contact, ContactGroup
            group = ContactGroup.query.filter_by(
                user_id=seed['agent_a'], organization_id=seed['org_a'],
            ).first()
            contact = Contact(
                first_name='Tg', last_name='Confirm',
                user_id=seed['agent_a'],
                organization_id=seed['org_a'],
                email='tg.confirm@example.com',
            )
            if group:
                contact.groups.append(group)
            db.session.add(contact)
            db.session.commit()
            contact_id = contact.id

            pending = dispatch(
                'update_contact',
                {'contact_id': contact_id, 'fields': {'phone': '8325550100'}},
                ctx,
            )
            assert pending.requires_confirmation
            action_id = pending.action_id

            channel = db.session.get(AgentMessagingChannel, linked_channel['id'])
            channel.pending_action_id = action_id
            db.session.commit()

            handle_callback_query(
                channel_id=linked_channel['id'],
                org_id=seed['org_a'],
                callback_query_id='cb-1',
                data=f'confirm:{action_id}',
                message_id='10',
            )

            action = db.session.get(BobAction, action_id)
            assert action.status == BobAction.STATUS_EXECUTED
            refreshed = db.session.get(Contact, contact_id)
            assert '8325550100' in (refreshed.phone or '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '')

            db.session.delete(refreshed)
            db.session.commit()

        assert _fake_transport.answered
        assert _fake_transport.edited
        assert 'Confirmed' in _fake_transport.edited[0]['text']

    def test_confirm_after_ttl_does_not_execute(
        self, app, seed, linked_channel, _fake_transport,
    ):
        with app.app_context():
            ctx = BobContext(
                user_id=seed['agent_a'],
                organization_id=seed['org_a'],
                org_role='agent',
                surface='bob_telegram',
            )
            from models import Contact, ContactGroup
            group = ContactGroup.query.filter_by(
                user_id=seed['agent_a'], organization_id=seed['org_a'],
            ).first()
            contact = Contact(
                first_name='Tg', last_name='Expire',
                user_id=seed['agent_a'],
                organization_id=seed['org_a'],
                email='tg.expire@example.com',
            )
            if group:
                contact.groups.append(group)
            db.session.add(contact)
            db.session.commit()
            contact_id = contact.id
            original_phone = contact.phone

            pending = dispatch(
                'update_contact',
                {'contact_id': contact_id, 'fields': {'phone': '8325559999'}},
                ctx,
            )
            assert pending.requires_confirmation
            action = db.session.get(BobAction, pending.action_id)
            action.expires_at = datetime.utcnow() - timedelta(minutes=1)
            channel = db.session.get(AgentMessagingChannel, linked_channel['id'])
            channel.pending_action_id = action.id
            db.session.commit()
            action_id = action.id

            handle_callback_query(
                channel_id=linked_channel['id'],
                org_id=seed['org_a'],
                callback_query_id='cb-2',
                data=f'confirm:{action_id}',
                message_id='11',
            )

            refreshed = db.session.get(Contact, contact_id)
            assert refreshed.phone == original_phone
            action = db.session.get(BobAction, action_id)
            assert action.status in (
                BobAction.STATUS_EXPIRED, BobAction.STATUS_PENDING,
            )

            db.session.delete(refreshed)
            db.session.commit()

    def test_callback_with_other_users_action_refused(
        self, app, seed, linked_channel, _fake_transport,
    ):
        with app.app_context():
            # Pending action owned by owner_a, callback comes from agent_a's channel.
            foreign = BobAction(
                organization_id=seed['org_a'],
                user_id=seed['owner_a'],
                tool_name='update_contact',
                arguments={'contact_id': 1},
                status=BobAction.STATUS_PENDING,
                surface='bob_telegram',
                expires_at=datetime.utcnow() + timedelta(minutes=10),
                summary='Foreign action',
            )
            db.session.add(foreign)
            db.session.commit()
            foreign_id = foreign.id

            handle_callback_query(
                channel_id=linked_channel['id'],
                org_id=seed['org_a'],
                callback_query_id='cb-3',
                data=f'confirm:{foreign_id}',
                message_id='12',
            )

            action = db.session.get(BobAction, foreign_id)
            assert action.status == BobAction.STATUS_PENDING
            db.session.delete(action)
            db.session.commit()

        # Should have answered the callback and reported failure, not executed.
        assert _fake_transport.answered


# ---------------------------------------------------------------------------
# Cross-tenant + rate limits
# ---------------------------------------------------------------------------

class TestIsolationAndLimits:
    def test_channel_from_org_a_cannot_load_as_org_b(
        self, app, seed, linked_channel,
    ):
        with app.app_context():
            from services.messaging.conversation import _load_channel
            assert _load_channel(linked_channel['id'], seed['org_a']) is not None
            assert _load_channel(linked_channel['id'], seed['org_b']) is None

    def test_daily_rate_limit_trips(self, app, seed, linked_channel):
        with app.app_context():
            channel = db.session.get(
                AgentMessagingChannel, linked_channel['id'],
            )
            channel.daily_count = PER_USER_DAILY_LIMIT
            # Must match the local date _bump_daily_count compares against,
            # or the counter resets instead of tripping.
            channel.daily_count_date = date.today()
            db.session.commit()

            with pytest.raises(RateLimitExceeded):
                _bump_daily_count(channel)

            channel.daily_count = 0
            db.session.commit()


# ---------------------------------------------------------------------------
# Disabled channel silence
# ---------------------------------------------------------------------------

class TestToolLoopContract:
    """The tool loop only runs with a live model, so pin its contract here.

    ``ai_service.run_tool_conversation`` unpacks ``execute_tool`` into
    ``(payload, meta)``. A callback returning a bare dict blows up at runtime
    with "too many values to unpack" and the agent just sees a generic error.
    """

    def test_execute_tool_returns_model_and_client_payloads(
        self, app, seed, linked_channel, _fake_transport,
    ):
        captured = {}

        def fake_run_tool_conversation(
            *, system_prompt, messages, tools, execute_tool, **kwargs
        ):
            payload, meta = execute_tool('count_contacts', {})
            captured['payload'] = payload
            captured['meta'] = meta
            yield ('text', f"You have {payload.get('count')} contacts.")

        with app.app_context():
            with patch(
                'services.messaging.conversation.run_tool_conversation',
                fake_run_tool_conversation,
            ):
                handle_inbound_message(
                    channel_id=linked_channel['id'],
                    org_id=seed['org_a'],
                    text='How many contacts do I have?',
                    telegram_message_id='500',
                )

        assert isinstance(captured['payload'], dict)
        assert isinstance(captured['meta'], dict)
        sent = ' '.join(m['text'] for m in _fake_transport.sent)
        assert 'Something went wrong' not in sent
        assert 'contacts' in sent


# ---------------------------------------------------------------------------
# Disabled channel silence
# ---------------------------------------------------------------------------

class TestDisabledChannel:
    def test_disabled_channel_is_not_active(self, app, seed, linked_channel):
        with app.app_context():
            assert disconnect_channel(seed['agent_a']) is True
            assert get_active_channel(seed['agent_a']) is None
            assert find_channel_by_external_id(
                linked_channel['external_id']
            ) is None
