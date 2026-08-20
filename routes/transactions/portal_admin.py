"""Agent-side management of client portal links for a transaction.

Lets the agent generate, copy, rotate, and revoke the private seller portal
link from the transaction detail page. All endpoints are agent-authenticated
and org-scoped; the portal itself (token-authenticated) lives in routes/portal.py.
"""
from flask import abort, jsonify, request, url_for
from flask_login import current_user, login_required

from models import (
    ClientPortalAccess,
    TransactionParticipant,
    db,
)
from services.transaction_auth import CAP_EDIT, CAP_VIEW, get_transaction_for_user
from . import transactions_bp
from .decorators import transactions_required

SELLER_ROLES = ('seller', 'co_seller')
BUYER_ROLES = ('buyer', 'co_buyer')
CLIENT_ROLES = SELLER_ROLES + BUYER_ROLES


def _get_transaction(id, capability=CAP_EDIT):
    transaction, decision = get_transaction_for_user(id, capability=capability)
    if not transaction:
        abort(403 if decision.reason != 'not_found' else 404)
    return transaction


def _link_url(access):
    return url_for('portal.home', token=access.token, _external=True)


def _serialize(participant, access):
    return {
        'participant_id': participant.id,
        'name': participant.name or (
            f'{participant.contact.first_name} {participant.contact.last_name}'.strip()
            if participant.contact else 'Seller'),
        'role': participant.role,
        'email': participant.display_email,
        'has_link': access is not None,
        'access_id': access.id if access else None,
        'url': _link_url(access) if access else None,
        'invite_code': (
            access.invite_code_display if access and access.invite_code else None),
        'view_count': access.view_count if access else 0,
        'last_viewed': (
            access.last_viewed_at.strftime('%b %d, %Y')
            if access and access.last_viewed_at else None),
    }


def _seller_participants(transaction):
    return TransactionParticipant.query.filter(
        TransactionParticipant.transaction_id == transaction.id,
        TransactionParticipant.organization_id == current_user.organization_id,
        TransactionParticipant.role.in_(SELLER_ROLES),
    ).all()


def _client_participants(transaction):
    """Seller + buyer participants eligible for portal links."""
    return TransactionParticipant.query.filter(
        TransactionParticipant.transaction_id == transaction.id,
        TransactionParticipant.organization_id == current_user.organization_id,
        TransactionParticipant.role.in_(CLIENT_ROLES),
    ).all()


def _active_link_for(transaction, participant_id):
    return ClientPortalAccess.query.filter_by(
        transaction_id=transaction.id,
        participant_id=participant_id,
        is_active=True,
    ).first()


@transactions_bp.route('/<int:id>/portal/status')
@login_required
@transactions_required
def portal_status(id):
    """List client (seller + buyer) participants and their active portal links."""
    transaction = _get_transaction(id, CAP_VIEW)
    rows = []
    for participant in _client_participants(transaction):
        access = _active_link_for(transaction, participant.id)
        rows.append(_serialize(participant, access))
    # Keep `sellers` key for existing UI; also expose `clients`.
    return jsonify({'success': True, 'sellers': rows, 'clients': rows})


@transactions_bp.route('/<int:id>/portal/create', methods=['POST'])
@login_required
@transactions_required
def portal_create_link(id):
    """Create (or return the existing) active portal link for a seller or buyer."""
    transaction = _get_transaction(id)
    data = request.get_json(silent=True) or request.form
    try:
        participant_id = int(data.get('participant_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Missing participant.'}), 400

    participant = TransactionParticipant.query.filter_by(
        id=participant_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first()
    if not participant or participant.role not in CLIENT_ROLES:
        return jsonify({
            'success': False,
            'error': 'Not a seller or buyer on this transaction.',
        }), 400

    access = _active_link_for(transaction, participant.id)
    if not access:
        access = ClientPortalAccess(
            organization_id=current_user.organization_id,
            transaction_id=transaction.id,
            participant_id=participant.id,
            token=ClientPortalAccess.generate_token(),
            invite_code=ClientPortalAccess.generate_invite_code(),
            session_version=1,
            is_active=True,
        )
        db.session.add(access)
        db.session.commit()
    else:
        access.ensure_invite_code()
        db.session.commit()

    return jsonify({'success': True, 'link': _serialize(participant, access)})


@transactions_bp.route('/<int:id>/portal/<int:access_id>/rotate', methods=['POST'])
@login_required
@transactions_required
def portal_rotate_link(id, access_id):
    """Issue a fresh token, invalidating the old link."""
    transaction = _get_transaction(id)
    access = ClientPortalAccess.query.filter_by(
        id=access_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    access.rotate_invite()
    db.session.commit()
    return jsonify({'success': True, 'link': _serialize(access.participant, access)})


@transactions_bp.route('/<int:id>/portal/<int:access_id>/revoke', methods=['POST'])
@login_required
@transactions_required
def portal_revoke_link(id, access_id):
    """Turn off a portal link. The seller's URL stops working immediately."""
    transaction = _get_transaction(id)
    access = ClientPortalAccess.query.filter_by(
        id=access_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    access.revoke()
    db.session.commit()
    return jsonify({'success': True, 'participant_id': access.participant_id})
