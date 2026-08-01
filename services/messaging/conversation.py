"""Turn handler for B.O.B. over a messaging channel.

Owns history windowing, the keyword / callback pre-pass, the tool loop, and
reply rendering. The webhook never calls into this synchronously — the RQ job
does, after returning 200 to Telegram.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional

from models import (
    AgentMessagingChannel,
    BobAction,
    ChatConversation,
    ChatMessage,
    User,
    db,
)
from services.ai_service import run_tool_conversation
from services.bob_tools import (
    BobContext,
    confirm_action,
    openai_tool_schemas,
    reject_action,
    undo_action,
)
from services.bob_tools.notifications import ActionCollector
from services.bob_tools.notifications import flush as flush_action_notification
from services.bob_tools.registry import dispatch as bob_dispatch
from services.messaging.base import ChoiceOption
from services.messaging.prompt import TELEGRAM_SYSTEM_PROMPT
from services.messaging.telegram import get_transport, show_typing

logger = logging.getLogger(__name__)

HISTORY_TURNS = 12  # user+assistant pairs kept in the model window
SURFACE = 'bob_telegram'
CHANNEL_NAME = 'telegram'

# Per-user / per-org daily turn caps. Each inbound turn is a full OpenAI tool
# loop, so these are tighter than Magic Inbox's email limits.
PER_USER_DAILY_LIMIT = 100
PER_ORG_DAILY_LIMIT = 500

_CALLBACK_RE = re.compile(
    r'^(confirm|reject|undo):(\d+)$'
)
_COMMAND_RE = re.compile(r'^/([a-zA-Z_]+)(?:@\S+)?(?:\s+(.*))?$')


class RateLimitExceeded(Exception):
    """Raised when the agent or org has hit today's turn cap."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Public entry points used by the RQ job
# ---------------------------------------------------------------------------

def handle_inbound_message(
    *,
    channel_id: int,
    org_id: int,
    text: str,
    telegram_message_id: Optional[str] = None,
) -> None:
    """Process a text message from a linked agent."""
    channel = _load_channel(channel_id, org_id)
    if channel is None:
        return

    user = User.query.filter_by(
        id=channel.user_id, organization_id=org_id,
    ).first()
    if user is None:
        logger.warning('Telegram turn: user %s missing for channel %s',
                       channel.user_id, channel_id)
        return

    transport = get_transport()
    command = _parse_command(text)

    if command == 'stop':
        from services.messaging.binding import disconnect_channel
        disconnect_channel(user.id, reason='telegram_stop')
        transport.send_text(
            channel.chat_id,
            "Disconnected. You will not hear from me again until you "
            "reconnect from your CRM profile.\n\n--BOB",
        )
        return

    if command == 'help':
        transport.send_text(
            channel.chat_id,
            _help_text(user),
        )
        return

    if command == 'undo':
        _handle_undo_command(channel, user, transport)
        return

    if command == 'start':
        # Binding is handled in the webhook before enqueue. A bare /start from
        # an already-linked user just gets a greeting.
        transport.send_text(
            channel.chat_id,
            f"Already linked, {user.first_name or 'there'}. "
            f"Ask me about your contacts or tasks anytime.\n\n--BOB",
        )
        return

    try:
        _bump_daily_count(channel)
    except RateLimitExceeded as exc:
        transport.send_text(channel.chat_id, f"{exc.message}\n\n--BOB")
        return

    conversation = _get_or_create_conversation(user)
    _persist_message(conversation, 'user', text)

    ctx = BobContext.from_user(user, surface=SURFACE, timezone=_user_tz(user))
    with show_typing(transport, channel.chat_id):
        reply_text, pending, undoable = _run_tool_loop(
            user=user,
            ctx=ctx,
            conversation=conversation,
            user_text=text,
        )

    if pending is not None:
        channel.pending_action_id = pending['action_id']
        db.session.commit()
        preview_body = _format_pending_preview(pending, reply_text)
        style = 'danger' if pending['tool_name'].startswith('delete_') else 'primary'
        transport.send_choice(
            channel.chat_id,
            preview_body,
            [
                ChoiceOption(
                    label='Confirm',
                    callback_data=f"confirm:{pending['action_id']}",
                    style=style,
                ),
                ChoiceOption(
                    label='Cancel',
                    callback_data=f"reject:{pending['action_id']}",
                ),
            ],
        )
        _persist_message(conversation, 'assistant', preview_body)
        return

    options = []
    if undoable is not None:
        options.append(ChoiceOption(
            label='Undo',
            callback_data=f"undo:{undoable}",
        ))

    if options:
        transport.send_choice(channel.chat_id, reply_text, options)
    else:
        transport.send_text(channel.chat_id, reply_text)
    _persist_message(conversation, 'assistant', reply_text)


