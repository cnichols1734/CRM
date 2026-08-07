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
from services.messaging.photo_contacts import (
    PhotoContactError,
    confirm_photo_contacts,
    create_contacts_now,
    create_pending_photo_action,
    download_telegram_photo,
    extract_contact_candidates,
    format_candidate_preview,
    format_saved_contacts,
    is_photo_pending_action,
    wants_create_contact,
)
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
    photo_file_id: Optional[str] = None,
    document_file_id: Optional[str] = None,
    document_filename: Optional[str] = None,
    document_mime_type: Optional[str] = None,
) -> None:
    """Process a text, voice, photo, or PDF message from a linked agent."""
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

    if document_file_id:
        _handle_pdf_document_message(
            channel=channel,
            user=user,
            transport=transport,
            document_file_id=document_file_id,
            filename=document_filename or 'telegram.pdf',
            mime_type=document_mime_type or 'application/pdf',
            caption=working_text,
        )
        return

    if photo_file_id:
        _handle_photo_message(
            channel=channel,
            user=user,
            transport=transport,
            photo_file_id=photo_file_id,
            caption=working_text,
        )
        return

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
    ctx = _bob_context_for_channel(user, channel)

    # Never trust the callback payload for identity — confirm_action already
    # scopes by user_id + organization_id, but we also refuse early if the
    # pending pointer on the channel does not match (except for undo).
    if verb in ('confirm', 'reject'):
        if channel.pending_action_id not in (None, action_id):
            # Stale button from an older preview; still allow if the action
            # itself is pending and owned by this user.
            pass

    if verb == 'confirm':
        action = BobAction.query.filter_by(
            id=action_id,
            organization_id=user.organization_id,
            user_id=user.id,
        ).first()
        undoable = None
        record_urls: list[str] = []
        if action is not None and is_photo_pending_action(action):
            result, created = confirm_photo_contacts(action, ctx)
            channel.pending_action_id = None
            db.session.commit()
            transport.answer_callback(
                callback_query_id,
                text='Saved' if result.ok else 'Failed',
            )
            if result.ok and created:
                body = format_saved_contacts(created)
                undoable = result.action_id
                record_urls = [
                    f'/contact/{c.id}' for c in created
                ]
            else:
                body = result.summary if result.ok else (
                    result.error or 'Could not confirm.'
                )
        else:
            result = confirm_action(action_id, ctx)
            channel.pending_action_id = None
            db.session.commit()
            transport.answer_callback(
                callback_query_id,
                text='Confirmed' if result.ok else 'Failed',
            )
            if result.ok and (result.data or {}).get('created'):
                body = _format_create_confirm_body(result)
                undoable = result.action_id if result.undoable else None
            else:
                body = (
                    f"{'Confirmed. ' if result.ok else ''}"
                    f"{result.summary if result.ok else (result.error or 'Could not confirm.')}"
                )
            if result.record_url:
                record_urls = [result.record_url]

        body = _append_record_links(body, record_urls)
        body = _ensure_bob_signoff(body)
        options = []
        if undoable is not None:
            options.append(ChoiceOption(
                label='Undo', callback_data=f'undo:{undoable}',
            ))
        if message_id:
            # editMessageText cannot add a new keyboard reliably after clear;
            # edit the body, then send Undo as a follow-up if needed.
            transport.edit_message(channel.chat_id, message_id, body)
            if options:
                transport.send_choice(
                    channel.chat_id, 'Changed your mind?', options,
                )
        elif options:
            transport.send_choice(channel.chat_id, body, options)
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

def _bob_context_for_channel(user: User, channel: AgentMessagingChannel) -> BobContext:
    """Build Telegram BobContext with durable selected_transaction_id."""
    return BobContext.from_user(
        user,
        surface=SURFACE,
        timezone=_user_tz(user),
        selected_transaction_id=getattr(channel, 'selected_transaction_id', None),
    )


