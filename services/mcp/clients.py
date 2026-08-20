"""Dynamic client registration with dedupe and unused-client cleanup."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

from models import McpOAuthClient, db
from services.mcp.crypto import client_dedupe_hash, is_loopback_host, new_secret

logger = logging.getLogger(__name__)

MAX_CLIENT_NAME = 120
MAX_REDIRECTS = 8
UNUSED_CLIENT_TTL = timedelta(hours=24)

# Cursor DCR registers a custom-scheme callback alongside loopback + https.
# Rejecting that one URI fails the whole client even when the others are fine.
_CURSOR_CALLBACK_HOSTS = frozenset({'anysphere.cursor-mcp'})


def _valid_native_redirect(parsed) -> bool:
    if parsed.scheme != 'cursor':
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or '').lower()
    path = parsed.path or ''
    return (
        host in _CURSOR_CALLBACK_HOSTS
        and path.startswith('/oauth/')
        and path.endswith('/callback')
    )


def _valid_redirect(uri: str) -> bool:
    if not uri or '*' in uri:
        return False
    parsed = urlparse(uri)
    if _valid_native_redirect(parsed):
        return True
    if parsed.scheme not in ('https', 'http') or not parsed.netloc:
        return False
    host = (parsed.hostname or '').lower()
    if parsed.scheme == 'http' and not is_loopback_host(host):
        return False
    return True


def redirect_uri_allowed(client: McpOAuthClient, redirect_uri: str) -> bool:
    parsed = urlparse(redirect_uri or '')
    host = (parsed.hostname or '').lower()
    for registered in client.redirect_uris or []:
        if registered == redirect_uri:
            return True
        other = urlparse(registered)
        other_host = (other.hostname or '').lower()
        if (
            is_loopback_host(host)
            and is_loopback_host(other_host)
            and other.scheme == parsed.scheme
            and other.path == parsed.path
        ):
            return True
    return False


def gc_unused_clients() -> int:
    cutoff = datetime.utcnow() - UNUSED_CLIENT_TTL
    stale = McpOAuthClient.query.filter(
        McpOAuthClient.authorized_at.is_(None),
        McpOAuthClient.created_at < cutoff,
    ).all()
    count = len(stale)
    for row in stale:
        db.session.delete(row)
    if count:
        db.session.commit()
    return count


def register_client(payload: dict) -> tuple[McpOAuthClient | None, str]:
    gc_unused_clients()
    name = str(payload.get('client_name') or 'MCP client').strip()[:MAX_CLIENT_NAME]
    if not name:
        return None, 'client_name is required'
    uris = payload.get('redirect_uris') or []
    if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECTS:
        return None, 'redirect_uris must be a non-empty list'
    cleaned = []
    for uri in uris:
        text = str(uri).strip()
        if not _valid_redirect(text):
            logger.warning('MCP DCR rejected redirect_uri=%s', text)
            return None, (
                'redirect_uris must be https, http loopback, '
                'or a known desktop MCP callback, with no wildcards'
            )
        if text not in cleaned:
            cleaned.append(text)

    digest = client_dedupe_hash(name, cleaned)
    existing = McpOAuthClient.query.filter_by(dedupe_hash=digest).first()
    if existing:
        return existing, ''

    method = str(payload.get('token_endpoint_auth_method') or 'none')
    if method not in ('none', 'client_secret_post', 'client_secret_basic'):
        method = 'none'

    client = McpOAuthClient(
        client_id=new_secret(16),
        client_name=name,
        redirect_uris=cleaned,
        token_endpoint_auth_method=method,
        grant_types=['authorization_code', 'refresh_token'],
        response_types=['code'],
        dedupe_hash=digest,
    )
    db.session.add(client)
    db.session.commit()
    return client, ''
