"""Transport-agnostic messaging for B.O.B. outside the web chat.

Telegram is the first provider. SMS and WhatsApp plug in later by implementing
the same interface in ``base.py`` and adding a ``provider`` value on
``AgentMessagingChannel``.
"""
from services.messaging.base import ChoiceOption, MessagingTransport
from services.messaging.telegram import (
    FakeTransport,
    TelegramTransport,
    get_transport,
    markdown_to_telegram_html,
)

__all__ = [
    'ChoiceOption',
    'FakeTransport',
    'MessagingTransport',
    'TelegramTransport',
    'get_transport',
    'markdown_to_telegram_html',
]