def _run_and_reply(
    *,
    channel: AgentMessagingChannel,
    user: User,
    conversation: ChatConversation,
    user_text: str,
    transport: Any,
) -> None:
    ctx = _bob_context_for_channel(user, channel)
    with show_typing(transport, channel.chat_id):
        reply_text, pending, undoable, disambiguation, record_urls = _run_tool_loop(
            user=user,
            ctx=ctx,
            conversation=conversation,
            user_text=user_text,
            channel=channel,
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
    channel: Optional[AgentMessagingChannel] = None,
) -> tuple[str, Optional[dict], Optional[int], Optional[list[dict]], list[str]]:
    """Returns reply, pending, undoable id, disambiguation contacts, record urls."""
    messages = _history_messages(conversation)
    messages.append({'role': 'user', 'content': user_text})

    first_name = user.first_name or 'there'
    selected_note = ''
    if ctx.selected_transaction_id:
        selected_note = (
            f"\nSelected transaction_id for this chat: {ctx.selected_transaction_id}."
            " Use it for status/deadline questions unless the agent picks another."
        )
    system = (
        TELEGRAM_SYSTEM_PROMPT
        + f"\n\nAgent first name (use rarely, per Name usage rules): {first_name}."
        + f"\nToday (agent local): {ctx.today().isoformat()}."
        + selected_note
    )

    pending: Optional[dict] = None
    undoable_action_id: Optional[int] = None
    disambiguation: Optional[list[dict]] = None
    record_urls: list[str] = []
    created_saved: list[dict] = []
    text_parts: list[str] = []
    collector = ActionCollector()

    def execute_tool(name: str, args: dict) -> tuple[dict, dict]:
        nonlocal pending, undoable_action_id, disambiguation, ctx
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

        if (
            name == 'create_contact'
            and result.ok
            and (result.data or {}).get('created')
            and isinstance((result.data or {}).get('saved'), dict)
        ):
            created_saved.append(result.data['saved'])

        if name == 'search_contacts' and result.ok and pending is None:
            contacts = (result.data or {}).get('contacts') or []
            if 2 <= len(contacts) <= MAX_DISAMBIGUATION_OPTIONS:
                disambiguation = contacts
            else:
                disambiguation = None

        # Durable transaction disambiguation for Telegram status questions.
        if name == 'select_transaction_context' and result.ok and channel is not None:
            selected_id = (result.data or {}).get('selected_transaction_id')
            if selected_id:
                channel.selected_transaction_id = int(selected_id)
                db.session.commit()
                ctx = BobContext.from_user(
                    user,
                    surface=SURFACE,
                    timezone=_user_tz(user),
                    selected_transaction_id=int(selected_id),
                )

        return result.for_model(), result.for_client()

    try:
        for event, payload in run_tool_conversation(
            system_prompt=system,
            messages=messages,
            tools=openai_tool_schemas(ctx),
            execute_tool=execute_tool,
            safety_identifier=f'org:{ctx.organization_id}:user:{ctx.user_id}',
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

    if created_saved and pending is None:
        from services.messaging.photo_contacts import display_name, format_fields
        blocks = []
        for saved in created_saved:
            name = display_name(saved)
            fields = format_fields(saved, skip_name=True)
            block = f'**{name}**'
            if fields:
                block += f'\n{fields}'
            blocks.append(block)
        saved_block = 'Saved to your CRM:\n\n' + '\n\n'.join(blocks)
        # Prefer the structured dump over a vague model summary.
        if 'Saved to your CRM' not in reply:
            reply = saved_block

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


def _handle_pdf_document_message(
    *,
    channel: AgentMessagingChannel,
    user: User,
    transport: Any,
    document_file_id: str,
    filename: str,
    mime_type: str,
    caption: str,
) -> None:
    """Route a PDF into bootstrap/document-review after transaction selection."""
    from models import Transaction
    from services.messaging.telegram_documents import (
        TelegramDocumentError,
        download_telegram_document,
        format_intake_reply,
        process_telegram_pdf_for_transaction,
    )

    try:
        _bump_daily_count(channel)
    except RateLimitExceeded as exc:
        transport.send_text(channel.chat_id, f"{exc.message}\n\n--BOB")
        return

    conversation = _get_or_create_conversation(user)
    user_line = caption.strip() if caption.strip() else f'[pdf:{filename}]'
    _persist_message(conversation, 'user', user_line)

    selected_id = getattr(channel, 'selected_transaction_id', None)
    if not selected_id:
        body = _ensure_bob_signoff(
            "Select a transaction first (search + select_transaction_context), "
            "then send the PDF. I won't attach deal docs without a selected file."
        )
        transport.send_text(channel.chat_id, body)
        _persist_message(conversation, 'assistant', body)
        return

    tx = Transaction.query.filter_by(
        id=selected_id,
        organization_id=channel.organization_id,
    ).first()
    if tx is None:
        body = _ensure_bob_signoff(
            "The selected transaction is gone. Search and select again, then resend the PDF."
        )
        transport.send_text(channel.chat_id, body)
        _persist_message(conversation, 'assistant', body)
        return

    try:
        with show_typing(transport, channel.chat_id):
            pdf_bytes = download_telegram_document(transport, document_file_id)
            session = process_telegram_pdf_for_transaction(
                user=user,
                transaction=tx,
                file_bytes=pdf_bytes,
                filename=filename,
                mime_type=mime_type,
                run_extraction=True,
            )
            body = format_intake_reply(session, tx)
    except TelegramDocumentError as exc:
        body = _ensure_bob_signoff(exc.message)
        transport.send_text(channel.chat_id, body)
        _persist_message(conversation, 'assistant', body)
        return
    except Exception:
        logger.exception(
            'Telegram PDF intake crashed channel=%s tx=%s',
            channel.id, selected_id,
        )
        body = _ensure_bob_signoff(
            "Couldn't process that PDF. Try again, or upload it in the CRM bootstrap inbox."
        )
        transport.send_text(channel.chat_id, body)
        _persist_message(conversation, 'assistant', body)
        return

    transport.send_text(channel.chat_id, body)
    _persist_message(conversation, 'assistant', body)


def _handle_photo_message(
    *,
    channel: AgentMessagingChannel,
    user: User,
    transport: Any,
    photo_file_id: str,
    caption: str,
) -> None:
    """Extract contact info from a photo (Magic Inbox vision path)."""
    try:
        _bump_daily_count(channel)
    except RateLimitExceeded as exc:
        transport.send_text(channel.chat_id, f"{exc.message}\n\n--BOB")
        return

    conversation = _get_or_create_conversation(user)
    user_line = caption.strip() if caption.strip() else '[photo]'
    _persist_message(conversation, 'user', user_line)

    ctx = _bob_context_for_channel(user, channel)
    try:
        with show_typing(transport, channel.chat_id):
            image_bytes = download_telegram_photo(transport, photo_file_id)
            candidates = extract_contact_candidates(
                image_bytes, caption=caption, user=user,
            )
    except PhotoContactError as exc:
        body = _ensure_bob_signoff(exc.message)
        transport.send_text(channel.chat_id, body)
        _persist_message(conversation, 'assistant', body)
        return
    except Exception:
        logger.exception('Telegram photo turn crashed channel=%s', channel.id)
        body = _ensure_bob_signoff(
            "Couldn't read that photo. Try again, or type the contact details."
        )
        transport.send_text(channel.chat_id, body)
        _persist_message(conversation, 'assistant', body)
        return

    if not candidates:
        body = _ensure_bob_signoff(
            "I didn't find usable contact info in that photo. "
            "Try a clearer shot of the card or email, or type the details."
        )
        transport.send_text(channel.chat_id, body)
        _persist_message(conversation, 'assistant', body)
        return

    if wants_create_contact(caption):
        with show_typing(transport, channel.chat_id):
            body, created, undoable, record_urls = create_contacts_now(
                ctx=ctx,
                candidates=candidates,
                conversation_id=conversation.id,
            )
        body = _append_record_links(body, record_urls)
        body = _ensure_bob_signoff(body)
        options = []
        if undoable is not None:
            options.append(ChoiceOption(
                label='Undo', callback_data=f'undo:{undoable}',
            ))
        if options:
            transport.send_choice(channel.chat_id, body, options)
        else:
            transport.send_text(channel.chat_id, body)
        _persist_message(conversation, 'assistant', body)
        return

    # Ask before creating — same Confirm/Cancel pattern as risky writes.
    preview = format_candidate_preview(candidates)
    action = create_pending_photo_action(
        ctx=ctx,
        candidates=candidates,
        conversation_id=conversation.id,
    )
    channel.pending_action_id = action.id
    db.session.commit()
    body = _ensure_bob_signoff(preview)
    transport.send_choice(
        channel.chat_id,
        body,
        [
            ChoiceOption(
                label='Create contact' if len(candidates) == 1 else 'Create all',
                callback_data=f'confirm:{action.id}',
                style='primary',
            ),
            ChoiceOption(
                label='Cancel',
                callback_data=f'reject:{action.id}',
            ),
        ],
    )
    _persist_message(conversation, 'assistant', body)


def _format_create_confirm_body(result) -> str:
    """Prefer the full saved field list after a contact create confirm."""
    saved = (result.data or {}).get('saved')
    if isinstance(saved, dict) and saved:
        from services.messaging.photo_contacts import display_name, format_fields
        name = display_name(saved)
        fields = format_fields(saved, skip_name=True)
        body = f'Saved to your CRM:\n\n**{name}**'
        if fields:
            body += f'\n{fields}'
        return body

    contact_id = None
    contact_data = (result.data or {}).get('contact') or {}
    if isinstance(contact_data, dict):
        contact_id = contact_data.get('contact_id')
    contact_id = contact_id or (result.data or {}).get('undo_target_id')
    if contact_id:
        contact = db.session.get(Contact, int(contact_id))
        if contact is not None:
            return format_saved_contacts([contact])
    return result.summary or 'Saved.'


def _help_text(user: User) -> str:
    name = user.first_name or 'there'
    return (
        f"Hey {name}. I am B.O.B. in your CRM.\n\n"
        "Text me, send a voice note, or photo a business card / email. I can:\n"
        "- Show today's agenda and overdue tasks\n"
        "- Look up contacts and counts by city or group\n"
        "- Log calls, texts, notes, and follow-ups\n"
        "- Add contacts (including from photos) and create tasks\n\n"
        "Try:\n"
        "- What's on my plate today?\n"
        "- Log a call with Sarah and follow up Friday\n"
        "- Photo a card with caption: create this contact\n\n"
        "Commands: /today  /overdue  /help  /undo  /stop\n"
        "Risky edits and deletes show a Confirm button first.\n"
        "When a few people match a name, tap the right one.\n\n"
        "--BOB"
    )


def _handle_undo_command(channel, user, transport) -> None:
    ctx = _bob_context_for_channel(user, channel)
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
