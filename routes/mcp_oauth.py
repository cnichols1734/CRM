"""AgentFlow OAuth 2.1 authorization server for the MCP connector."""
from __future__ import annotations

import secrets
from datetime import datetime
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session,
)
from flask_login import current_user, login_required

from models import McpOAuthClient, McpUserGrant, db
from services.mcp.access import grant_still_valid, mcp_allowed_for_user
from services.mcp.adapter import build_context, grouped_tool_names
from services.mcp.audit import log_mcp_event
from services.mcp.clients import redirect_uri_allowed, register_client
from services.mcp.crypto import is_loopback_host, new_secret, pkce_matches, redirect_host
from services.mcp.http_util import json_response, with_cors
from services.mcp.rate_limit import register_allowed
from services.mcp.scopes import (
    ALL_SCOPES,
    DEFAULT_CONSENT_SCOPES,
    SCOPE_DESTRUCTIVE,
    SCOPE_LABELS,
    SCOPE_OFFLINE,
    SCOPE_READ,
    SCOPE_WRITE,
    normalize_scopes,
)
from services.mcp.tokens import (
    consume_authorization_code,
    issue_authorization_code,
    issue_token_pair,
    load_refresh_token,
    lookup_authorization_code,
    revoke_presented_token,
    rotate_refresh_token,
)
from services.mcp.urls import (
    app_base_url,
    authorization_server_issuer,
    mcp_resource_url,
    resource_matches,
)

mcp_oauth_bp = Blueprint('mcp_oauth', __name__)

PENDING_KEY = 'mcp_oauth_pending'
MAX_REGISTER_BYTES = 8 * 1024


def _as_metadata(*, readonly: bool) -> dict:
    resource = mcp_resource_url(readonly=readonly)
    scopes = [SCOPE_READ, SCOPE_OFFLINE] if readonly else list(ALL_SCOPES)
    return {
        'resource': resource,
        'authorization_servers': [authorization_server_issuer()],
        'scopes_supported': scopes,
        'bearer_methods_supported': ['header'],
        'resource_documentation': f'{app_base_url()}/integrations/mcp',
    }


@mcp_oauth_bp.route('/.well-known/oauth-protected-resource')
@mcp_oauth_bp.route('/.well-known/oauth-protected-resource/mcp')
def protected_resource_metadata():
    return json_response(_as_metadata(readonly=False))


@mcp_oauth_bp.route('/.well-known/oauth-protected-resource/mcp/readonly')
def protected_resource_metadata_readonly():
    return json_response(_as_metadata(readonly=True))


@mcp_oauth_bp.route('/.well-known/oauth-authorization-server')
def authorization_server_metadata():
    issuer = authorization_server_issuer()
    return json_response({
        'issuer': issuer,
        'authorization_endpoint': f'{issuer}/oauth/authorize',
        'token_endpoint': f'{issuer}/oauth/token',
        'revocation_endpoint': f'{issuer}/oauth/revoke',
        'registration_endpoint': f'{issuer}/oauth/register',
        'scopes_supported': list(ALL_SCOPES),
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'refresh_token'],
        'code_challenge_methods_supported': ['S256'],
        'token_endpoint_auth_methods_supported': [
            'none', 'client_secret_post', 'client_secret_basic',
        ],
        'revocation_endpoint_auth_methods_supported': ['none'],
    })


@mcp_oauth_bp.route('/oauth/register', methods=['POST', 'OPTIONS'])
def register():
    if request.method == 'OPTIONS':
        return with_cors(json_response({}, 204))
    if not request.is_json:
        return json_response(
            {'error': 'invalid_client_metadata', 'error_description': 'JSON body required'},
            400,
        )
    raw = request.get_data(cache=True) or b''
    if len(raw) > MAX_REGISTER_BYTES:
        return json_response(
            {'error': 'invalid_client_metadata', 'error_description': 'Registration payload is too large'},
            400,
        )
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
    if not register_allowed(ip):
        return json_response(
            {'error': 'temporarily_unavailable', 'error_description': 'Too many registrations from this address'},
            429,
        )
    client, error = register_client(request.get_json(silent=True) or {})
    if error or client is None:
        return json_response(
            {'error': 'invalid_client_metadata', 'error_description': error or 'Could not register client'},
            400,
        )
    issued = int((client.created_at or datetime.utcnow()).timestamp())
    return json_response({
        'client_id': client.client_id,
        'client_id_issued_at': issued,
        'client_name': client.client_name,
        'redirect_uris': client.redirect_uris,
        'token_endpoint_auth_method': client.token_endpoint_auth_method,
        'grant_types': client.grant_types,
        'response_types': client.response_types,
    }, 201)


@mcp_oauth_bp.route('/oauth/authorize', methods=['GET', 'POST'])
@login_required
def authorize():
    if request.method == 'GET':
        return _authorize_get()
    return _authorize_post()


