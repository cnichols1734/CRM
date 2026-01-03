# routes/transactions.py
"""
Transaction Management Routes
All routes protected by admin role + TRANSACTIONS_ENABLED feature flag
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from functools import wraps
from models import (
    db, Transaction, TransactionType, TransactionParticipant,
    TransactionDocument, DocumentSignature, Contact, User
)
from feature_flags import can_access_transactions

transactions_bp = Blueprint('transactions', __name__, url_prefix='/transactions')


def transactions_required(f):
    """Decorator to check if user can access transactions module."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not can_access_transactions(current_user):
            flash('You do not have access to this feature.', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# =============================================================================
# TRANSACTION LIST
# =============================================================================

@transactions_bp.route('/')
@login_required
@transactions_required
def list_transactions():
    """List all transactions for the current user."""
    # Get filter params
    status_filter = request.args.get('status', '')
    type_filter = request.args.get('type', '')
    
    # Base query - transactions created by current user
    query = Transaction.query.filter_by(created_by_id=current_user.id)
    
    # Apply filters
    if status_filter:
        query = query.filter_by(status=status_filter)
    if type_filter:
        query = query.filter_by(transaction_type_id=int(type_filter))
    
    # Order by most recent first
    transactions = query.order_by(Transaction.created_at.desc()).all()
    
    # Get transaction types for filter dropdown
    transaction_types = TransactionType.query.filter_by(is_active=True)\
        .order_by(TransactionType.sort_order).all()
    
    return render_template(
        'transactions/list.html',
        transactions=transactions,
        transaction_types=transaction_types,
        status_filter=status_filter,
        type_filter=type_filter
    )


# =============================================================================
# CREATE TRANSACTION
# =============================================================================

@transactions_bp.route('/new')
@login_required
@transactions_required
def new_transaction():
    """Show the create transaction form."""
    # Get transaction types for selection
    transaction_types = TransactionType.query.filter_by(is_active=True)\
        .order_by(TransactionType.sort_order).all()
    
    # Get contacts for the current user (for contact selection)
    contacts = Contact.query.filter_by(user_id=current_user.id)\
        .order_by(Contact.last_name, Contact.first_name).all()
    
    return render_template(
        'transactions/create.html',
        transaction_types=transaction_types,
        contacts=contacts
    )


@transactions_bp.route('/', methods=['POST'])
@login_required
@transactions_required
def create_transaction():
    """Create a new transaction."""
    try:
        # Get form data
        transaction_type_id = request.form.get('transaction_type_id')
        street_address = request.form.get('street_address')
        city = request.form.get('city')
        state = request.form.get('state', 'TX')
        zip_code = request.form.get('zip_code')
        county = request.form.get('county')
        ownership_status = request.form.get('ownership_status')
        contact_ids = request.form.getlist('contact_ids')
        
        # Validate required fields
        if not transaction_type_id:
            flash('Please select a transaction type.', 'error')
            return redirect(url_for('transactions.new_transaction'))
        
        if not street_address:
            flash('Please enter a property address.', 'error')
            return redirect(url_for('transactions.new_transaction'))
        
        if not contact_ids:
            flash('Please select at least one contact.', 'error')
            return redirect(url_for('transactions.new_transaction'))
        
        # Create the transaction
        transaction = Transaction(
            created_by_id=current_user.id,
            transaction_type_id=int(transaction_type_id),
            street_address=street_address,
            city=city,
            state=state,
            zip_code=zip_code,
            county=county,
            ownership_status=ownership_status,
            status='draft'
        )
        db.session.add(transaction)
        db.session.flush()  # Get the transaction ID
        
        # Get the transaction type to determine participant role
        tx_type = TransactionType.query.get(int(transaction_type_id))
        
        # Determine the role based on transaction type
        role_map = {
            'seller': 'seller',
            'buyer': 'buyer',
            'landlord': 'landlord',
            'tenant': 'tenant',
            'referral': 'referral_client'
        }
        participant_role = role_map.get(tx_type.name, 'client')
        
        # Add contacts as participants
        for i, contact_id in enumerate(contact_ids):
            contact = Contact.query.get(int(contact_id))
            if contact and contact.user_id == current_user.id:
                participant = TransactionParticipant(
                    transaction_id=transaction.id,
                    contact_id=contact.id,
                    role=participant_role if i == 0 else f'co_{participant_role}',
                    is_primary=(i == 0)
                )
                db.session.add(participant)
        
        # Add current user as listing agent (for seller/landlord transactions)
        if tx_type.name in ['seller', 'landlord']:
            agent_participant = TransactionParticipant(
                transaction_id=transaction.id,
                user_id=current_user.id,
                role='listing_agent',
                is_primary=True
            )
            db.session.add(agent_participant)
        elif tx_type.name in ['buyer', 'tenant']:
            agent_participant = TransactionParticipant(
                transaction_id=transaction.id,
                user_id=current_user.id,
                role='buyers_agent',
                is_primary=True
            )
            db.session.add(agent_participant)
        
        db.session.commit()
        
        flash('Transaction created successfully!', 'success')
        return redirect(url_for('transactions.view_transaction', id=transaction.id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating transaction: {str(e)}', 'error')
        return redirect(url_for('transactions.new_transaction'))


# =============================================================================
# VIEW/EDIT TRANSACTION
# =============================================================================

@transactions_bp.route('/<int:id>')
@login_required
@transactions_required
def view_transaction(id):
    """View a single transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    # Ensure user owns this transaction
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    # Get participants grouped by role
    participants = transaction.participants.all()
    
    # Get documents
    documents = transaction.documents.order_by(TransactionDocument.created_at).all()
    
    return render_template(
        'transactions/detail.html',
        transaction=transaction,
        participants=participants,
        documents=documents
    )


@transactions_bp.route('/<int:id>/edit')
@login_required
@transactions_required
def edit_transaction(id):
    """Show edit form for a transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    transaction_types = TransactionType.query.filter_by(is_active=True)\
        .order_by(TransactionType.sort_order).all()
    
    return render_template(
        'transactions/edit.html',
        transaction=transaction,
        transaction_types=transaction_types
    )


@transactions_bp.route('/<int:id>', methods=['POST'])
@login_required
@transactions_required
def update_transaction(id):
    """Update a transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    try:
        # Update fields
        transaction.street_address = request.form.get('street_address', transaction.street_address)
        transaction.city = request.form.get('city', transaction.city)
        transaction.state = request.form.get('state', transaction.state)
        transaction.zip_code = request.form.get('zip_code', transaction.zip_code)
        transaction.county = request.form.get('county', transaction.county)
        transaction.ownership_status = request.form.get('ownership_status', transaction.ownership_status)
        transaction.status = request.form.get('status', transaction.status)
        
        # Parse expected close date if provided
        expected_close = request.form.get('expected_close_date')
        if expected_close:
            from datetime import datetime
            transaction.expected_close_date = datetime.strptime(expected_close, '%Y-%m-%d').date()
        
        db.session.commit()
        flash('Transaction updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating transaction: {str(e)}', 'error')
    
    return redirect(url_for('transactions.view_transaction', id=id))


# =============================================================================
# PARTICIPANTS MANAGEMENT
# =============================================================================

@transactions_bp.route('/<int:id>/participants', methods=['POST'])
@login_required
@transactions_required
def add_participant(id):
    """Add a participant to a transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    try:
        role = request.form.get('role')
        contact_id = request.form.get('contact_id')
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        company = request.form.get('company')
        
        if not role:
            return jsonify({'success': False, 'error': 'Role is required'}), 400
        
        participant = TransactionParticipant(
            transaction_id=transaction.id,
            role=role,
            contact_id=int(contact_id) if contact_id else None,
            name=name,
            email=email,
            phone=phone,
            company=company,
            is_primary=False
        )
        db.session.add(participant)
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
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    participant = TransactionParticipant.query.get_or_404(participant_id)
    
    if participant.transaction_id != transaction.id:
        abort(404)
    
    try:
        db.session.delete(participant)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# API ENDPOINTS
# =============================================================================

@transactions_bp.route('/api/contacts/search')
@login_required
@transactions_required
def search_contacts():
    """Search contacts for the contact picker."""
    query = request.args.get('q', '')
    
    contacts = Contact.query.filter_by(user_id=current_user.id)
    
    if query:
        search = f'%{query}%'
        contacts = contacts.filter(
            db.or_(
                Contact.first_name.ilike(search),
                Contact.last_name.ilike(search),
                Contact.email.ilike(search)
            )
        )
    
    contacts = contacts.order_by(Contact.last_name, Contact.first_name).limit(20).all()
    
    return jsonify([{
        'id': c.id,
        'name': f'{c.first_name} {c.last_name}',
        'email': c.email,
        'phone': c.phone
    } for c in contacts])


@transactions_bp.route('/<int:id>/status', methods=['POST'])
@login_required
@transactions_required
def update_status(id):
    """Update transaction status via AJAX."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    new_status = data.get('status')
    
    valid_statuses = ['draft', 'active', 'pending', 'under_contract', 'closed', 'cancelled']
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    
    try:
        transaction.status = new_status
        db.session.commit()
        return jsonify({'success': True, 'status': new_status})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# INTAKE QUESTIONNAIRE
# =============================================================================

@transactions_bp.route('/<int:id>/intake')
@login_required
@transactions_required
def intake_questionnaire(id):
    """Show the intake questionnaire for a transaction."""
    from services.intake_service import get_intake_schema
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    # Get the intake schema based on transaction type and ownership status
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        flash('No intake questionnaire available for this transaction type.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    return render_template(
        'transactions/intake.html',
        transaction=transaction,
        schema=schema,
        intake_data=transaction.intake_data or {}
    )


@transactions_bp.route('/<int:id>/intake', methods=['POST'])
@login_required
@transactions_required
def save_intake(id):
    """Save intake questionnaire answers."""
    from services.intake_service import get_intake_schema, validate_intake_data
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    # Get the schema
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        return jsonify({'success': False, 'error': 'Schema not found'}), 404
    
    # Parse the incoming data
    data = request.get_json() if request.is_json else None
    
    if data is None:
        # Handle form submission
        intake_data = {}
        for section in schema.get('sections', []):
            for question in section.get('questions', []):
                field_id = question['id']
                value = request.form.get(field_id)
                
                # Convert boolean fields
                if question['type'] == 'boolean':
                    intake_data[field_id] = value == 'true' or value == 'yes'
                else:
                    intake_data[field_id] = value
    else:
        intake_data = data.get('intake_data', {})
    
    try:
        # Save the intake data
        transaction.intake_data = intake_data
        db.session.commit()
        
        if request.is_json:
            return jsonify({'success': True, 'intake_data': intake_data})
        else:
            flash('Questionnaire saved successfully!', 'success')
            return redirect(url_for('transactions.view_transaction', id=id))
            
    except Exception as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({'success': False, 'error': str(e)}), 500
        else:
            flash(f'Error saving questionnaire: {str(e)}', 'error')
            return redirect(url_for('transactions.intake_questionnaire', id=id))


@transactions_bp.route('/<int:id>/intake/generate-package', methods=['POST'])
@login_required
@transactions_required
def generate_document_package(id):
    """Generate the document package based on intake answers."""
    from services.intake_service import get_intake_schema, evaluate_document_rules, validate_intake_data
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    # Get the schema
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        flash('Schema not found for this transaction type.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    # Validate that all required questions are answered
    is_valid, missing = validate_intake_data(schema, transaction.intake_data or {})
    
    if not is_valid:
        flash(f'Please answer all required questions before generating the document package.', 'error')
        return redirect(url_for('transactions.intake_questionnaire', id=id))
    
    # Evaluate document rules
    required_docs = evaluate_document_rules(schema, transaction.intake_data)
    
    try:
        # Clear existing documents (in case regenerating)
        TransactionDocument.query.filter_by(transaction_id=transaction.id).delete()
        
        # Create TransactionDocument records for each required doc
        for doc in required_docs:
            tx_doc = TransactionDocument(
                transaction_id=transaction.id,
                template_slug=doc['slug'],
                template_name=doc['name'],
                included_reason=doc['reason'] if not doc.get('always') else None,
                status='pending'
            )
            db.session.add(tx_doc)
        
        db.session.commit()
        
        flash(f'Document package generated with {len(required_docs)} documents!', 'success')
        return redirect(url_for('transactions.view_transaction', id=id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating document package: {str(e)}', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))

