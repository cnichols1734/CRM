"""Invite-code grant and short-lived JWT for the client iPhone app.

The grant is still ClientPortalAccess. The HTML portal keeps using the
URL token. The app never puts that token in a path. A human invite code
is exchanged for a JWT sent as Authorization: Bearer.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time

from flask import current_app

from models import ClientPortalAccess, Organization


DEFAULT_ACCENT = '#f97316'
DEFAULT_ACCENT_INK = '#ea580c'
JWT_TTL_SECONDS = 12 * 60 * 60
JWT_TYP = 'client_portal'
_HEX_COLOR = re.compile(r'^#[0-9A-Fa-f]{6}$')


def normalize_brand_accent(value):
    raw = (value or '').strip()
    if not raw:
        return None
    if not _HEX_COLOR.match(raw):
        return False
    return raw.lower()


def org_branding(org):
    """Name, logo, and accent for THIS app. Defaults to product orange."""
    if org is None:
        return {
            'name': 'Your brokerage',
            'logo_url': None,
            'accent': DEFAULT_ACCENT,
            'accent_ink': DEFAULT_ACCENT_INK,
        }
    accent = (getattr(org, 'brand_accent', None) or '').strip().lower()
    if not _HEX_COLOR.match(accent or ''):
        accent = DEFAULT_ACCENT
        accent_ink = DEFAULT_ACCENT_INK
    elif accent == DEFAULT_ACCENT:
        accent_ink = DEFAULT_ACCENT_INK
    else:
        accent_ink = _darken_hex(accent)
    return {
        'name': org.name,
        'logo_url': org.logo_url or None,
        'accent': accent,
        'accent_ink': accent_ink,
    }


def branding_for_access(access):
    org = Organization.query.get(access.organization_id)
    return org_branding(org)


def _darken_hex(hex_color, factor=0.88):
    h = hex_color.lstrip('#')
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return '#{:02x}{:02x}{:02x}'.format(
        max(0, int(r * factor)),
        max(0, int(g * factor)),
        max(0, int(b * factor)),
    )


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(value: str) -> bytes:
    pad = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _secret() -> bytes:
    key = current_app.config.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    if isinstance(key, bytes):
        return key
    return str(key).encode('utf-8')


def issue_client_jwt(access, now=None, ttl_seconds=JWT_TTL_SECONDS):
    """Return a compact HS256 JWT bound to this grant and session_version."""
    now = int(now if now is not None else time.time())
    payload = {
        'typ': JWT_TYP,
        'aid': access.id,
        'oid': access.organization_id,
        'tid': access.transaction_id,
        'pid': access.participant_id,
        'sv': access.session_version or 1,
        'iat': now,
        'exp': now + int(ttl_seconds),
        'iss': 'agentflow-client',
    }
    header = {'alg': 'HS256', 'typ': 'JWT'}
    header_b64 = _b64url_encode(json.dumps(header, separators=(',', ':')).encode())
    body_b64 = _b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
    signing = f'{header_b64}.{body_b64}'.encode('ascii')
    sig = hmac.new(_secret(), signing, hashlib.sha256).digest()
    return f'{header_b64}.{body_b64}.{_b64url_encode(sig)}'


def decode_client_jwt(token):
    """Return claims dict or None if the signature or shape is wrong."""
    if not token or token.count('.') != 2:
        return None
    header_b64, body_b64, sig_b64 = token.split('.')
    signing = f'{header_b64}.{body_b64}'.encode('ascii')
    try:
        expected = hmac.new(_secret(), signing, hashlib.sha256).digest()
        given = _b64url_decode(sig_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(expected, given):
        return None
    try:
        claims = json.loads(_b64url_decode(body_b64))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict) or claims.get('typ') != JWT_TYP:
        return None
    return claims


def jwt_is_expired(claims, now=None):
    now = int(now if now is not None else time.time())
    try:
        return int(claims.get('exp') or 0) <= now
    except (TypeError, ValueError):
        return True


def exchange_invite_code(code, now=None):
    """Resolve a human invite code to an active grant.

    Returns (access, error_message). error_message is set on 401 cases.
    """
    access = ClientPortalAccess.find_by_invite_code(code)
    if not access:
        return None, 'That invite code is not valid.'
    if not access.is_active:
        return None, 'This invite is no longer active.'
    if access.invite_is_expired(now=now):
        return None, 'This invite has expired.'
    return access, None


def load_access_from_jwt(token, now=None):
    """Return (access, error_message) for a Bearer JWT."""
    claims = decode_client_jwt(token)
    if not claims:
        return None, 'Sign in with your invite code first.'
    if jwt_is_expired(claims, now=now):
        return None, 'Your session expired. Enter your invite code again.'
    access = ClientPortalAccess.query.get(claims.get('aid'))
    if not access or not access.is_active:
        return None, 'This invite is no longer active.'
    if int(access.session_version or 1) != int(claims.get('sv') or 0):
        return None, 'Your session is no longer valid. Enter your invite code again.'
    if (
        access.organization_id != claims.get('oid')
        or access.transaction_id != claims.get('tid')
        or access.participant_id != claims.get('pid')
    ):
        return None, 'Sign in with your invite code first.'
    return access, None
