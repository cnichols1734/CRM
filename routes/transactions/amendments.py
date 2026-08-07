"""Seller contract amendment review + accept/reject routes."""

import logging

from flask import abort, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from models import SellerContractAmendment, TransactionDocument, db
from services import amendment_service
from services.transaction_auth import CAP_EDIT, CAP_VIEW, get_transaction_for_user
from . import transactions_bp
from .decorators import transactions_required

logger = logging.getLogger(__name__)


def _load_amendment(tx, amendment_id):
    return SellerContractAmendment.query.filter_by(
        id=amendment_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first()


def _humanize_amendment_type(value):
    text = (value or 'amendment').replace('_', ' ').replace('-', ' ').strip()
    if not text:
        return 'Amendment'
    return text[:1].upper() + text[1:]


def _status_badge(status):
    tone_map = {
        'received': 'warning',
        'reviewing': 'info',
        'countered': 'warning',
        'accepted': 'success',
        'rejected': 'danger',
        'withdrawn': 'neutral',
    }
    label = (status or 'received').replace('_', ' ').title()
    return label, tone_map.get(status or 'received', 'neutral')


@transactions_bp.route('/<int:id>/amendments/<int:amendment_id>', methods=['GET'])
@login_required
@transactions_required
def amendment_review(id, amendment_id):
    """Full-page PDF + proposed-changes review for a contract amendment."""
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    amendment = _load_amendment(tx, amendment_id)
    if not amendment:
        abort(404)

    diff = amendment_service.diff_against_contract(amendment)
    version = amendment_service.current_version(amendment)

    pdf_url = None
    if version and version.transaction_document_id:
        doc = TransactionDocument.query.filter_by(
            id=version.transaction_document_id,
            transaction_id=tx.id,
            organization_id=current_user.organization_id,
        ).first()
        if doc:
            pdf_url = url_for('transactions.view_document_pdf', id=tx.id, doc_id=doc.id)

    changed_count = sum(1 for entry in diff if entry.get('changed'))
    status_label, status_tone = _status_badge(amendment.status)
    amendment_label = _humanize_amendment_type(amendment.amendment_type)
    version_direction_label = amendment_service.amendment_direction_label(
        getattr(version, 'direction', None) if version else None,
    )
    return_url = url_for('transactions.view_transaction', id=tx.id)
    accept_url = url_for(
        'transactions.accept_amendment',
        id=tx.id,
        amendment_id=amendment.id,
    )
    reject_url = url_for(
        'transactions.reject_amendment',
        id=tx.id,
        amendment_id=amendment.id,
    )
    can_decide = amendment.status in ('received', 'reviewing', 'countered')

    return render_template(
        'transactions/amendment_review.html',
        transaction=tx,
        amendment=amendment,
        diff=diff,
        changed_count=changed_count,
        pdf_url=pdf_url,
        return_url=return_url,
        accept_url=accept_url,
        reject_url=reject_url,
        amendment_label=amendment_label,
        version_direction_label=version_direction_label,
        status_label=status_label,
        status_tone=status_tone,
        can_decide=can_decide,
    )


@transactions_bp.route(
    '/<int:id>/amendments/<int:amendment_id>/accept',
    methods=['POST'],
)
@login_required
@transactions_required
def accept_amendment(id, amendment_id):
    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    amendment = _load_amendment(tx, amendment_id)
    if not amendment:
        abort(404)

    data = request.get_json(silent=True) or {}
    if 'selected' not in data:
        selected_keys = None
    else:
        selected = data.get('selected') or {}
        if not isinstance(selected, dict):
            return jsonify({'success': False, 'error': 'selected must be an object'}), 400
        selected_keys = [str(key) for key, value in selected.items() if value]

    try:
        result = amendment_service.accept(
            amendment,
            actor_id=current_user.id,
            selected_keys=selected_keys,
        )
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception:
        logger.exception(
            'accept_amendment failed tx=%s amendment=%s', id, amendment_id,
        )
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Could not apply this amendment. The change was not saved.',
        }), 500

    return jsonify({'success': True, **result})


@transactions_bp.route(
    '/<int:id>/amendments/<int:amendment_id>/reject',
    methods=['POST'],
)
@login_required
@transactions_required
def reject_amendment(id, amendment_id):
    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    amendment = _load_amendment(tx, amendment_id)
    if not amendment:
        abort(404)

    data = request.get_json(silent=True) or {}
    reason = data.get('reason')
    if reason is not None:
        reason = str(reason).strip() or None

    try:
        amendment_service.reject(
            amendment,
            actor_id=current_user.id,
            reason=reason,
        )
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception:
        logger.exception(
            'reject_amendment failed tx=%s amendment=%s', id, amendment_id,
        )
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Could not reject this amendment.',
        }), 500

    return jsonify({'success': True, 'amendment_id': amendment.id, 'status': amendment.status})
