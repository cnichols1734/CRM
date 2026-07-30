"""Deep-link account binding for messaging channels.

Issue a single-use hashed token, render it as ``t.me/<bot>?start=<token>``,
redeem it when Telegram delivers ``/start <token>``, and disconnect on demand.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta
from typing import Optional

from models import (
    AgentMessagingChannel,
    MessagingLinkToken,
    db,
)

logger = logging.getLogger(__name__)

TOKEN_TTL_MINUTES = 15
# Telegram's start payload is capped at 64 characters; 32 random bytes → 43.
TOKEN_BYTES = 32
PROVIDER = AgentMessagingChannel.PROVIDER_TELEGRAM


class BindingError(Exception):
    """Raised when a link token cannot be issued or redeemed."""


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def issue_link_token(user_id: int, organization_id: int,
                     *, provider: str = PROVIDER) -> str:
    """Create a fresh binding token and return the raw value (shown once)."""
    # Invalidate any unused tokens for this user so a leaked older QR dies.
    MessagingLinkToken.query.filter_by(
        user_id=user_id,
        provider=provider,
        used_at=None,
    ).delete(synchronize_session=False)

    raw = urlsafe_b64encode(secrets.token_bytes(TOKEN_BYTES)).decode('ascii').rstrip('=')
    row = MessagingLinkToken(
        user_id=user_id,
        organization_id=organization_id,
        provider=provider,
        token_hash=_hash_token(raw),
        expires_at=datetime.utcnow() + timedelta(minutes=TOKEN_TTL_MINUTES),
    )
    db.session.add(row)
    db.session.commit()
    return raw


def deep_link_url(bot_username: str, raw_token: str) -> str:
    username = (bot_username or '').lstrip('@')
    if not username:
        raise BindingError('TELEGRAM_BOT_USERNAME is not configured')
    return f'https://t.me/{username}?start={raw_token}'


def get_active_channel(user_id: int, *, provider: str = PROVIDER
                       ) -> Optional[AgentMessagingChannel]:
    return AgentMessagingChannel.query.filter_by(
        user_id=user_id,
        provider=provider,
        disabled_at=None,
    ).first()


def find_channel_by_external_id(external_id: str, *, provider: str = PROVIDER
                                ) -> Optional[AgentMessagingChannel]:
    return AgentMessagingChannel.query.filter_by(
        provider=provider,
        external_id=str(external_id),
        disabled_at=None,
    ).first()


def redeem_link_token(
    raw_token: str,
    *,
    external_id: str,
    chat_id: str,
    provider: str = PROVIDER,
) -> AgentMessagingChannel:
    """Bind ``external_id`` to the CRM user that issued ``raw_token``.

    Raises ``BindingError`` on missing, expired, or already-used tokens.
    """
    if not raw_token or not external_id or not chat_id:
        raise BindingError('Missing token or Telegram identity.')

    token_hash = _hash_token(raw_token)
    row = MessagingLinkToken.query.filter_by(
        token_hash=token_hash,
        provider=provider,
    ).first()
    if row is None:
        raise BindingError('That link is not recognised. Generate a new one from your profile.')
    if row.used_at is not None:
        raise BindingError('That link was already used. Generate a new one from your profile.')
    if row.expires_at < datetime.utcnow():
        raise BindingError('That link expired. Generate a new one from your profile.')

    # One Telegram account → one CRM user. If already linked elsewhere, refuse.
    existing = AgentMessagingChannel.query.filter_by(
        provider=provider,
        external_id=str(external_id),
    ).first()
    if existing is not None and existing.user_id != row.user_id:
        raise BindingError(
            'This Telegram account is already linked to a different CRM user.'
        )

    channel = AgentMessagingChannel.query.filter_by(
        user_id=row.user_id,
        provider=provider,
    ).first()
    if channel is None:
        channel = AgentMessagingChannel(
            user_id=row.user_id,
            organization_id=row.organization_id,
            provider=provider,
            external_id=str(external_id),
            chat_id=str(chat_id),
            linked_at=datetime.utcnow(),
        )
        db.session.add(channel)
    else:
        channel.external_id = str(external_id)
        channel.chat_id = str(chat_id)
        channel.organization_id = row.organization_id
        channel.linked_at = datetime.utcnow()
        channel.disabled_at = None
        channel.disable_reason = None

    row.used_at = datetime.utcnow()
    db.session.commit()
    logger.info(
        'Linked %s channel user_id=%s external_id=%s',
        provider, row.user_id, external_id,
    )
    return channel


def disconnect_channel(user_id: int, *, provider: str = PROVIDER,
                       reason: str = 'user_disconnected') -> bool:
    """Disable the agent's channel. Returns True if one was active."""
    channel = AgentMessagingChannel.query.filter_by(
        user_id=user_id,
        provider=provider,
        disabled_at=None,
    ).first()
    if channel is None:
        return False
    channel.disabled_at = datetime.utcnow()
    channel.disable_reason = reason[:100]
    channel.pending_action_id = None
    db.session.commit()
    return True


def constant_time_secret_match(expected: str, provided: Optional[str]) -> bool:
    """Compare webhook secrets without leaking length via early return."""
    if not expected or provided is None:
        return False
    return hmac.compare_digest(expected.encode('utf-8'), provided.encode('utf-8'))
