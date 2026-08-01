"""Turn handler for B.O.B. over a messaging channel.

Owns history windowing, the keyword / callback pre-pass, the tool loop, and
reply rendering. The webhook never calls into this synchronously — the RQ job
does, after returning 200 to Telegram.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from flask import current_app

from models import (
    AgentMessagingChannel,
    BobAction,
    ChatConversation,
    ChatMessage,
    Contact,
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
from services.messaging.voice import VoiceTranscriptionError, transcribe_telegram_voice

logger = logging.getLogger(__name__)

HISTORY_TURNS = 12  # user+assistant pairs kept in the model window
SURFACE = 'bob_telegram'
CHANNEL_NAME = 'telegram'

# Per-user / per-org daily turn caps. Each inbound turn is a full OpenAI tool
# loop, so these are tighter than Magic Inbox's email limits.
PER_USER_DAILY_LIMIT = 100
PER_ORG_DAILY_LIMIT = 500

# Contact picker: enough options to be useful, few enough for one keyboard row
# wrap without drowning the agent.
MAX_DISAMBIGUATION_OPTIONS = 6

_CALLBACK_RE = re.compile(r'^(confirm|reject|undo):(\d+)$')
_PICK_RE = re.compile(r'^pick:c:(\d+)$')
_CMD_CALLBACK_RE = re.compile(r'^cmd:(today|overdue|help)$')
_COMMAND_RE = re.compile(r'^/([a-zA-Z_]+)(?:@\S+)?(?:\s+(.*))?$')

# Slash commands that expand into a normal user turn (not meta-commands).
COMMAND_PROMPTS = {
    'today': "What's on my plate today?",
    'overdue': 'What tasks am I overdue on?',
}


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
    voice_file_id: Optional[str] = None,
    voice_duration_seconds: Optional[int] = None,
) -> None:
    """Process a text or voice message from a linked agent."""
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
    from_voice = False
    working_text = (text or '').strip()

    if voice_file_id and not working_text:
        try:
            with show_typing(transport, channel.chat_id):
                working_text = transcribe_telegram_voice(
                    transport,
                    voice_file_id,
                    duration_seconds=voice_duration_seconds,
                )
            from_voice = True
        except VoiceTranscriptionError as exc:
            transport.send_text(channel.chat_id, f"{exc.message}\n\n--BOB")
            return
        except Exception:
            logger.exception('Voice transcription crashed channel=%s', channel_id)
            transport.send_text(
                channel.chat_id,
                "Couldn't catch that. Try again or type it.\n\n--BOB",
            )
            return

    if not working_text:
        transport.send_text(
            channel.chat_id,
            "I can take a text message or a voice note.\n\n--BOB",
        )
        return

    command = _parse_command(working_text)

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
        transport.send_text(channel.chat_id, _help_text(user))
        return

    if command == 'undo':
        _handle_undo_command(channel, user, transport)
        return

    if command == 'start':
        # Binding is handled in the webhook before enqueue. A bare /start from
        # an already-linked user just gets a greeting + starters.
        transport.send_choice(
            channel.chat_id,
            f"Already linked, {user.first_name or 'there'}. "
            "Ask me about your contacts or tasks anytime, "
            "or send a voice note.\n\n--BOB",
            _starter_options(),
        )
        return

    if command in COMMAND_PROMPTS:
        working_text = COMMAND_PROMPTS[command]
        from_voice = False

    try:
        _bump_daily_count(channel)
    except RateLimitExceeded as exc:
        transport.send_text(channel.chat_id, f"{exc.message}\n\n--BOB")
        return

    if from_voice:
        # Show what Whisper heard so the agent can catch a bad transcript
        # before confirming a write.
        heard = working_text if len(working_text) <= 280 else working_text[:277] + '...'
        transport.send_text(channel.chat_id, f'Heard: "{heard}"')

    conversation = _get_or_create_conversation(user)
    _persist_message(conversation, 'user', working_text)

    _run_and_reply(
        channel=channel,
        user=user,
        conversation=conversation,
        user_text=working_text,
        transport=transport,
    )


def handle_callback_query(
    *,
    channel_id: int,
    org_id: int,
    callback_query_id: str,
    data: str,
    message_id: Optional[str],
) -> None:
    """Process Confirm / Cancel / Undo / pick / starter button taps."""
    channel = _load_channel(channel_id, org_id)
    if channel is None:
        return

    user = User.query.filter_by(
        id=channel.user_id, organization_id=org_id,
    ).first()
    if user is None:
        return

    transport = get_transport()
    raw = (data or '').strip()

    cmd_match = _CMD_CALLBACK_RE.match(raw)
    if cmd_match is not None:
        transport.answer_callback(callback_query_id, text='On it')
        verb = cmd_match.group(1)
        if verb == 'help':
            if message_id:
                transport.edit_message(
                    channel.chat_id, message_id, _help_text(user),
                )
            else:
                transport.send_text(channel.chat_id, _help_text(user))
            return
        handle_inbound_message(
            channel_id=channel_id,
            org_id=org_id,
            text=f'/{verb}',
        )
        return

    pick_match = _PICK_RE.match(raw)
    if pick_match is not None:
        _handle_pick_contact(
            channel=channel,
            user=user,
            transport=transport,
            callback_query_id=callback_query_id,
            contact_id=int(pick_match.group(1)),
            message_id=message_id,
        )
        return

    match = _CALLBACK_RE.match(raw)
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
        body = f"{'Confirmed. ' if result.ok else ''}{outcome}"
        body = _append_record_links(body, [result.record_url])
        body = _ensure_bob_signoff(body)
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
        body = _ensure_bob_signoff(f"Cancelled. {result.summary}")
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
        body = _ensure_bob_signoff(
            (result.summary if result.ok else result.error) or 'Could not undo.'
        )
        if message_id:
            transport.edit_message(channel.chat_id, message_id, body)
        else:
            transport.send_text(channel.chat_id, body)
        return


# ---------------------------------------------------------------------------
# Tool loop + reply rendering
# ---------------------------------------------------------------------------

def _run_and_reply(
    *,
    channel: AgentMessagingChannel,
    user: User,
    conversation: ChatConversation,
    user_text: str,
    transport: Any,
) -> None:
    ctx = BobContext.from_user(user, surface=SURFACE, timezone=_user_tz(user))
    with show_typing(transport, channel.chat_id):
        reply_text, pending, undoable, disambiguation, record_urls = _run_tool_loop(
            user=user,
            ctx=ctx,
            conversation=conversation,
            user_text=user_text,
        )

    reply_text = _append_record_links(reply_text, record_urls)

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

    options: list[ChoiceOption] = []
    if disambiguation:
        options.extend(_disambiguation_options(disambiguation))
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


def _run_tool_loop(
    *,
    user: User,
    ctx: BobContext,
    conversation: ChatConversation,
    user_text: str,
) -> tuple[str, Optional[dict], Optional[int], Optional[list[dict]], list[str]]:
    """Returns reply, pending, undoable id, disambiguation contacts, record urls."""
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
    disambiguation: Optional[list[dict]] = None
    record_urls: list[str] = []
    text_parts: list[str] = []
    collector = ActionCollector()

    def execute_tool(name: str, args: dict) -> tuple[dict, dict]:
        nonlocal pending, undoable_action_id, disambiguation
        result = bob_dispatch(
            name, args, ctx, conversation_id=conversation.id,
            collector=collector,
        )
        if result.record_url:
            record_urls.append(result.record_url)
        if result.requires_confirmation and result.action_id:
            pending = {
                'action_id': result.action_id,
                'tool_name': name,
                'summary': result.summary,
                'preview': (result.data or {}).get('preview') or result.data,
            }
            if result.record_url:
                pending['record_url'] = result.record_url
        elif result.ok and result.undoable and result.action_id:
            undoable_action_id = result.action_id

        if name == 'search_contacts' and result.ok and pending is None:
            contacts = (result.data or {}).get('contacts') or []
            if 2 <= len(contacts) <= MAX_DISAMBIGUATION_OPTIONS:
                disambiguation = contacts
            else:
                disambiguation = None

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

    # If a write landed, the agent already resolved the contact. Don't also
    # pile on picker buttons.
    if pending is not None or undoable_action_id is not None:
        disambiguation = None

    reply = ''.join(text_parts).strip()
    if not reply:
        if pending is not None:
            reply = pending['summary']
        elif disambiguation:
            reply = 'A few people match. Tap the right one:'
        else:
            reply = "Done."
    reply = _ensure_bob_signoff(reply)
    return reply, pending, undoable_action_id, disambiguation, record_urls


def _handle_pick_contact(
    *,
    channel: AgentMessagingChannel,
    user: User,
    transport: Any,
    callback_query_id: str,
    contact_id: int,
    message_id: Optional[str],
) -> None:
    """Continue the prior turn with a tapped contact_id."""
    contact = Contact.query.filter_by(
        id=contact_id,
        organization_id=user.organization_id,
    ).first()
    if contact is None:
        transport.answer_callback(callback_query_id, text='Not found')
        return

    # Agents only pick among contacts they can already see.
    if contact.user_id != user.id and getattr(user, 'org_role', None) not in (
        'owner', 'admin',
    ):
        transport.answer_callback(callback_query_id, text='Not available')
        return

    name = f'{contact.first_name} {contact.last_name}'.strip()
    transport.answer_callback(callback_query_id, text=name[:200] or 'Selected')
    if message_id:
        try:
            transport.edit_message(
                channel.chat_id,
                message_id,
                f'Using **{name}**.',
            )
        except Exception:
            logger.debug('Could not edit disambiguation message', exc_info=True)

    followup = f'Use contact_id {contact.id} ({name}).'
    try:
        _bump_daily_count(channel)
    except RateLimitExceeded as exc:
        transport.send_text(channel.chat_id, f"{exc.message}\n\n--BOB")
        return

    conversation = _get_or_create_conversation(user)
    _persist_message(conversation, 'user', followup)
    _run_and_reply(
        channel=channel,
        user=user,
        conversation=conversation,
        user_text=followup,
        transport=transport,
    )


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


def _starter_options() -> list[ChoiceOption]:
    return [
        ChoiceOption(label="Today's plate", callback_data='cmd:today'),
        ChoiceOption(label='Overdue', callback_data='cmd:overdue'),
        ChoiceOption(label='Help', callback_data='cmd:help'),
    ]


def _help_text(user: User) -> str:
    name = user.first_name or 'there'
    return (
        f"Hey {name}. I am B.O.B. in your CRM.\n\n"
        "Text me, or send a voice note. I can:\n"
        "- Show today's agenda and overdue tasks\n"
        "- Look up contacts and counts by city or group\n"
        "- Log calls, texts, notes, and follow-ups\n"
        "- Add contacts and create tasks\n\n"
        "Try:\n"
        "- What's on my plate today?\n"
        "- Log a call with Sarah and follow up Friday\n"
        "- How many contacts in Houston?\n\n"
        "Commands: /today  /overdue  /help  /undo  /stop\n"
        "Risky edits and deletes show a Confirm button first.\n"
        "When a few people match a name, tap the right one.\n\n"
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
    transport.send_text(channel.chat_id, _ensure_bob_signoff(body))


def _format_pending_preview(pending: dict, model_text: str) -> str:
    preview = pending.get('preview') or {}
    lines = [model_text.rstrip()]
    if isinstance(preview, dict):
        # Keep the preview short; the model already summarised.
        summary = preview.get('summary') or pending.get('summary')
        if summary and summary not in model_text:
            lines.append(summary)
    record_url = pending.get('record_url')
    if record_url:
        linked = _append_record_links('', [record_url]).strip()
        if linked:
            lines.append(linked)
    lines.append('')
    lines.append('Tap Confirm to apply, or Cancel to leave it alone.')
    return _ensure_bob_signoff('\n'.join(lines))


def _disambiguation_options(contacts: list[dict]) -> list[ChoiceOption]:
    options: list[ChoiceOption] = []
    for contact in contacts[:MAX_DISAMBIGUATION_OPTIONS]:
        contact_id = contact.get('contact_id')
        if contact_id is None:
            continue
        name = (contact.get('name') or f'Contact {contact_id}').strip()
        label = name[:64]
        options.append(ChoiceOption(
            label=label,
            callback_data=f'pick:c:{int(contact_id)}',
        ))
    return options


def _app_base_url() -> str:
    try:
        return (current_app.config.get('APP_BASE_URL') or '').rstrip('/')
    except RuntimeError:
        return ''


def _append_record_links(body: str, record_urls: list[Optional[str]]) -> str:
    base = _app_base_url()
    seen: list[str] = []
    for path in record_urls:
        if not path:
            continue
        full = path if str(path).startswith('http') else f'{base}{path}'
        if not full or full in seen:
            continue
        seen.append(full)
    if not seen:
        return body

    text = (body or '').rstrip()
    if text.endswith('--BOB'):
        text = text[: -len('--BOB')].rstrip()
    link_block = '\n'.join(f'Open: {url}' for url in seen)
    if link_block not in text:
        text = f'{text}\n\n{link_block}' if text else link_block
    return _ensure_bob_signoff(text)


def _ensure_bob_signoff(body: str) -> str:
    text = (body or '').rstrip()
    if not text:
        return '--BOB'
    if text.endswith('--BOB'):
        return text
    return text + '\n\n--BOB'


def _get_or_create_conversation(user: User) -> ChatConversation:
    """Reuse today's open Telegram conversation (agent-local day), or start one."""
    day_start = _local_day_start_utc(user)
    existing = (
        ChatConversation.query.filter_by(
            user_id=user.id,
            organization_id=user.organization_id,
            channel=CHANNEL_NAME,
        )
        .filter(ChatConversation.updated_at >= day_start)
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


def _local_day_start_utc(user: User) -> datetime:
    """UTC datetime for midnight at the start of the agent's local calendar day."""
    tz_name = _user_tz(user)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo('America/Chicago')
    local_now = datetime.now(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


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
