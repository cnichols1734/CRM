"""JSON API for the client iPhone app.

Wraps the existing client portal. Auth is a ClientPortalAccess grant:
invite code in, short-lived JWT out. Flask-Login cookies are ignored.
Do not put the grant token in a URL.
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, g, jsonify, redirect, request
from sqlalchemy import text

from models import DeviceToken, db, PortalMessage, SellerShowing
from services.client_portal_auth import (
    JWT_TTL_SECONDS,
    branding_for_access,
    exchange_invite_code,
    issue_client_jwt,
    load_access_from_jwt,
)
from services.portal_service import (
    CLIENT_PORTAL_ROLES,
    SELLER_ROLES,
    _participant_first_name,
    client_document_file_url,
    documents_for_client_api,
    list_client_messages,
    milestones_for_client_api,
    serialize_client_deal,
    showings_for_client_api,
)

logger = logging.getLogger(__name__)

client_api_bp = Blueprint('client_api', __name__, url_prefix='/api/client/v1')


def _json_error(message, status=401):
    return jsonify({'error': message}), status


def _set_org_context(org_id: int) -> None:
    try:
        db.session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, false)"),
            {'org_id': str(org_id)},
        )
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


@client_api_bp.teardown_request
def _reset_org_context(exc=None):
    try:
        db.session.execute(text('RESET app.current_org_id'))
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _bearer_token():
    header = request.headers.get('Authorization') or ''
    if header.lower().startswith('bearer '):
        return header[7:].strip()
    return ''


def _role_allowed(role):
    return (role or '').strip().lower() in CLIENT_PORTAL_ROLES


def _invite_from_request():
    """Native posts invite_code. Older clients may still send code."""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        if 'invite_code' in data:
            return data.get('invite_code')
        if 'code' in data:
            return data.get('code')
    return request.form.get('invite_code') or request.form.get('code')


def client_jwt_required(view):
    """Authorize from the client JWT only. A CRM cookie is not enough."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        if not token:
            return _json_error('Sign in with your invite code first.', 401)
        access, error = load_access_from_jwt(token)
        if error:
            return _json_error(error, 401)

        _set_org_context(access.organization_id)

        tx = access.transaction
        participant = access.participant
        if (
            tx is None
            or participant is None
            or participant.transaction_id != tx.id
            or not _role_allowed(participant.role)
        ):
            return _json_error('This invite is no longer active.', 401)

        g.client_access = access
        return view(access, *args, **kwargs)

    return wrapper


@client_api_bp.route('/session', methods=['POST'])
def create_session():
    access, error = exchange_invite_code(_invite_from_request())
    if error:
        return _json_error(error, 422)

    _set_org_context(access.organization_id)
    tx = access.transaction
    participant = access.participant
    if (
        tx is None
        or participant is None
        or participant.transaction_id != tx.id
        or not _role_allowed(participant.role)
    ):
        return _json_error('This invite is no longer active.', 422)

    try:
        access.record_view()
        db.session.commit()
    except Exception:
        db.session.rollback()

    token = issue_client_jwt(access)
    return jsonify({
        'token': token,
        'token_type': 'Bearer',
        'expires_in': JWT_TTL_SECONDS,
        'participant_first_name': _participant_first_name(participant),
        'role': (participant.role or 'seller').lower(),
        'branding': branding_for_access(access),
    })


@client_api_bp.route('/session', methods=['DELETE'])
@client_jwt_required
def delete_session(access):
    access.bump_session()
    db.session.commit()
    return jsonify({'ok': True})


@client_api_bp.route('/deal', methods=['GET'])
@client_jwt_required
def get_deal(access):
    deal = serialize_client_deal(access)
    deal['branding'] = branding_for_access(access)
    return jsonify(deal)


@client_api_bp.route('/messages', methods=['GET'])
@client_jwt_required
def get_messages(access):
    return jsonify({'messages': list_client_messages(access)})


