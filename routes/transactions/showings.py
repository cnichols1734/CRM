"""Seller showing routes."""

from datetime import datetime

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from models import SellerShowing, Transaction, db
from . import transactions_bp
from .decorators import transactions_required


def _can_manage_transaction(transaction):
    return (
        transaction.created_by_id == current_user.id
        or getattr(current_user, 'role', None) == 'admin'
        or getattr(current_user, 'org_role', None) in ('admin', 'owner')
    )


def _get_seller_transaction(id):
    transaction = Transaction.query.filter_by(
        id=id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    if not _can_manage_transaction(transaction):
        abort(403)
    if transaction.transaction_type and transaction.transaction_type.name != 'seller':
        return None
    return transaction


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _showing_payload(showing):
    return {
        'id': showing.id,
        'status': showing.status,
        'showing_agent_name': showing.showing_agent_name,
        'showing_agent_email': showing.showing_agent_email,
        'showing_agent_phone': showing.showing_agent_phone,
        'showing_agent_brokerage': showing.showing_agent_brokerage,
        'buyer_name': showing.buyer_name,
        'scheduled_start_at': showing.scheduled_start_at.isoformat() if showing.scheduled_start_at else None,
        'scheduled_end_at': showing.scheduled_end_at.isoformat() if showing.scheduled_end_at else None,
        'feedback_received_at': showing.feedback_received_at.isoformat() if showing.feedback_received_at else None,
        'feedback_interest_level': showing.feedback_interest_level,
        'feedback_price_opinion': showing.feedback_price_opinion,
        'feedback_follow_up_requested': showing.feedback_follow_up_requested,
    }


@transactions_bp.route('/<int:id>/showings', methods=['GET'])
@login_required
@transactions_required
def list_seller_showings(id):
    """Return seller showings for a transaction."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Showings are only available for seller transactions'}), 400

    showings = transaction.seller_showings.order_by(SellerShowing.scheduled_start_at.desc()).all()
    return jsonify({
        'success': True,
        'showings': [_showing_payload(showing) for showing in showings],
    })


@transactions_bp.route('/<int:id>/showings', methods=['POST'])
@login_required
@transactions_required
def create_seller_showing(id):
    """Create a seller showing."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Showings are only available for seller transactions'}), 400

    data = request.get_json(silent=True) or request.form
    scheduled_start_at = _parse_datetime(data.get('scheduled_start_at'))
    if not data.get('showing_agent_name') or not scheduled_start_at:
        return jsonify({'success': False, 'error': 'Showing agent and start time are required'}), 400

    status = data.get('status') or SellerShowing.STATUS_SCHEDULED
    if status not in {
        SellerShowing.STATUS_PENDING_APPROVAL,
        SellerShowing.STATUS_APPROVED,
        SellerShowing.STATUS_SCHEDULED,
        SellerShowing.STATUS_COMPLETED,
        SellerShowing.STATUS_CANCELLED,
        SellerShowing.STATUS_DECLINED,
        SellerShowing.STATUS_NO_SHOW,
    }:
        return jsonify({'success': False, 'error': 'Invalid showing status'}), 400

    showing = SellerShowing(
        organization_id=current_user.organization_id,
        transaction_id=transaction.id,
        created_by_id=current_user.id,
        showing_agent_name=data.get('showing_agent_name'),
        showing_agent_email=data.get('showing_agent_email'),
        showing_agent_phone=data.get('showing_agent_phone'),
        showing_agent_brokerage=data.get('showing_agent_brokerage'),
        buyer_name=data.get('buyer_name'),
        source=data.get('source') or 'manual',
        requested_start_at=_parse_datetime(data.get('requested_start_at')),
        scheduled_start_at=scheduled_start_at,
        scheduled_end_at=_parse_datetime(data.get('scheduled_end_at')),
        status=status,
        access_instructions_snapshot=data.get('access_instructions_snapshot'),
        private_notes=data.get('private_notes'),
    )

    try:
        db.session.add(showing)
        db.session.commit()
        return jsonify({'success': True, 'showing': _showing_payload(showing)}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/showings/<int:showing_id>', methods=['POST', 'PATCH'])
@login_required
@transactions_required
def update_seller_showing(id, showing_id):
    """Update core showing details."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Showings are only available for seller transactions'}), 400

    showing = SellerShowing.query.filter_by(
        id=showing_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    data = request.get_json(silent=True) or request.form
    scheduled_start_at = _parse_datetime(data.get('scheduled_start_at'))
    if not data.get('showing_agent_name') or not scheduled_start_at:
        return jsonify({'success': False, 'error': 'Showing agent and start time are required'}), 400

    status = data.get('status') or showing.status
    if status not in {
        SellerShowing.STATUS_PENDING_APPROVAL,
        SellerShowing.STATUS_APPROVED,
        SellerShowing.STATUS_SCHEDULED,
        SellerShowing.STATUS_COMPLETED,
        SellerShowing.STATUS_CANCELLED,
        SellerShowing.STATUS_DECLINED,
        SellerShowing.STATUS_NO_SHOW,
    }:
        return jsonify({'success': False, 'error': 'Invalid showing status'}), 400

    showing.showing_agent_name = data.get('showing_agent_name')
    showing.showing_agent_email = data.get('showing_agent_email')
    showing.showing_agent_phone = data.get('showing_agent_phone')
    showing.showing_agent_brokerage = data.get('showing_agent_brokerage')
    showing.buyer_name = data.get('buyer_name')
    showing.source = data.get('source') or showing.source or 'manual'
    showing.requested_start_at = _parse_datetime(data.get('requested_start_at'))
    showing.scheduled_start_at = scheduled_start_at
    showing.scheduled_end_at = _parse_datetime(data.get('scheduled_end_at'))
    showing.status = status
    showing.access_instructions_snapshot = data.get('access_instructions_snapshot')
    showing.private_notes = data.get('private_notes')
    if status == SellerShowing.STATUS_APPROVED and not showing.approved_at:
        showing.approved_at = datetime.utcnow()
        showing.approved_by_id = current_user.id

    try:
        db.session.commit()
        return jsonify({'success': True, 'showing': _showing_payload(showing)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/showings/<int:showing_id>/feedback', methods=['POST'])
@login_required
@transactions_required
def update_seller_showing_feedback(id, showing_id):
    """Store post-showing feedback."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Showings are only available for seller transactions'}), 400

    showing = SellerShowing.query.filter_by(
        id=showing_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    data = request.get_json(silent=True) or request.form
    showing.feedback_received_at = datetime.utcnow()
    showing.feedback_interest_level = data.get('feedback_interest_level')
    showing.feedback_price_opinion = data.get('feedback_price_opinion')
    showing.feedback_condition_comments = data.get('feedback_condition_comments')
    showing.feedback_objections = data.get('feedback_objections')
    showing.feedback_likelihood = data.get('feedback_likelihood')
    showing.feedback_follow_up_requested = str(data.get('feedback_follow_up_requested', '')).lower() in ('1', 'true', 'yes', 'on')
    showing.feedback_notes = data.get('feedback_notes')
    if showing.status in (SellerShowing.STATUS_SCHEDULED, SellerShowing.STATUS_APPROVED):
        showing.status = SellerShowing.STATUS_COMPLETED

    try:
        db.session.commit()
        return jsonify({'success': True, 'showing': _showing_payload(showing)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
