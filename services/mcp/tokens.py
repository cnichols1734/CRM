"""Issue and verify opaque MCP access/refresh tokens."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from models import (
    McpAccessToken, McpAuthorizationCode, McpRefreshToken, McpUserGrant,
    User, db,
)
from services.mcp.access import grant_still_valid
from services.mcp.crypto import hash_secret, new_secret
from services.mcp.urls import resource_matches

ACCESS_TTL = timedelta(hours=1)
REFRESH_IDLE_TTL = timedelta(days=30)
REFRESH_ABSOLUTE_TTL = timedelta(days=180)
AUTH_CODE_TTL = timedelta(minutes=10)


@dataclass
class VerifiedToken:
    user: User
    grant: McpUserGrant
    scopes: list[str]
    resource: str
    client_id: str
    organization_id: int


def issue_authorization_code(
    *,
    grant: McpUserGrant,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    resource: str,
    code_challenge: str,
) -> str:
    code = new_secret(32)
    row = McpAuthorizationCode(
        code_hash=hash_secret(code),
        grant_id=grant.id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        resource=resource,
        code_challenge=code_challenge,
        code_challenge_method='S256',
        expires_at=datetime.utcnow() + AUTH_CODE_TTL,
    )
    db.session.add(row)
    db.session.commit()
    return code


def lookup_authorization_code(code: str):
    row = McpAuthorizationCode.query.filter_by(code_hash=hash_secret(code)).first()
    now = datetime.utcnow()
    if row is None or row.consumed_at or row.expires_at < now:
        return None
    return row


def consume_authorization_code(row: McpAuthorizationCode) -> None:
    row.consumed_at = datetime.utcnow()
    db.session.commit()


def issue_token_pair(grant: McpUserGrant, *, scopes: list[str], resource: str, client_id: str) -> dict:
    access = new_secret(32)
    refresh = new_secret(32)
    now = datetime.utcnow()
    db.session.add(McpAccessToken(
        token_hash=hash_secret(access),
        grant_id=grant.id,
        client_id=client_id,
        user_id=grant.user_id,
        organization_id=grant.organization_id,
        scopes=scopes,
        resource=resource,
        expires_at=now + ACCESS_TTL,
    ))
    db.session.add(McpRefreshToken(
        token_hash=hash_secret(refresh),
        grant_id=grant.id,
        client_id=client_id,
        expires_at=now + REFRESH_IDLE_TTL,
        absolute_expires_at=now + REFRESH_ABSOLUTE_TTL,
    ))
    db.session.commit()
    return {
        'access_token': access,
        'token_type': 'Bearer',
        'expires_in': int(ACCESS_TTL.total_seconds()),
        'refresh_token': refresh,
        'scope': ' '.join(scopes),
    }


def rotate_refresh_token(old: McpRefreshToken, grant: McpUserGrant) -> dict:
    now = datetime.utcnow()
    old.revoked_at = now
    access = new_secret(32)
    refresh = new_secret(32)
    remaining_abs = old.absolute_expires_at
    db.session.add(McpAccessToken(
        token_hash=hash_secret(access),
        grant_id=grant.id,
        client_id=old.client_id,
        user_id=grant.user_id,
        organization_id=grant.organization_id,
        scopes=grant.scopes,
        resource=grant.resource,
        expires_at=now + ACCESS_TTL,
    ))
    db.session.add(McpRefreshToken(
        token_hash=hash_secret(refresh),
        grant_id=grant.id,
        client_id=old.client_id,
        expires_at=now + REFRESH_IDLE_TTL,
        absolute_expires_at=remaining_abs,
    ))
    db.session.commit()
    return {
        'access_token': access,
        'token_type': 'Bearer',
        'expires_in': int(ACCESS_TTL.total_seconds()),
        'refresh_token': refresh,
        'scope': ' '.join(grant.scopes or []),
    }


def load_refresh_token(token: str) -> McpRefreshToken | None:
    row = McpRefreshToken.query.filter_by(token_hash=hash_secret(token)).first()
    now = datetime.utcnow()
    if row is None or row.revoked_at:
        return None
    if row.expires_at < now or row.absolute_expires_at < now:
        return None
    return row


def verify_access_token(token: str, *, expected_resource: str) -> VerifiedToken | None:
    if not token:
        return None
    row = McpAccessToken.query.filter_by(token_hash=hash_secret(token)).first()
    now = datetime.utcnow()
    if row is None or row.revoked_at or row.expires_at < now:
        return None
    if not resource_matches(row.resource, expected_resource):
        return None
    grant = McpUserGrant.query.get(row.grant_id)
    user = User.query.filter_by(id=row.user_id, organization_id=row.organization_id).first()
    ok, _reason = grant_still_valid(grant, user)
    if not ok:
        return None
    return VerifiedToken(
        user=user,
        grant=grant,
        scopes=list(row.scopes or []),
        resource=row.resource,
        client_id=row.client_id,
        organization_id=row.organization_id,
    )


def revoke_presented_token(token: str) -> None:
    now = datetime.utcnow()
    digest = hash_secret(token)
    access = McpAccessToken.query.filter_by(token_hash=digest, revoked_at=None).first()
    if access:
        access.revoked_at = now
        db.session.commit()
        return
    refresh = McpRefreshToken.query.filter_by(token_hash=digest, revoked_at=None).first()
    if refresh:
        refresh.revoked_at = now
        db.session.commit()
