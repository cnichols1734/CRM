"""Telegram Bot API transport for B.O.B.

Plain HTTPS JSON via ``requests`` — no async framework. The Bot API is small
enough that a thin wrapper beats pulling in ``python-telegram-bot``.
"""
from __future__ import annotations

import html
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence
from urllib.parse import urljoin

import requests

from services.messaging.base import ChoiceOption

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = 'https://api.telegram.org'
MAX_MESSAGE_LENGTH = 4096
# Soft cap so a chatty model reply still fits after HTML wrapping.
SOFT_CHUNK_LENGTH = 3800

# Back off on Telegram's own rate limits. The per-chat floor is ~1 msg/sec.
MAX_RETRIES = 3

# Telegram drops the typing indicator after ~5s, so refresh under that while
# the model works. Give up after a bounded run so a wedged turn cannot leave
# the dots spinning forever.
TYPING_REFRESH_SECONDS = 4
MAX_TYPING_SECONDS = 90


class TelegramError(RuntimeError):
    """Raised when the Bot API returns a non-ok response after retries."""

    def __init__(self, method: str, description: str, *, status_code: int = 0,
                 retry_after: Optional[int] = None):
        super().__init__(f'Telegram {method} failed: {description}')
        self.method = method
        self.description = description
        self.status_code = status_code
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
_ITALIC_RE = re.compile(r'(?<!\*)\*([^*]+?)\*(?!\*)')
_CODE_RE = re.compile(r'`([^`]+)`')
_HEADER_RE = re.compile(r'^#{1,6}\s+(.*)$', re.MULTILINE)


def markdown_to_telegram_html(text: str) -> str:
    """Convert the light markdown B.O.B. produces into Telegram HTML.

    Telegram's MarkdownV2 escaping is a reliable source of malformed-entity
    errors, so we escape first and then re-apply a small set of tags. Anything
    we don't understand stays as escaped plain text.
    """
    if not text:
        return ''

    # Protect fenced / inline code before escaping, so contents stay literal.
    code_slots: list[str] = []

    def _stash_code(match: re.Match) -> str:
        code_slots.append(html.escape(match.group(1)))
        return f'\x00CODE{len(code_slots) - 1}\x00'

    working = _CODE_RE.sub(_stash_code, text)
    working = html.escape(working)
    working = _HEADER_RE.sub(r'<b>\1</b>', working)
    working = _BOLD_RE.sub(r'<b>\1</b>', working)
    working = _ITALIC_RE.sub(r'<i>\1</i>', working)

    for i, slot in enumerate(code_slots):
        working = working.replace(f'\x00CODE{i}\x00', f'<code>{slot}</code>')

    return working


