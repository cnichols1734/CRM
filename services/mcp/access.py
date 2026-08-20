"""Feature-flag, org-role, and grant revocation checks."""
from __future__ import annotations

from datetime import datetime

from feature_flags import org_has_feature
from models import McpAccessToken, McpRefreshToken, McpUserGrant, Organization, User, db
from services.bob_tools.context import ADMIN_ORG_ROLES
from services.mcp.audit import log_mcp_event


def mcp_allowed_for_user(user: User, org: Organization | None = None) -> tuple[bool, str]:
    """Whether this user may connect or keep using MCP right now."""
    org = org or getattr(user, 'organization', None)
    if user is None or not getattr(user, 'id', None):
        return False, 'Not signed in.'
    if not org:
        return False, 'Your account is not in an organization.'
    if org.status != 'active':
        return False, 'This organization is not active.'
    if not org_has_feature('MCP_CONNECTOR', org):
        return False, 'MCP is turned off for this organization.'
    if getattr(org, 'mcp_admin_only', False) and (user.org_role or 'agent') not in ADMIN_ORG_ROLES:
        return False, 'Only owners and admins can connect MCP for this organization.'
    return True, ''


def grant_still_valid(grant: McpUserGrant, user: User | None = None) -> tuple[bool, str]:
    if grant is None or grant.is_revoked:
        return False, 'This connection was revoked.'
    user = user or grant.user
    org = Organization.query.get(grant.organization_id)
    if org is None:
        return False, 'Organization not found.'
    if user is None or user.organization_id != grant.organization_id:
        return False, 'This connection is no longer valid for your account.'
    invalidated = getattr(org, 'session_invalidated_at', None)
    if invalidated and grant.approved_at and grant.approved_at < invalidated:
        return False, 'Your organization signed everyone out. Connect again.'
    return mcp_allowed_for_user(user, org)


def revoke_grant(grant: McpUserGrant, *, actor_id: int | None = None) -> None:
    now = datetime.utcnow()
    grant.revoked_at = now
    McpAccessToken.query.filter_by(grant_id=grant.id, revoked_at=None).update(
        {'revoked_at': now}, synchronize_session=False,
    )
    McpRefreshToken.query.filter_by(grant_id=grant.id, revoked_at=None).update(
        {'revoked_at': now}, synchronize_session=False,
    )
    db.session.commit()
    log_mcp_event(
        'mcp_grant_revoked',
        organization_id=grant.organization_id,
        actor_id=actor_id or grant.user_id,
        description='MCP connection revoked',
        event_data={'grant_id': grant.id, 'client_id': grant.client_id},
    )


def revoke_user_mcp_grants(user: User) -> int:
    grants = McpUserGrant.query.filter_by(
        user_id=user.id, revoked_at=None,
    ).all()
    for grant in grants:
        revoke_grant(grant, actor_id=user.id)
    return len(grants)
