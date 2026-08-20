"""AgentFlow MCP resource server and in-app connection settings."""
from __future__ import annotations

import secrets

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_required

from jobs.base import set_job_org_context
from models import BobAction, McpOAuthClient, McpUserGrant
from services.mcp.access import (
    mcp_allowed_for_user,
    revoke_grant,
)
from services.mcp.audit import log_mcp_event
from services.mcp.http_util import (
    bearer_token,
    empty_response,
    json_response,
    www_authenticate,
)
from services.mcp.protocol import handle_rpc
from services.mcp.tokens import verify_access_token
from services.mcp.urls import mcp_resource_url

mcp_bp = Blueprint('mcp', __name__)

SETTINGS_CSRF = 'mcp_settings_csrf'
MAX_RPC_BYTES = 256 * 1024


@mcp_bp.route('/mcp', methods=['GET', 'POST', 'OPTIONS'])
def mcp_endpoint():
    return _mcp_endpoint(readonly=False)


@mcp_bp.route('/mcp/readonly', methods=['GET', 'POST', 'OPTIONS'])
def mcp_readonly_endpoint():
    return _mcp_endpoint(readonly=True)


@mcp_bp.route('/integrations/mcp', methods=['GET'])
@login_required
def settings():
    allowed, reason = mcp_allowed_for_user(current_user)
    grants = []
    if current_user.organization_id:
        rows = (
            McpUserGrant.query
            .filter_by(user_id=current_user.id, revoked_at=None)
            .order_by(McpUserGrant.approved_at.desc())
            .all()
        )
        clients = {
            client.client_id: client
            for client in McpOAuthClient.query.filter(
                McpOAuthClient.client_id.in_([row.client_id for row in rows] or ['']),
            ).all()
        }
        grants = [
            {
                'grant': row,
                'client': clients.get(row.client_id),
            }
            for row in rows
        ]
    actions = []
    if current_user.organization_id:
        actions = (
            BobAction.query
            .filter_by(
                organization_id=current_user.organization_id,
                user_id=current_user.id,
                surface='mcp',
            )
            .order_by(BobAction.created_at.desc())
            .limit(20)
            .all()
        )
    csrf = secrets.token_urlsafe(16)
    session[SETTINGS_CSRF] = csrf
    return render_template(
        'integrations/mcp.html',
        allowed=allowed,
        blocked_reason=reason,
        mcp_url=mcp_resource_url(readonly=False),
        readonly_url=mcp_resource_url(readonly=True),
        grants=grants,
        actions=actions,
        csrf=csrf,
    )


@mcp_bp.route('/integrations/mcp/revoke/<int:grant_id>', methods=['POST'])
@login_required
def revoke_connection(grant_id):
    posted = request.form.get('csrf') or ''
    expected = session.get(SETTINGS_CSRF) or ''
    if not posted or not expected or not secrets.compare_digest(posted, expected):
        abort(400, description='This revoke form is no longer valid.')
    grant = McpUserGrant.query.filter_by(
        id=grant_id,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        revoked_at=None,
    ).first_or_404()
    revoke_grant(grant, actor_id=current_user.id)
    flash('That MCP connection was revoked.', 'success')
    return redirect(url_for('mcp.settings'))


def _mcp_endpoint(*, readonly: bool):
    if request.method == 'OPTIONS':
        return empty_response(204)
    if request.method == 'GET':
        response = empty_response(405)
        response.headers['Allow'] = 'POST, OPTIONS'
        return response

    raw = request.get_data(cache=True) or b''
    if len(raw) > MAX_RPC_BYTES:
        return json_response(
            {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Payload too large'}},
            400,
        )

    token = bearer_token()
    expected = mcp_resource_url(readonly=readonly)
    if not token:
        return _unauthorized(readonly)
    verified = verify_access_token(token, expected_resource=expected)
    if verified is None:
        log_mcp_event(
            'mcp_token_rejected',
            organization_id=None,
            actor_id=None,
            description='MCP access token rejected',
            event_data={'readonly': readonly, 'path': request.path},
        )
        return _unauthorized(readonly)

    payload = request.get_json(silent=True)
    if payload is None:
        return json_response(
            {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}},
            400,
        )
    if isinstance(payload, list):
        return json_response(
            {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Batch requests are not supported'}},
            400,
        )

    set_job_org_context(verified.organization_id)
    result = handle_rpc(payload, verified)
    if result is None:
        return empty_response(202)
    return json_response(result)


def _unauthorized(readonly: bool):
    response = json_response({'error': 'invalid_token'}, 401)
    response.headers['WWW-Authenticate'] = www_authenticate(readonly=readonly)
    return response