def _authorize_get():
    params = request.args
    client = McpOAuthClient.query.filter_by(client_id=params.get('client_id') or '').first()
    if client is None:
        abort(400, description='Unknown MCP client.')
    if (params.get('response_type') or '') != 'code':
        abort(400, description='response_type must be code.')
    redirect_uri = params.get('redirect_uri') or ''
    if not redirect_uri_allowed(client, redirect_uri):
        abort(400, description='redirect_uri is not registered for this client.')
    if (params.get('code_challenge_method') or 'S256') != 'S256' or not params.get('code_challenge'):
        return _oauth_redirect(redirect_uri, {
            'error': 'invalid_request',
            'error_description': 'PKCE S256 is required',
            'state': params.get('state') or '',
        })

    resource = (params.get('resource') or '').strip() or mcp_resource_url(readonly=False)
    if not _known_resource(resource):
        return _oauth_redirect(redirect_uri, {
            'error': 'invalid_target',
            'error_description': 'resource must be the AgentFlow MCP URL',
            'state': params.get('state') or '',
        })

    allowed, reason = mcp_allowed_for_user(current_user)
    if not allowed:
        flash(reason, 'error')
        return render_template(
            'mcp/authorize.html',
            blocked=True,
            blocked_reason=reason,
            client=client,
            redirect_host=redirect_host(redirect_uri),
            loopback=is_loopback_host(redirect_host(redirect_uri)),
        )

    requested = normalize_scopes(params.get('scope'))
    readonly = resource.rstrip('/').endswith('/readonly')
    if readonly:
        requested = [scope for scope in requested if scope in (SCOPE_READ, SCOPE_OFFLINE)]
        if SCOPE_READ not in requested:
            requested.insert(0, SCOPE_READ)

    csrf = new_secret(16)
    session[PENDING_KEY] = {
        'client_id': client.client_id,
        'redirect_uri': redirect_uri,
        'state': params.get('state') or '',
        'code_challenge': params.get('code_challenge'),
        'resource': resource,
        'requested_scopes': requested,
        'csrf': csrf,
    }

    ctx = build_context(current_user)
    preview_scopes = [scope for scope in requested if scope != SCOPE_OFFLINE] or [SCOPE_READ]
    tool_groups = grouped_tool_names(ctx, preview_scopes)
    return render_template(
        'mcp/authorize.html',
        blocked=False,
        client=client,
        redirect_host=redirect_host(redirect_uri),
        loopback=is_loopback_host(redirect_host(redirect_uri)),
        user=current_user,
        org=current_user.organization,
        scopes=requested,
        scope_labels=SCOPE_LABELS,
        readonly=readonly,
        tool_groups=tool_groups,
        csrf=csrf,
        default_scopes=DEFAULT_CONSENT_SCOPES,
    )


def _authorize_post():
    pending = session.get(PENDING_KEY) or {}
    if not pending:
        abort(400, description='This authorization request expired. Start the connection again.')
    posted_csrf = request.form.get('csrf') or ''
    if not posted_csrf or not secrets.compare_digest(posted_csrf, pending.get('csrf') or ''):
        abort(400, description='This authorization form is no longer valid.')

    client = McpOAuthClient.query.filter_by(client_id=pending['client_id']).first()
    redirect_uri = pending['redirect_uri']
    state = pending.get('state') or ''
    if client is None or not redirect_uri_allowed(client, redirect_uri):
        session.pop(PENDING_KEY, None)
        abort(400, description='Unknown MCP client.')

    if request.form.get('decision') != 'approve':
        session.pop(PENDING_KEY, None)
        log_mcp_event(
            'mcp_consent_denied',
            organization_id=current_user.organization_id,
            actor_id=current_user.id,
            description='MCP consent denied',
            event_data={'client_id': client.client_id},
        )
        return _oauth_redirect(redirect_uri, {
            'error': 'access_denied',
            'state': state,
        })

    allowed, reason = mcp_allowed_for_user(current_user)
    if not allowed:
        session.pop(PENDING_KEY, None)
        flash(reason, 'error')
        return _oauth_redirect(redirect_uri, {
            'error': 'access_denied',
            'error_description': reason,
            'state': state,
        })

    resource = pending['resource']
    readonly = resource.rstrip('/').endswith('/readonly')
    chosen = []
    if request.form.get('scope_read'):
        chosen.append(SCOPE_READ)
    if request.form.get('scope_write') and not readonly:
        chosen.append(SCOPE_WRITE)
    if request.form.get('scope_destructive') and not readonly:
        chosen.append(SCOPE_DESTRUCTIVE)
    chosen.append(SCOPE_OFFLINE)
    scopes = normalize_scopes(chosen)
    if SCOPE_READ not in scopes:
        scopes.insert(0, SCOPE_READ)

    now = datetime.utcnow()
    grant = McpUserGrant.query.filter_by(
        user_id=current_user.id,
        client_id=client.client_id,
        resource=resource,
        revoked_at=None,
    ).first()
    if grant is None:
        grant = McpUserGrant(
            organization_id=current_user.organization_id,
            user_id=current_user.id,
            client_id=client.client_id,
            scopes=scopes,
            resource=resource,
            approved_at=now,
        )
        db.session.add(grant)
    else:
        grant.scopes = scopes
        grant.approved_at = now
        grant.organization_id = current_user.organization_id
    client.authorized_at = now
    db.session.commit()

    code = issue_authorization_code(
        grant=grant,
        client_id=client.client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        resource=resource,
        code_challenge=pending['code_challenge'],
    )
    session.pop(PENDING_KEY, None)
    log_mcp_event(
        'mcp_consent_approved',
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        description='MCP consent approved',
        event_data={'client_id': client.client_id, 'scopes': scopes, 'grant_id': grant.id},
    )
    return _oauth_redirect(redirect_uri, {'code': code, 'state': state})