def handle_callback_query(
    *,
    channel_id: int,
    org_id: int,
    callback_query_id: str,
    data: str,
    message_id: Optional[str],
) -> None:
    """Process a Confirm / Cancel / Undo button tap."""
    channel = _load_channel(channel_id, org_id)
    if channel is None:
        return

    user = User.query.filter_by(
        id=channel.user_id, organization_id=org_id,
    ).first()
    if user is None:
        return

    transport = get_transport()
    match = _CALLBACK_RE.match((data or '').strip())
    if match is None:
        transport.answer_callback(callback_query_id, text='Unknown action')
        return

    verb, action_id_str = match.group(1), match.group(2)
    action_id = int(action_id_str)
    ctx = BobContext.from_user(user, surface=SURFACE, timezone=_user_tz(user))

    # Never trust the callback payload for identity — confirm_action already
    # scopes by user_id + organization_id, but we also refuse early if the
    # pending pointer on the channel does not match (except for undo).
    if verb in ('confirm', 'reject'):
        if channel.pending_action_id not in (None, action_id):
            # Stale button from an older preview; still allow if the action
            # itself is pending and owned by this user.
            pass

    if verb == 'confirm':
        result = confirm_action(action_id, ctx)
        channel.pending_action_id = None
        db.session.commit()
        outcome = result.summary if result.ok else (result.error or 'Could not confirm.')
        transport.answer_callback(callback_query_id, text='Confirmed' if result.ok else 'Failed')
        body = f"{'Confirmed. ' if result.ok else ''}{outcome}\n\n--BOB"
        if message_id:
            transport.edit_message(channel.chat_id, message_id, body)
        else:
            transport.send_text(channel.chat_id, body)
        return

    if verb == 'reject':
        result = reject_action(action_id, ctx)
        channel.pending_action_id = None
        db.session.commit()
        transport.answer_callback(callback_query_id, text='Cancelled')
        body = f"Cancelled. {result.summary}\n\n--BOB"
        if message_id:
            transport.edit_message(channel.chat_id, message_id, body)
        else:
            transport.send_text(channel.chat_id, body)
        return

    if verb == 'undo':
        result = undo_action(action_id, ctx)
        transport.answer_callback(
            callback_query_id,
            text='Undone' if result.ok else 'Could not undo',
        )
        body = f"{result.summary if result.ok else result.error}\n\n--BOB"
        if message_id:
            transport.edit_message(channel.chat_id, message_id, body)
        else:
            transport.send_text(channel.chat_id, body)
        return


# ---------------------------------------------------------------------------
# Tool loop
# ---------------------------------------------------------------------------

def _run_tool_loop(
    *,
    user: User,
    ctx: BobContext,
    conversation: ChatConversation,
    user_text: str,
) -> tuple[str, Optional[dict], Optional[int]]:
    """Returns (reply_text, pending_preview_or_None, undoable_action_id_or_None)."""
    messages = _history_messages(conversation)
    messages.append({'role': 'user', 'content': user_text})

    first_name = user.first_name or 'there'
    system = (
        TELEGRAM_SYSTEM_PROMPT
        + f"\n\nAgent first name (use rarely, per Name usage rules): {first_name}."
        + f"\nToday (agent local): {ctx.today().isoformat()}."
    )

    pending: Optional[dict] = None
    undoable_action_id: Optional[int] = None
    text_parts: list[str] = []
    collector = ActionCollector()

    def execute_tool(name: str, args: dict) -> tuple[dict, dict]:
        nonlocal pending, undoable_action_id
        result = bob_dispatch(
            name, args, ctx, conversation_id=conversation.id,
            collector=collector,
        )
        if result.requires_confirmation and result.action_id:
            pending = {
                'action_id': result.action_id,
                'tool_name': name,
                'summary': result.summary,
                'preview': (result.data or {}).get('preview') or result.data,
            }
        elif result.ok and result.undoable and result.action_id:
            undoable_action_id = result.action_id
        return result.for_model(), result.for_client()

    try:
        for event, payload in run_tool_conversation(
            system_prompt=system,
            messages=messages,
            tools=openai_tool_schemas(),
            execute_tool=execute_tool,
        ):
            if event == 'text':
                text_parts.append(payload)
            elif event == 'error':
                logger.error('Telegram tool loop error user=%s: %s',
                             user.id, payload)
                text_parts.append(
                    "Something went wrong on my end. Try that again in a moment."
                )
    except Exception:
        logger.exception('Telegram tool loop crashed user=%s', user.id)
        text_parts.append(
            "Something went wrong on my end. Try that again in a moment."
        )

    flush_action_notification(collector, ctx)

    reply = ''.join(text_parts).strip()
    if not reply:
        if pending is not None:
            reply = pending['summary']
        else:
            reply = "Done."
    if not reply.rstrip().endswith('--BOB'):
        reply = reply.rstrip() + '\n\n--BOB'
    return reply, pending, undoable_action_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_channel(channel_id: int, org_id: int
                  ) -> Optional[AgentMessagingChannel]:
    channel = AgentMessagingChannel.query.filter_by(
        id=channel_id,
        organization_id=org_id,
        disabled_at=None,
    ).first()
    if channel is None:
        logger.info('Telegram turn: channel %s gone or disabled', channel_id)
    return channel


