"""Password login and long-lived JWT for the agent iPhone app.

Mirrors the client portal JWT crypto. typ=agent. Flask-Login cookies
are not consulted here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from flask import current_app

from models import Organization, User


JWT_TTL_SECONDS = 30 * 24 * 60 * 60
JWT_TYP = 'agent'
JWT_ISS = 'agentflow-agent'


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


def find_login_user(login_input):
    """Look up email first, then username. Same order as web login."""
    value = (login_input or '').strip()
    if not value:
        return None
    user = User.query.filter(User.email == value).first()
    if not user:
        user = User.query.filter(User.username == value).first()
    return user


def login_input_from_body(data):
    """Prefer email, then username. Accepts either key."""
    if not isinstance(data, dict):
        return ''
    email = (data.get('email') or '').strip()
    if email:
        return email
    return (data.get('username') or '').strip()


def org_is_active(user):
    org = user.organization if user else None
    return bool(org and org.status == 'active')


def issue_agent_jwt(user, now=None, ttl_seconds=JWT_TTL_SECONDS):
    now = int(now if now is not None else time.time())
    payload = {
        'typ': JWT_TYP,
        'uid': user.id,
        'oid': user.organization_id,
        'sv': user.session_version or 1,
        'iat': now,
        'exp': now + int(ttl_seconds),
        'iss': JWT_ISS,
    }
    header = {'alg': 'HS256', 'typ': 'JWT'}
    header_b64 = _b64url_encode(json.dumps(header, separators=(',', ':')).encode())
    body_b64 = _b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
    signing = f'{header_b64}.{body_b64}'.encode('ascii')
    sig = hmac.new(_secret(), signing, hashlib.sha256).digest()
    return f'{header_b64}.{body_b64}.{_b64url_encode(sig)}'


def decode_agent_jwt(token):
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


def load_user_from_jwt(token, now=None):
    """Return (user, error_message) for a Bearer agent JWT."""
    claims = decode_agent_jwt(token)
    if not claims:
        return None, 'Sign in with your email and password.'
    if jwt_is_expired(claims, now=now):
        return None, 'Your session expired. Sign in again.'
    user = User.query.get(claims.get('uid'))
    if not user:
        return None, 'Sign in with your email and password.'
    if int(user.session_version or 1) != int(claims.get('sv') or 0):
        return None, 'Your session is no longer valid. Sign in again.'
    if user.organization_id != claims.get('oid'):
        return None, 'Sign in with your email and password.'
    org = Organization.query.get(user.organization_id)
    if not org or org.status != 'active':
        return None, 'This account is not active.'
    return user, None


def serialize_user(user):
    return {
        'id': user.id,
        'email': user.email,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'org_role': user.org_role,
    }


def serialize_org(org):
    if org is None:
        return None
    return {
        'id': org.id,
        'name': org.name,
        'slug': org.slug,
        'subscription_tier': org.subscription_tier,
        'status': org.status,
    }