@client_api_bp.route('/messages', methods=['POST'])
@client_jwt_required
def post_message(access):
    data = request.get_json(silent=True) or {}
    body = (data.get('body') or request.form.get('body') or '').strip()
    if not body:
        return _json_error('Write a message before sending.', 400)
    if len(body) > 4000:
        body = body[:4000]

    msg = PortalMessage(
        organization_id=access.organization_id,
        transaction_id=access.transaction_id,
        participant_id=access.participant_id,
        sender='client',
        kind='message',
        body=body,
    )
    db.session.add(msg)
    db.session.commit()

    try:
        from services.device_push import enqueue_portal_push
        enqueue_portal_push(msg)
    except Exception:
        logger.exception('Client API: failed to enqueue APNs for client message.')

    try:
        from routes.portal import _notify_agent_of_message
        _notify_agent_of_message(access, body)
    except Exception:
        logger.exception('Client API: failed to notify agent of client message.')

    return jsonify({
        'message': list_client_messages(access)[-1],
    }), 201


@client_api_bp.route('/milestones', methods=['GET'])
@client_jwt_required
def get_milestones(access):
    return jsonify(milestones_for_client_api(access))


@client_api_bp.route('/documents', methods=['GET'])
@client_jwt_required
def get_documents(access):
    return jsonify(documents_for_client_api(access))


@client_api_bp.route('/documents/<int:doc_id>/file', methods=['GET'])
@client_jwt_required
def get_document_file(access, doc_id):
    url, error_status = client_document_file_url(access, doc_id)
    if error_status:
        if error_status == 403:
            return _json_error('That document is not yours.', 403)
        return _json_error('Document not found.', 404)
    return redirect(url)


@client_api_bp.route('/devices', methods=['POST'])
@client_jwt_required
def register_client_device(access):
    data = request.get_json(silent=True) or {}
    try:
        from services.device_push import register_device
        row = register_device(
            organization_id=access.organization_id,
            audience=DeviceToken.AUDIENCE_CLIENT,
            token=data.get('token'),
            platform=data.get('platform') or request.form.get('platform'),
            participant_id=access.participant_id,
        )
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return jsonify({
        'ok': True,
        'device_id': row.id,
        'platform': row.platform,
    }), 201


@client_api_bp.route('/showings', methods=['GET'])
@client_jwt_required
def get_showings(access):
    return jsonify(showings_for_client_api(access))


def _showing_for_access(access, showing_id):
    role = (getattr(access.participant, 'role', None) or '').lower()
    if role not in SELLER_ROLES:
        return None, _json_error('Showing approvals are for sellers on this listing.', 403)
    showing = SellerShowing.query.filter_by(
        id=showing_id,
        transaction_id=access.transaction_id,
        organization_id=access.organization_id,
    ).first()
    if not showing:
        return None, _json_error('Showing not found.', 404)
    if showing.status != SellerShowing.STATUS_PENDING_APPROVAL:
        return None, _json_error('This showing is not waiting for your approval.', 409)
    return showing, None


@client_api_bp.route('/showings/<int:showing_id>/approve', methods=['POST'])
@client_jwt_required
def approve_showing(access, showing_id):
    from datetime import datetime

    showing, error_response = _showing_for_access(access, showing_id)
    if error_response:
        return error_response
    showing.status = SellerShowing.STATUS_APPROVED
    showing.approved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        'ok': True,
        'showing_id': showing.id,
        'status': showing.status,
    })


@client_api_bp.route('/showings/<int:showing_id>/decline', methods=['POST'])
@client_jwt_required
def decline_showing(access, showing_id):
    showing, error_response = _showing_for_access(access, showing_id)
    if error_response:
        return error_response
    showing.status = SellerShowing.STATUS_DECLINED
    db.session.commit()
    return jsonify({
        'ok': True,
        'showing_id': showing.id,
        'status': showing.status,
    })