@mcp_oauth_bp.route('/oauth/token', methods=['POST', 'OPTIONS'])
def token():
    if request.method == 'OPTIONS':
        return with_cors(json_response({}, 204))
    if request.mimetype and request.mimetype not in (
        'application/x-www-form-urlencoded',
        'application/x-www-form-urlencoded; charset=UTF-8',
    ):
        if request.form:
            pass
        else:
            return json_response(
                {'error': 'invalid_request', 'error_description': 'Use application/x-www-form-urlencoded'},
                400,
            )

    grant_type = request.form.get('grant_type')
    if grant_type == 'authorization_code':
        return _token_authorization_code()
    if grant_type == 'refresh_token':
        return _token_refresh()
    return json_response({'error': 'unsupported_grant_type'}, 400)


@mcp_oauth_bp.route('/oauth/revoke', methods=['POST', 'OPTIONS'])
def revoke():
    if request.method == 'OPTIONS':
        return with_cors(json_response({}, 204))
    token_value = request.form.get('token') or ''
    if token_value:
        revoke_presented_token(token_value)
    return json_response({}, 200)


def _token_authorization_code():
    code = request.form.get('code') or ''
    client_id = request.form.get('client_id') or ''
    redirect_uri = request.form.get('redirect_uri') or ''
    verifier = request.form.get('code_verifier') or ''
    requested_resource = (request.form.get('resource') or '').strip()

    row = lookup_authorization_code(code)
    if row is None:
        return json_response({'error': 'invalid_grant'}, 400)
    if row.client_id != client_id or row.redirect_uri != redirect_uri:
        return json_response({'error': 'invalid_grant'}, 400)
    if not pkce_matches(verifier, row.code_challenge):
        consume_authorization_code(row)
        return json_response({'error': 'invalid_grant', 'error_description': 'PKCE verification failed'}, 400)
    if requested_resource and not resource_matches(row.resource, requested_resource):
        consume_authorization_code(row)
        return json_response({'error': 'invalid_target'}, 400)

    grant = row.grant
    user = grant.user if grant else None
    ok, reason = grant_still_valid(grant, user)
    consume_authorization_code(row)
    if not ok:
        return json_response({'error': 'invalid_grant', 'error_description': reason}, 400)

    tokens = issue_token_pair(
        grant, scopes=list(row.scopes or []), resource=row.resource, client_id=client_id,
    )
    log_mcp_event(
        'mcp_token_issued',
        organization_id=grant.organization_id,
        actor_id=grant.user_id,
        description='MCP access token issued',
        event_data={'grant_id': grant.id, 'client_id': client_id},
    )
    return json_response(tokens)


def _token_refresh():
    refresh = request.form.get('refresh_token') or ''
    client_id = request.form.get('client_id') or ''
    row = load_refresh_token(refresh)
    if row is None or row.client_id != client_id:
        return json_response({'error': 'invalid_grant'}, 400)
    grant = row.grant
    user = grant.user if grant else None
    ok, reason = grant_still_valid(grant, user)
    if not ok:
        return json_response({'error': 'invalid_grant', 'error_description': reason}, 400)
    tokens = rotate_refresh_token(row, grant)
    log_mcp_event(
        'mcp_token_refreshed',
        organization_id=grant.organization_id,
        actor_id=grant.user_id,
        description='MCP refresh token rotated',
        event_data={'grant_id': grant.id, 'client_id': client_id},
    )
    return json_response(tokens)


def _known_resource(resource: str) -> bool:
    return any(
        resource_matches(resource, mcp_resource_url(readonly=flag))
        for flag in (False, True)
    )


def _oauth_redirect(redirect_uri: str, params: dict):
    parsed = urlparse(redirect_uri)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: value for key, value in params.items() if value is not None})
    target = urlunparse(parsed._replace(query=urlencode(query)))
    return redirect(target)
