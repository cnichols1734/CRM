"""Confirm / correct uploaded document identity and lifecycle destination."""

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from models import TransactionDocument, db
from services.document_classification_confirm import (
    ClassificationConfirmError,
    build_routing_context_payload,
    confirm_document_classification,
)
from services.transaction_auth import CAP_EDIT, CAP_VIEW, get_transaction_for_user
from . import transactions_bp
from .decorators import transactions_required


def _require_tx(transaction_id, capability=CAP_EDIT):
    tx, decision = get_transaction_for_user(transaction_id, capability=capability)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    return tx


@transactions_bp.route(
    '/<int:id>/documents/<int:doc_id>/classification/confirm',
    methods=['POST'],
)
@login_required
@transactions_required
def confirm_document_classification_route(id, doc_id):
    """Confirm identity + scope for an uploaded document (JSON)."""
    transaction = _require_tx(id, CAP_EDIT)
    document = TransactionDocument.query.filter_by(
        id=doc_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first()
    if not document:
        abort(404)

    payload = request.get_json(silent=True) or {}
    try:
        result = confirm_document_classification(
            transaction=transaction,
            document=document,
            actor_id=current_user.id,
            payload=payload,
        )
        db.session.commit()
        return jsonify(result)
    except ClassificationConfirmError as exc:
        db.session.rollback()
        status = getattr(exc, 'status', 400) or 400
        return jsonify({
            'success': False,
            'error': str(exc),
            'code': getattr(exc, 'code', 'invalid_confirmation'),
        }), status
    except Exception:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': 'Could not confirm document classification.',
            'code': 'confirm_failed',
        }), 500


@transactions_bp.route('/<int:id>/documents/routing-context', methods=['GET'])
@login_required
@transactions_required
def document_routing_context(id):
    """Expose side/stage/contract/offer facts for classification UI."""
    transaction = _require_tx(id, CAP_VIEW)
    return jsonify({
        'success': True,
        'routing_context': build_routing_context_payload(transaction),
    })
