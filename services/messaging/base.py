"""Provider-agnostic messaging surface for B.O.B.

Two verbs plus an edit. Telegram renders ``send_choice`` as an inline keyboard;
a future SMS adapter would render it as "reply 1 or 2". Everything above this
layer (conversation handler, binding, proactive notify) talks only to this
interface so a second channel is a new file, not a rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence


@dataclass(frozen=True)
class ChoiceOption:
    """One tappable / replyable option under a message."""

    label: str
    callback_data: str
    style: Optional[str] = None  # 'danger' | 'success' | 'primary' | None


class MessagingTransport(Protocol):
    """What every channel adapter must implement."""

    def send_text(
        self,
        chat_id: str,
        body: str,
        *,
        parse_mode: Optional[str] = None,
    ) -> dict:
        """Send a plain (or lightly formatted) text message. Returns provider ids."""
        ...

    def send_choice(
        self,
        chat_id: str,
        body: str,
        options: Sequence[ChoiceOption],
        *,
        parse_mode: Optional[str] = None,
    ) -> dict:
        """Send text with a choice UI (inline keyboard, numbered reply, etc.)."""
        ...

    def edit_message(
        self,
        chat_id: str,
        message_id: str,
        body: str,
        *,
        parse_mode: Optional[str] = None,
        clear_choices: bool = True,
    ) -> dict:
        """Replace a previously sent message (used after Confirm/Cancel)."""
        ...

    def answer_callback(
        self,
        callback_query_id: str,
        *,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> dict:
        """Acknowledge a button tap so the client stops spinning."""
        ...

    def send_activity(self, chat_id: str) -> dict:
        """Signal that a reply is being composed (typing dots, etc.).

        Best-effort and cosmetic. Channels without the concept may no-op.
        """
        ...
