"""
Magic Inbox provisioning.

Each user gets a unique forwarding address of the form
    <slug>-<token>@<INBOUND_EMAIL_DOMAIN>

The 8-char base32 token is the auth — anyone with the address can post,
which is the entire point. Slug is just there to make the address feel
human ("oh that's mine") rather than to gate access.
"""
from __future__ import annotations

import os
import re
import secrets
import string
import unicodedata

from flask import current_app

from models import db, User


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DOMAIN = 'inbox.origentechnolog.com'
TOKEN_LENGTH = 8
# Crockford-ish base32 minus visually ambiguous chars (0/O, 1/I/l). Lowercase
# so the address is comfortable to read in the wild.
TOKEN_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
SLUG_MAX = 40

# Slugs that would look weird or land us in the middle of a system address.
RESERVED_SLUGS = {
    'admin', 'support', 'help', 'info', 'noreply', 'no-reply',
    'postmaster', 'abuse', 'security', 'hello', 'inbox', 'mail',
    'mailer-daemon', 'system', 'root', 'webmaster', 'origen',
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_inbox_domain() -> str:
    """Return the configured inbox subdomain (env-overridable)."""
    return os.getenv('INBOUND_EMAIL_DOMAIN') or DEFAULT_DOMAIN


def slugify_for_inbox(first_name: str | None, last_name: str | None,
                      fallback: str | None = None) -> str:
    """Build a human-readable slug like ``chris.nichols``.

    Falls back to *fallback* (typically the username or email local-part)
    if the name is empty or strips to nothing useful.
    """
    parts = [(first_name or '').strip(), (last_name or '').strip()]
    raw = '.'.join(p for p in parts if p)
    if not raw and fallback:
        raw = fallback
    return _normalize_slug(raw or 'user')


def _normalize_slug(text: str) -> str:
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii').lower()
    # Replace any run of non-alphanumeric characters with a single dot so
    # "Mary-Jane O'Brien" → "mary.jane.obrien" — readable, RFC-safe.
    text = re.sub(r'[^a-z0-9]+', '.', text).strip('.')
    text = re.sub(r'\.{2,}', '.', text) or 'user'
    text = text[:SLUG_MAX].strip('.')
    if text in RESERVED_SLUGS or not text:
        text = f'{text or "user"}.user'.strip('.')
    return text


def generate_token() -> str:
    """8 chars of base32-ish entropy (~40 bits)."""
    return ''.join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def build_address(slug: str, token: str, domain: str | None = None) -> str:
    return f'{slug}-{token}@{domain or get_inbox_domain()}'


def parse_recipient(address: str) -> tuple[str | None, str | None, str | None]:
    """Split an inbound recipient into (slug, token, plus_alias).

    Returns (None, None, None) if it doesn't look like one of our addresses.
    """
    if not address or '@' not in address:
        return None, None, None
    local, _, domain = address.lower().partition('@')
    if domain != get_inbox_domain().lower():
        return None, None, None

    plus_alias = None
    if '+' in local:
        local, _, plus_alias = local.partition('+')
        plus_alias = plus_alias or None

    if '-' not in local:
        return None, None, None
    slug, _, token = local.rpartition('-')
    if not slug or len(token) != TOKEN_LENGTH:
        return None, None, None
    if not all(c in TOKEN_ALPHABET for c in token):
        return None, None, None
    return slug, token, plus_alias


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

def provision_inbox_address(user: User, *, commit: bool = True) -> User:
    """Generate and assign a unique inbox address for *user*.

    Idempotent — if the user already has an address, returns the user
    unchanged. Caller is responsible for handling commit failures.
    """
    if user.inbox_address and user.inbox_token:
        return user

    fallback = (user.username or
                (user.email.split('@')[0] if user.email else None))
    base_slug = slugify_for_inbox(user.first_name, user.last_name,
                                  fallback=fallback)
    domain = get_inbox_domain()

    # The token alone is sufficiently unique. We retry on the rare slug+token
    # collision so the address column's UNIQUE constraint can stay the source
    # of truth.
    for _ in range(8):
        token = generate_token()
        address = build_address(base_slug, token, domain)
        existing = User.query.filter(
            (User.inbox_token == token) | (User.inbox_address == address)
        ).first()
        if existing is None:
            user.inbox_token = token
            user.inbox_address = address
            if commit:
                db.session.commit()
            return user

    raise RuntimeError(
        f'Could not allocate a unique inbox token for user {user.id} '
        f'after 8 attempts. Check the inbox token alphabet/length.'
    )


def rotate_inbox_address(user: User, *, commit: bool = True) -> User:
    """Re-issue the token suffix while keeping the slug.

    Use sparingly — anyone who saved the old address (e.g. on their phone
    as "Origen Inbox") will need the new one.
    """
    user.inbox_token = None
    user.inbox_address = None
    return provision_inbox_address(user, commit=commit)


def ensure_inbox_for(user: User) -> User:
    """Lazy provisioning safety net used by the request hook.

    Wraps any failure so a missing inbox can never break a normal request.
    """
    if not user or user.inbox_address:
        return user
    try:
        return provision_inbox_address(user)
    except Exception:
        current_app.logger.exception(
            'Failed to lazily provision inbox address for user_id=%s',
            getattr(user, 'id', None),
        )
        try:
            db.session.rollback()
        except Exception:
            pass
        return user
