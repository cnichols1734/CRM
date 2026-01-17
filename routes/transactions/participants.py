# routes/transactions/participants.py
"""
Transaction participant management routes.
"""

from flask import request, jsonify, abort
from flask_login import login_required, current_user
from models import db, Transaction, TransactionParticipant, Contact
from services import audit_service
from . import transactions_bp
from .decorators import transactions_required


# =============================================================================
# PARTICIPANTS MANAGEMENT
# =============================================================================

@transactions_bp.route('/<int:id>/participants', methods=['POST'])
@login_required
@transactions_required
def add_participant(id):
    """Add a participant to a transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    try:
        role = request.form.get('role')
        contact_id = request.form.get('contact_id')
        
        if not role:
            return jsonify({'success': False, 'error': 'Role is required'}), 400
        
        if not contact_id:
            return jsonify({'success': False, 'error': 'Please select a contact'}), 400
        
        # Get and validate the contact
        contact = Contact.query.filter_by(id=int(contact_id), user_id=current_user.id).first()
        if not contact:
            return jsonify({'success': False, 'error': 'Contact not found'}), 404
        
        # Validate contact has required fields
        if not contact.first_name or not contact.last_name:
            return jsonify({
                'success': False, 
                'error': 'This contact is missing a name. Please update the contact first.'
            }), 400
        
        if not contact.email:
            return jsonify({
                'success': False, 
                'error': 'This contact is missing an email address. Please update the contact first.'
            }), 400
        
        participant = TransactionParticipant(
            organization_id=current_user.organization_id,
            transaction_id=transaction.id,
            role=role,
            contact_id=contact.id,
            name=f'{contact.first_name} {contact.last_name}',
            email=contact.email,
            phone=contact.phone,
            is_primary=False
        )
        db.session.add(participant)
        db.session.flush()  # Get participant ID

        # Log audit event
        audit_service.log_participant_added(transaction, participant)

        db.session.commit()

        return jsonify({
            'success': True,
            'participant': {
                'id': participant.id,
                'name': participant.display_name,
                'role': participant.role,
                'email': participant.display_email
            }
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/participants/<int:participant_id>', methods=['DELETE'])
@login_required
@transactions_required
def remove_participant(id, participant_id):
    """Remove a participant from a transaction."""
    transaction = Transaction.query.get_or_404(id)

    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)

    participant = TransactionParticipant.query.get_or_404(participant_id)

    if participant.transaction_id != transaction.id:
        abort(404)

    try:
        # Log audit event before deletion
        audit_service.log_participant_removed(transaction, participant)

        db.session.delete(participant)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