def chunk_text(text: str, limit: int = SOFT_CHUNK_LENGTH) -> list[str]:
    """Split a long reply on paragraph boundaries so each piece fits Telegram."""
    if not text:
        return ['']
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind('\n\n', 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind('\n', 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


# ---------------------------------------------------------------------------
# Live transport
# ---------------------------------------------------------------------------

class TelegramTransport:
    """Sends and edits messages through the Bot API."""

    def __init__(self, bot_token: str, *, session: Optional[requests.Session] = None):
        if not bot_token:
            raise ValueError('TELEGRAM_BOT_TOKEN is required')
        self.bot_token = bot_token
        self._session = session or requests.Session()
        self._base = f'{TELEGRAM_API_BASE}/bot{bot_token}/'

    def send_text(
        self,
        chat_id: str,
        body: str,
        *,
        parse_mode: Optional[str] = 'HTML',
    ) -> dict:
        last: dict = {}
        for piece in chunk_text(body):
            payload: dict[str, Any] = {
                'chat_id': chat_id,
                'text': piece if parse_mode != 'HTML' else (
                    piece if '<' in piece else markdown_to_telegram_html(piece)
                ),
            }
            if parse_mode:
                payload['parse_mode'] = parse_mode
            last = self._call('sendMessage', payload)
        return last

    def send_choice(
        self,
        chat_id: str,
        body: str,
        options: Sequence[ChoiceOption],
        *,
        parse_mode: Optional[str] = 'HTML',
    ) -> dict:
        text = body if (parse_mode != 'HTML' or '<' in body) else markdown_to_telegram_html(body)
        # Telegram caps callback_data at 64 bytes. Keep one row of buttons.
        keyboard = [[_button_dict(opt) for opt in options]]
        payload: dict[str, Any] = {
            'chat_id': chat_id,
            'text': text,
            'reply_markup': {'inline_keyboard': keyboard},
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode
        return self._call('sendMessage', payload)

    def edit_message(
        self,
        chat_id: str,
        message_id: str,
        body: str,
        *,
        parse_mode: Optional[str] = 'HTML',
        clear_choices: bool = True,
    ) -> dict:
        text = body if (parse_mode != 'HTML' or '<' in body) else markdown_to_telegram_html(body)
        payload: dict[str, Any] = {
            'chat_id': chat_id,
            'message_id': int(message_id),
            'text': text,
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode
        if clear_choices:
            payload['reply_markup'] = {'inline_keyboard': []}
        return self._call('editMessageText', payload)

    def answer_callback(
        self,
        callback_query_id: str,
        *,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> dict:
        payload: dict[str, Any] = {
            'callback_query_id': callback_query_id,
            'show_alert': show_alert,
        }
        if text:
            payload['text'] = text[:200]
        return self._call('answerCallbackQuery', payload)

    def send_activity(self, chat_id: str) -> dict:
        return self._call('sendChatAction', {
            'chat_id': chat_id,
            'action': 'typing',
        })

    def set_webhook(
        self,
        url: str,
        *,
        secret_token: str,
        allowed_updates: Optional[Sequence[str]] = None,
        drop_pending_updates: bool = False,
    ) -> dict:
        payload: dict[str, Any] = {
            'url': url,
            'secret_token': secret_token,
            'max_connections': 40,
            'drop_pending_updates': drop_pending_updates,
        }
        if allowed_updates is not None:
            payload['allowed_updates'] = list(allowed_updates)
        else:
            payload['allowed_updates'] = ['message', 'callback_query']
        return self._call('setWebhook', payload)

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict:
        return self._call('deleteWebhook', {
            'drop_pending_updates': drop_pending_updates,
        })

    def get_me(self) -> dict:
        return self._call('getMe', {})

    def _call(self, method: str, payload: dict) -> dict:
        url = urljoin(self._base, method)
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.post(url, json=payload, timeout=15)
            except requests.RequestException as exc:
                if attempt >= MAX_RETRIES:
                    raise TelegramError(method, str(exc)) from exc
                time.sleep(0.5 * attempt)
                continue

            if response.status_code == 429:
                retry_after = _retry_after(response)
                logger.warning(
                    'Telegram rate limited on %s; sleeping %ss (attempt %s)',
                    method, retry_after, attempt,
                )
                if attempt >= MAX_RETRIES:
                    raise TelegramError(
                        method, 'rate limited',
                        status_code=429, retry_after=retry_after,
                    )
                time.sleep(retry_after)
                continue

            try:
                data = response.json()
            except ValueError as exc:
                raise TelegramError(
                    method, f'non-JSON response ({response.status_code})',
                    status_code=response.status_code,
                ) from exc

            if not data.get('ok'):
                description = data.get('description') or 'unknown error'
                parameters = data.get('parameters') or {}
                retry_after = parameters.get('retry_after')
                if retry_after and attempt < MAX_RETRIES:
                    time.sleep(int(retry_after))
                    continue
                raise TelegramError(
                    method, description,
                    status_code=response.status_code,
                    retry_after=retry_after,
                )
            return data.get('result') or {}

        raise TelegramError(method, 'exhausted retries')


def _button_dict(option: ChoiceOption) -> dict:
    button: dict[str, Any] = {
        'text': option.label[:64],
        'callback_data': option.callback_data[:64],
    }
    if option.style in ('danger', 'success', 'primary'):
        button['style'] = option.style
    return button


def _retry_after(response: requests.Response) -> int:
    try:
        data = response.json()
        value = (data.get('parameters') or {}).get('retry_after')
        if value is not None:
            return max(1, int(value))
    except (ValueError, TypeError, AttributeError):
        pass
    header = response.headers.get('Retry-After')
    if header:
        try:
            return max(1, int(header))
        except ValueError:
            pass
    return 1


# ---------------------------------------------------------------------------
# Fake transport for tests
# ---------------------------------------------------------------------------

@dataclass
class FakeTransport:
    """In-memory stand-in that records every call for assertions."""

    sent: list[dict] = field(default_factory=list)
    edited: list[dict] = field(default_factory=list)
    answered: list[dict] = field(default_factory=list)
    activity: list[dict] = field(default_factory=list)
    _next_message_id: int = 1

    def send_text(
        self,
        chat_id: str,
        body: str,
        *,
        parse_mode: Optional[str] = 'HTML',
    ) -> dict:
        message_id = self._next_message_id
        self._next_message_id += 1
        record = {
            'chat_id': str(chat_id),
            'text': body,
            'parse_mode': parse_mode,
            'message_id': message_id,
        }
        self.sent.append(record)
        return {'message_id': message_id, 'chat': {'id': chat_id}}

    def send_choice(
        self,
        chat_id: str,
        body: str,
        options: Sequence[ChoiceOption],
        *,
        parse_mode: Optional[str] = 'HTML',
    ) -> dict:
        message_id = self._next_message_id
        self._next_message_id += 1
        record = {
            'chat_id': str(chat_id),
            'text': body,
            'parse_mode': parse_mode,
            'options': list(options),
            'message_id': message_id,
        }
        self.sent.append(record)
        return {'message_id': message_id, 'chat': {'id': chat_id}}

    def edit_message(
        self,
        chat_id: str,
        message_id: str,
        body: str,
        *,
        parse_mode: Optional[str] = 'HTML',
        clear_choices: bool = True,
    ) -> dict:
        record = {
            'chat_id': str(chat_id),
            'message_id': str(message_id),
            'text': body,
            'parse_mode': parse_mode,
            'clear_choices': clear_choices,
        }
        self.edited.append(record)
        return {'message_id': int(message_id), 'chat': {'id': chat_id}}

    def answer_callback(
        self,
        callback_query_id: str,
        *,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> dict:
        record = {
            'callback_query_id': callback_query_id,
            'text': text,
            'show_alert': show_alert,
        }
        self.answered.append(record)
        return {'ok': True}

    def send_activity(self, chat_id: str) -> dict:
        self.activity.append({'chat_id': str(chat_id)})
        return {'ok': True}


@contextmanager
def show_typing(transport: Any, chat_id: str):
    """Keep the typing indicator alive for the duration of the block.

    Purely cosmetic, so every failure is swallowed — a dropped indicator must
    never cost the agent their answer. The refresh runs on a daemon thread and
    is stopped before the caller sends anything, so the two never share the
    HTTP session concurrently.
    """
    stop = threading.Event()

    def refresh():
        deadline = time.monotonic() + MAX_TYPING_SECONDS
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                transport.send_activity(chat_id)
            except Exception as exc:  # noqa: BLE001 - cosmetic only
                logger.debug('Telegram typing indicator stopped: %s', exc)
                return
            stop.wait(TYPING_REFRESH_SECONDS)

    thread = threading.Thread(
        target=refresh, name='telegram-typing', daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


_transport_override: Optional[Any] = None


def get_transport() -> Any:
    """Return the live Telegram transport, or a test override if set."""
    if _transport_override is not None:
        return _transport_override
    from flask import current_app
    token = current_app.config.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise TelegramError('get_transport', 'TELEGRAM_BOT_TOKEN is not configured')
    return TelegramTransport(token)


def set_transport_override(transport: Optional[Any]) -> None:
    """Install a FakeTransport (or None to clear) for the current process."""
    global _transport_override
    _transport_override = transport
