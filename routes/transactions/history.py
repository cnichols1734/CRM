# routes/transactions/history.py
"""
Transaction audit history routes.
"""

from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user
from models import db, Transaction, TransactionDocument, AuditEvent
from services import audit_service
from . import transactions_bp
from .decorators import transactions_required


# =============================================================================
# AUDIT HISTORY API
# =============================================================================

@transactions_bp.route('/<int:id>/history')
@login_required
@transactions_required
def transaction_history(id):
    """
    Get the audit history for a transaction.
    Returns a paginated list of all events related to this transaction.
    """
    transaction = Transaction.query.get_or_404(id)

    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)

    # Get pagination params
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(per_page, 100)  # Max 100 per page

    # Get events
    events = audit_service.get_transaction_history(
        transaction_id=id,
        limit=per_page,
        offset=(page - 1) * per_page
    )

    # Format for display
    formatted_events = [audit_service.format_event_for_display(e) for e in events]

    # Get total count for pagination
    total = AuditEvent.query.filter_by(transaction_id=id).count()

    return jsonify({
        'success': True,
        'events': formatted_events,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': (total + per_page - 1) // per_page
        }
    })


@transactions_bp.route('/<int:id>/history/view')
@login_required
@transactions_required
def view_transaction_history(id):
    """
    Render the transaction history page.
    """
    transaction = Transaction.query.get_or_404(id)

    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)

    return render_template(
        'transactions/history.html',
        transaction=transaction
    )


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/history')
@login_required
@transactions_required
def document_history(id, doc_id):
    """
    Get the audit history for a specific document.
    """
    transaction = Transaction.query.get_or_404(id)

    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    doc = TransactionDocument.query.get_or_404(doc_id)

    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404

    # Get events for this document
    events = audit_service.get_document_history(document_id=doc_id)

    # Format for display
    formatted_events = [audit_service.format_event_for_display(e) for e in events]

    return jsonify({
        'success': True,
        'document': {
            'id': doc.id,
            'name': doc.template_name,
            'status': doc.status
        },
        'events': formatted_events
    })