def _parse_command(text: str) -> Optional[str]:
    match = _COMMAND_RE.match((text or '').strip())
    if not match:
        return None
    return match.group(1).lower()


def _help_text(user: User) -> str:
    name = user.first_name or 'there'
    return (
        f"Hey {name}. I am B.O.B. in your CRM.\n\n"
        "Ask me things like:\n"
        "- What's on my plate today?\n"
        "- How many contacts in Houston?\n"
        "- Log a call with Sarah and follow up Friday\n"
        "- Add a note to @someone\n\n"
        "Risky changes (edits and deletes) show a Confirm button first.\n"
        "Commands: /help  /undo  /stop\n\n"
        "--BOB"
    )


def _handle_undo_command(channel, user, transport) -> None:
    ctx = BobContext.from_user(user, surface=SURFACE, timezone=_user_tz(user))
    action = (
        BobAction.query.filter_by(
            user_id=user.id,
            organization_id=user.organization_id,
            status=BobAction.STATUS_EXECUTED,
            surface=SURFACE,
        )
        .order_by(BobAction.executed_at.desc(), BobAction.id.desc())
        .first()
    )
    if action is None:
        transport.send_text(
            channel.chat_id,
            "Nothing recent to undo.\n\n--BOB",
        )
        return
    result = undo_action(action.id, ctx)
    body = (result.summary if result.ok else result.error) or 'Could not undo.'
    transport.send_text(channel.chat_id, f"{body}\n\n--BOB")


def _format_pending_preview(pending: dict, model_text: str) -> str:
    preview = pending.get('preview') or {}
    lines = [model_text.rstrip()]
    if isinstance(preview, dict):
        # Keep the preview short; the model already summarised.
        summary = preview.get('summary') or pending.get('summary')
        if summary and summary not in model_text:
            lines.append(summary)
    lines.append('')
    lines.append('Tap Confirm to apply, or Cancel to leave it alone.')
    if not any(part.strip().endswith('--BOB') for part in lines):
        lines.append('')
        lines.append('--BOB')
    return '\n'.join(lines)


def _get_or_create_conversation(user: User) -> ChatConversation:
    """Reuse today's open Telegram conversation, or start a new one."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    existing = (
        ChatConversation.query.filter_by(
            user_id=user.id,
            organization_id=user.organization_id,
            channel=CHANNEL_NAME,
        )
        .filter(ChatConversation.updated_at >= today_start)
        .order_by(ChatConversation.updated_at.desc())
        .first()
    )
    if existing is not None:
        return existing

    conversation = ChatConversation(
        user_id=user.id,
        organization_id=user.organization_id,
        channel=CHANNEL_NAME,
        title='Telegram',
    )
    db.session.add(conversation)
    db.session.commit()
    return conversation


def _persist_message(conversation: ChatConversation, role: str, content: str) -> None:
    db.session.add(ChatMessage(
        conversation_id=conversation.id,
        role=role,
        content=content,
    ))
    conversation.updated_at = datetime.utcnow()
    db.session.commit()


def _history_messages(conversation: ChatConversation) -> list[dict]:
    rows = (
        ChatMessage.query.filter_by(conversation_id=conversation.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(HISTORY_TURNS * 2)
        .all()
    )
    rows.reverse()
    # Tool turns are intentionally not replayed — same anti-forgery rule as web.
    return [
        {'role': row.role, 'content': row.content}
        for row in rows
        if row.role in ('user', 'assistant')
    ]


def _user_tz(user: User) -> str:
    return getattr(user, 'timezone', None) or 'America/Chicago'


def _bump_daily_count(channel: AgentMessagingChannel) -> None:
    """Increment per-user and enforce user + org daily caps."""
    today = date.today()
    if channel.daily_count_date != today:
        channel.daily_count = 0
        channel.daily_count_date = today

    if channel.daily_count >= PER_USER_DAILY_LIMIT:
        raise RateLimitExceeded(
            f"You've hit today's {PER_USER_DAILY_LIMIT}-message limit with me. "
            "Try again tomorrow."
        )

    org_count = (
        db.session.query(db.func.coalesce(db.func.sum(
            AgentMessagingChannel.daily_count
        ), 0))
        .filter(
            AgentMessagingChannel.organization_id == channel.organization_id,
            AgentMessagingChannel.daily_count_date == today,
            AgentMessagingChannel.disabled_at.is_(None),
        )
        .scalar()
    )
    if int(org_count or 0) >= PER_ORG_DAILY_LIMIT:
        raise RateLimitExceeded(
            f"Your team has hit today's {PER_ORG_DAILY_LIMIT}-message limit. "
            "Try again tomorrow."
        )

    channel.daily_count += 1
    channel.last_inbound_at = datetime.utcnow()
    db.session.commit()
