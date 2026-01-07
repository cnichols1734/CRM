# routes/transactions.py
"""
Transaction Management Routes
All routes protected by admin role + TRANSACTIONS_ENABLED feature flag
"""

from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from functools import wraps
from models import (
    db, Transaction, TransactionType, TransactionParticipant,
    TransactionDocument, DocumentSignature, Contact, User
)
from feature_flags import can_access_transactions
from services.document_registry import (
    DOCUMENT_REGISTRY, get_specialized_slugs, get_configs_for_slugs, get_document_config
)

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
    search_query = request.args.get('q', '').strip()
    
    # Base query - transactions created by current user
    query = Transaction.query.filter_by(created_by_id=current_user.id)
    
    # Apply filters
    if status_filter:
        query = query.filter_by(status=status_filter)
    if type_filter:
        query = query.filter_by(transaction_type_id=int(type_filter))
    
    # Apply search filter (address or contact name)
    if search_query:
        search_term = f'%{search_query}%'
        # Get transaction IDs that match participant names
        matching_participant_tx_ids = db.session.query(TransactionParticipant.transaction_id).join(
            Contact, TransactionParticipant.contact_id == Contact.id
        ).filter(
            db.or_(
                Contact.first_name.ilike(search_term),
                Contact.last_name.ilike(search_term),
                db.func.concat(Contact.first_name, ' ', Contact.last_name).ilike(search_term)
            )
        ).distinct().all()
        matching_tx_ids = [tx_id for (tx_id,) in matching_participant_tx_ids]
        
        # Also check participant name field (for external parties)
        external_match_ids = db.session.query(TransactionParticipant.transaction_id).filter(
            TransactionParticipant.name.ilike(search_term)
        ).distinct().all()
        matching_tx_ids.extend([tx_id for (tx_id,) in external_match_ids])
        
        # Filter by address OR matching participant
        query = query.filter(
            db.or_(
                Transaction.street_address.ilike(search_term),
                Transaction.city.ilike(search_term),
                Transaction.id.in_(matching_tx_ids) if matching_tx_ids else False
            )
        )
    
    # Order by most recent first
    transactions = query.order_by(Transaction.created_at.desc()).all()
    
    # Build a dict of primary contacts for each transaction
    transaction_contacts = {}
    for tx in transactions:
        # Get the primary client participant (seller, buyer, etc.)
        primary_participant = tx.participants.filter_by(is_primary=True).filter(
            TransactionParticipant.role.in_(['seller', 'buyer', 'landlord', 'tenant', 'referral_client'])
        ).first()
        if primary_participant:
            transaction_contacts[tx.id] = {
                'name': primary_participant.display_name,
                'email': primary_participant.display_email,
                'contact_id': primary_participant.contact_id
            }
    
    # Get transaction types for filter dropdown
    transaction_types = TransactionType.query.filter_by(is_active=True)\
        .order_by(TransactionType.sort_order).all()
    
    return render_template(
        'transactions/list.html',
        transactions=transactions,
        transaction_types=transaction_types,
        transaction_contacts=transaction_contacts,
        status_filter=status_filter,
        type_filter=type_filter,
        search_query=search_query
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
            status='preparing_to_list'
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


@transactions_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
@transactions_required
def delete_transaction(id):
    """Delete a transaction and all related data."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    try:
        # Get transaction address for flash message
        address = transaction.street_address
        
        # Delete the transaction - cascade will handle:
        # - TransactionParticipants (cascade='all, delete-orphan')
        # - TransactionDocuments (cascade='all, delete-orphan')
        #   - DocumentSignatures (cascade='all, delete-orphan' via TransactionDocument)
        db.session.delete(transaction)
        db.session.commit()
        
        flash(f'Transaction for "{address}" has been deleted.', 'success')
        return redirect(url_for('transactions.list_transactions'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting transaction: {str(e)}', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))


@transactions_bp.route('/<int:id>', methods=['POST'])
@login_required
@transactions_required
def update_transaction(id):
    """Update a transaction."""
    from datetime import datetime as dt
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    try:
        # Update fields
        transaction.street_address = request.form.get('street_address', transaction.street_address)
        transaction.city = request.form.get('city') or None
        transaction.state = request.form.get('state', transaction.state)
        transaction.zip_code = request.form.get('zip_code') or None
        transaction.county = request.form.get('county') or None
        transaction.ownership_status = request.form.get('ownership_status') or None
        transaction.status = request.form.get('status', transaction.status)
        
        # Parse expected close date if provided
        expected_close = request.form.get('expected_close_date')
        if expected_close:
            transaction.expected_close_date = dt.strptime(expected_close, '%Y-%m-%d').date()
        else:
            transaction.expected_close_date = None
        
        # Parse actual close date if provided
        actual_close = request.form.get('actual_close_date')
        if actual_close:
            transaction.actual_close_date = dt.strptime(actual_close, '%Y-%m-%d').date()
        else:
            transaction.actual_close_date = None
        
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


@transactions_bp.route('/api/<int:id>/signers')
@login_required
@transactions_required
def get_signers(id):
    """Get list of signers for a transaction document."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'signers': [], 'error': 'Unauthorized'}), 403
    
    participants = transaction.participants.all()
    signers = []
    
    # Add sellers
    for p in participants:
        if p.role in ['seller', 'co_seller'] and p.display_email:
            signers.append({
                'name': p.display_name,
                'email': p.display_email,
                'role': 'Seller' if p.role == 'seller' else 'Co-Seller'
            })
    
    # Add listing agent (current user)
    for p in participants:
        if p.role == 'listing_agent':
            signers.append({
                'name': p.display_name,
                'email': p.display_email or current_user.email,
                'role': 'Listing Agent'
            })
            break
    
    return jsonify({'signers': signers})


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
    
    valid_statuses = ['preparing_to_list', 'active', 'under_contract', 'closed', 'cancelled']
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


# =============================================================================
# DOCUMENT MANAGEMENT
# =============================================================================

@transactions_bp.route('/<int:id>/documents', methods=['POST'])
@login_required
@transactions_required
def add_document(id):
    """Add a document to a transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    try:
        template_slug = request.form.get('template_slug')
        template_name = request.form.get('template_name')
        reason = request.form.get('reason', 'Manually added')
        
        if not template_slug or not template_name:
            return jsonify({'success': False, 'error': 'Document type is required'}), 400
        
        # Check if document already exists
        existing = TransactionDocument.query.filter_by(
            transaction_id=transaction.id,
            template_slug=template_slug
        ).first()
        
        if existing:
            return jsonify({'success': False, 'error': 'This document already exists in the package'}), 400
        
        doc = TransactionDocument(
            transaction_id=transaction.id,
            template_slug=template_slug,
            template_name=template_name,
            included_reason=reason,
            status='pending'
        )
        db.session.add(doc)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'document': {
                'id': doc.id,
                'name': doc.template_name,
                'status': doc.status
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>', methods=['DELETE'])
@login_required
@transactions_required
def remove_document(id, doc_id):
    """Remove a document from a transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    try:
        db.session.delete(doc)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/form')
@login_required
@transactions_required
def document_form(id, doc_id):
    """Display the form for filling out a document."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        abort(404)
    
    # Get participants for the form
    participants = transaction.participants.all()
    
    # Prefill data from transaction and intake
    prefill_data = build_prefill_data(transaction, participants)
    
    # Merge with any existing field data
    if doc.field_data:
        prefill_data.update(doc.field_data)
    
    # Route to specialized form templates based on document type
    if doc.template_slug == 'listing-agreement':
        return render_template(
            'transactions/listing_agreement_form.html',
            transaction=transaction,
            document=doc,
            participants=participants,
            prefill_data=prefill_data
        )
    
    if doc.template_slug == 'hoa-addendum':
        return render_template(
            'transactions/hoa_addendum_form.html',
            transaction=transaction,
            document=doc,
            participants=participants,
            prefill_data=prefill_data
        )
    
    if doc.template_slug == 'flood-hazard':
        return render_template(
            'transactions/flood_hazard_form.html',
            transaction=transaction,
            document=doc,
            participants=participants,
            prefill_data=prefill_data
        )
    
    if doc.template_slug == 'seller-net-proceeds':
        return render_template(
            'transactions/seller_net_proceeds_form.html',
            transaction=transaction,
            document=doc,
            participants=participants,
            prefill_data=prefill_data
        )
    
    # Default generic form
    return render_template(
        'transactions/document_form.html',
        transaction=transaction,
        document=doc,
        participants=participants,
        prefill_data=prefill_data
    )


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/form', methods=['POST'])
@login_required
@transactions_required
def save_document_form(id, doc_id):
    """Save the document form data."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        abort(404)
    
    try:
        # Get form data
        if request.is_json:
            field_data = request.get_json().get('field_data', {})
        else:
            # Convert form data to dict
            field_data = {}
            for key in request.form:
                if key.startswith('field_'):
                    field_data[key[6:]] = request.form.get(key)
        
        # Save field data
        doc.field_data = field_data
        doc.status = 'filled'
        db.session.commit()
        
        if request.is_json:
            return jsonify({'success': True, 'status': doc.status})
        else:
            # Check if this is "Save & Continue" (redirect to preview) or just "Save Draft"
            action = request.form.get('submit_action', 'save')
            
            if action == 'continue':
                # Redirect to document preview
                return redirect(url_for('transactions.document_preview', id=id, doc_id=doc_id))
            else:
                flash('Document form saved successfully!', 'success')
                return redirect(url_for('transactions.view_transaction', id=id))
            
    except Exception as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({'success': False, 'error': str(e)}), 500
        else:
            flash(f'Error saving form: {str(e)}', 'error')
            return redirect(url_for('transactions.document_form', id=id, doc_id=doc_id))


# =============================================================================
# FILL ALL DOCUMENTS
# =============================================================================

# Document slugs with specialized form UIs are defined in services/document_registry.py
# Use get_specialized_slugs() to get the list dynamically


@transactions_bp.route('/<int:id>/documents/fill-all')
@login_required
@transactions_required
def fill_all_documents(id):
    """
    Show a combined form experience for filling multiple documents at once.
    Only includes documents that have specialized form UIs (defined in document_registry).
    """
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    # Get document slugs that have specialized forms from registry
    specialized_slugs = get_specialized_slugs()
    
    # Get all documents for this transaction that have specialized forms
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(specialized_slugs)
    ).order_by(TransactionDocument.created_at).all()
    
    if not documents:
        flash('No documents with specialized forms available. Use individual document fill for other documents.', 'info')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    # Get participants for prefill
    participants = transaction.participants.all()
    
    # Build prefill data (shared across all documents)
    prefill_data = build_prefill_data(transaction, participants)
    
    # Merge in any existing field data from all documents
    for doc in documents:
        if doc.field_data:
            # Prefix document-specific fields with doc slug to avoid collisions
            for key, value in doc.field_data.items():
                # Store both prefixed (for doc-specific) and unprefixed (for shared fields)
                prefill_data[f"{doc.template_slug}_{key}"] = value
                # Also store unprefixed for shared fields
                if key not in prefill_data:
                    prefill_data[key] = value
    
    # Get document configs for template (for dynamic styling/includes)
    doc_slugs = [doc.template_slug for doc in documents]
    doc_configs = get_configs_for_slugs(doc_slugs)
    
    return render_template(
        'transactions/fill_all_documents.html',
        transaction=transaction,
        documents=documents,
        participants=participants,
        prefill_data=prefill_data,
        doc_configs=doc_configs  # Pass registry configs for dynamic template rendering
    )


@transactions_bp.route('/<int:id>/documents/fill-all', methods=['POST'])
@login_required
@transactions_required
def save_all_documents(id):
    """
    Save form data for multiple documents at once.
    Form fields are prefixed with doc slug to separate document-specific data.
    """
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    # Get document slugs that have specialized forms from registry
    specialized_slugs = get_specialized_slugs()
    
    # Get documents with specialized forms
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(specialized_slugs)
    ).all()
    
    try:
        for doc in documents:
            # Extract fields for this document
            field_data = {}
            doc_prefix = f"doc_{doc.id}_field_"
            
            for key in request.form:
                if key.startswith(doc_prefix):
                    # Remove the doc-specific prefix and 'field_' prefix
                    field_name = key[len(doc_prefix):]
                    field_data[field_name] = request.form.get(key)
            
            # Only update if we have data for this doc
            if field_data:
                doc.field_data = field_data
                doc.status = 'filled'
        
        db.session.commit()
        
        # Check if this is "Save All & Continue" (redirect to preview) or just "Save All Drafts"
        action = request.form.get('submit_action', 'save')
        
        if action == 'continue':
            # Redirect directly to preview page with actual PDFs and send button
            return redirect(url_for('transactions.preview_all_documents', id=id))
        else:
            # Just saving drafts - go back to fill form
            flash(f'Successfully saved {len(documents)} document(s) as drafts.', 'success')
            return redirect(url_for('transactions.fill_all_documents', id=id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving documents: {str(e)}', 'error')
        return redirect(url_for('transactions.fill_all_documents', id=id))


@transactions_bp.route('/<int:id>/documents/preview-all')
@login_required
@transactions_required
def preview_all_documents(id):
    """
    Preview page showing actual filled PDFs for all documents before sending.
    Creates DocuSeal preview submissions for each document and displays them
    via embedded viewers. Also shows signers and send button.
    """
    from services.docuseal_service import (
        create_submission, build_docuseal_fields, get_template_id,
        get_template_submitter_roles, DOCUSEAL_MOCK_MODE
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    # Get documents with specialized forms that have been filled
    specialized_slugs = get_specialized_slugs()
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(specialized_slugs),
        TransactionDocument.status.in_(['filled', 'draft', 'generated'])
    ).order_by(TransactionDocument.created_at).all()
    
    if not documents:
        flash('No documents ready for preview. Please fill out the documents first.', 'warning')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    # Get participants for signer selection
    participants = transaction.participants.all()
    
    # Build signer list from participants
    signers = []
    
    # Primary seller
    seller = next((p for p in participants if p.role == 'seller' and p.is_primary), None)
    if seller and seller.display_email:
        signers.append({
            'id': seller.id,
            'role': 'Seller',
            'name': seller.display_name,
            'email': seller.display_email,
            'participant_role': 'seller'
        })
    
    # Co-seller if exists
    co_seller = next((p for p in participants if p.role == 'co_seller'), None)
    if co_seller and co_seller.display_email:
        signers.append({
            'id': co_seller.id,
            'role': 'Co-Seller',
            'name': co_seller.display_name,
            'email': co_seller.display_email,
            'participant_role': 'co_seller'
        })
    
    # Listing agent (maps to "Broker" in DocuSeal)
    listing_agent = next((p for p in participants if p.role == 'listing_agent'), None)
    if listing_agent and listing_agent.display_email:
        signers.append({
            'id': listing_agent.id,
            'role': 'Broker',
            'name': listing_agent.display_name,
            'email': listing_agent.display_email,
            'participant_role': 'listing_agent'
        })
    
    # Build preview data for each document - create DocuSeal preview submissions
    preview_docs = []
    
    for doc in documents:
        config = get_document_config(doc.template_slug)
        
        doc_preview = {
            'id': doc.id,
            'template_slug': doc.template_slug,
            'template_name': doc.template_name,
            'status': doc.status,
            'field_data': doc.field_data or {},
            'config': config,
            'embed_src': None,
            'embed_slug': None,
            'error': None
        }
        
        # In real mode, create DocuSeal preview submission
        if not DOCUSEAL_MOCK_MODE:
            template_id = get_template_id(doc.template_slug)
            if template_id:
                try:
                    # Build agent data for pre-filling broker fields
                    agent_data = {
                        'name': f"{current_user.first_name} {current_user.last_name}",
                        'email': current_user.email,
                        'license_number': getattr(current_user, 'license_number', '') or '',
                        'phone': getattr(current_user, 'phone', '') or ''
                    }
                    
                    # Get the submitter roles this template uses
                    template_roles = get_template_submitter_roles(doc.template_slug)
                    
                    # Build preview submitters based on template's roles
                    preview_submitters = []
                    
                    # Get all sellers for handling Seller 2, Seller 3, etc.
                    all_sellers = [p for p in participants if p.role == 'seller']
                    primary_seller = next((s for s in all_sellers if s.is_primary), None)
                    additional_sellers = [s for s in all_sellers if not s.is_primary and s.display_email]
                    
                    for role in template_roles:
                        if role == 'Seller':
                            # Seller fields from form data
                            seller_fields = build_docuseal_fields(
                                doc.field_data or {},
                                doc.template_slug,
                                agent_data=None,
                                submitter_role='Seller'
                            )
                            if seller and seller.display_email:
                                preview_submitters.append({
                                    'role': 'Seller',
                                    'email': seller.display_email,
                                    'name': seller.display_name,
                                    'fields': seller_fields
                                })
                            else:
                                # Use agent as placeholder for preview
                                preview_submitters.append({
                                    'role': 'Seller',
                                    'email': current_user.email,
                                    'name': f"{current_user.first_name} {current_user.last_name}",
                                    'fields': seller_fields
                                })
                        
                        elif role.startswith('Seller ') and role != 'Seller':
                            # Additional seller roles (Seller 2, Seller 3, etc.)
                            # Only include if we have an actual additional seller
                            try:
                                seller_num = int(role.split(' ')[1])
                                seller_index = seller_num - 2  # Seller 2 -> index 0
                                if seller_index < len(additional_sellers):
                                    add_seller = additional_sellers[seller_index]
                                    add_seller_fields = build_docuseal_fields(
                                        doc.field_data or {},
                                        doc.template_slug,
                                        agent_data=None,
                                        submitter_role=role
                                    )
                                    preview_submitters.append({
                                        'role': role,
                                        'email': add_seller.display_email,
                                        'name': add_seller.display_name,
                                        'fields': add_seller_fields
                                    })
                                # If no additional seller, skip this role - DocuSeal will deactivate those fields
                            except (ValueError, IndexError):
                                pass  # Invalid role format, skip
                        
                        elif role == 'Broker':
                            # Broker/agent fields
                            broker_fields = build_docuseal_fields(
                                doc.field_data or {},
                                doc.template_slug,
                                agent_data,
                                submitter_role='Broker'
                            )
                            preview_submitters.append({
                                'role': 'Broker',
                                'email': current_user.email,
                                'name': f"{current_user.first_name} {current_user.last_name}",
                                'fields': broker_fields
                            })
                        
                        elif role == 'Buyer':
                            # Buyer fields (for templates like HOA Addendum)
                            # In listing phase, agent fills these as preview
                            buyer_fields = build_docuseal_fields(
                                doc.field_data or {},
                                doc.template_slug,
                                agent_data=None,
                                submitter_role='Buyer'
                            )
                            # Use agent email for preview (no actual buyer yet)
                            preview_submitters.append({
                                'role': 'Buyer',
                                'email': current_user.email,
                                'name': f"{current_user.first_name} {current_user.last_name} (Preview)",
                                'fields': buyer_fields
                            })
                    
                    # Create submission with send_email=false for preview
                    submission = create_submission(
                        template_slug=doc.template_slug,
                        submitters=preview_submitters,
                        field_values=None,
                        send_email=False,
                        message=None
                    )
                    
                    # Find a submitter slug for embedding (prefer Broker/agent, fallback to first)
                    submitters_list = submission.get('submitters', [])
                    agent_submitter = next(
                        (s for s in submitters_list if s.get('role') in ['Broker', 'Buyer']),
                        submitters_list[0] if submitters_list else {}
                    )
                    
                    embed_slug = agent_submitter.get('slug', '')
                    doc_preview['embed_slug'] = embed_slug
                    doc_preview['embed_src'] = agent_submitter.get('embed_src', f"https://docuseal.com/s/{embed_slug}")
                    
                    # Update document with preview submission ID
                    doc.docuseal_submission_id = submission.get('id')
                    doc.status = 'generated'
                    
                except Exception as e:
                    doc_preview['error'] = str(e)
        
        preview_docs.append(doc_preview)
    
    # Commit any status updates
    db.session.commit()
    
    return render_template(
        'transactions/preview_all_documents.html',
        transaction=transaction,
        documents=documents,
        preview_docs=preview_docs,
        signers=signers,
        participants=participants,
        doc_configs=DOCUMENT_REGISTRY,
        mock_mode=DOCUSEAL_MOCK_MODE
    )


@transactions_bp.route('/<int:id>/documents/send-all', methods=['POST'])
@login_required
@transactions_required
def send_all_for_signature(id):
    """
    Send all filled documents as ONE envelope using DocuSeal's merge templates API.
    This merges multiple templates into one and sends a single email to signers.
    """
    from services.docuseal_service import (
        create_submission,
        merge_templates,
        build_docuseal_fields,
        get_template_id,
        DocuSealError,
        DOCUSEAL_MOCK_MODE
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    # Get documents with specialized forms that are filled or generated (previewed)
    specialized_slugs = get_specialized_slugs()
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(specialized_slugs),
        TransactionDocument.status.in_(['filled', 'draft', 'generated'])
    ).order_by(TransactionDocument.created_at).all()
    
    if not documents:
        flash('No documents ready to send. Please fill out the documents first.', 'warning')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    # Get participants
    participants = transaction.participants.all()
    
    # Get key participants
    seller = next((p for p in participants if p.role == 'seller' and p.is_primary), None)
    listing_agent = next((p for p in participants if p.role == 'listing_agent'), None)
    
    if not seller or not seller.display_email:
        flash('No seller with email found. Please add seller contact information.', 'error')
        return redirect(url_for('transactions.preview_all_documents', id=id))
    
    # Build agent data for field population
    agent_data = {
        'name': f"{current_user.first_name} {current_user.last_name}",
        'email': current_user.email,
        'license_number': getattr(current_user, 'license_number', '') or '',
        'phone': getattr(current_user, 'phone', '') or ''
    }
    
    try:
        # Step 1: Collect all template IDs
        template_ids = []
        for doc in documents:
            template_id = get_template_id(doc.template_slug)
            if template_id:
                template_ids.append(template_id)
        
        if not template_ids:
            flash('No valid templates found for the documents.', 'error')
            return redirect(url_for('transactions.preview_all_documents', id=id))
        
        # Step 2: Merge templates into one combined template
        # Use unified roles: Seller and Broker (Broker handles agent signing)
        merged_template = merge_templates(
            template_ids=template_ids,
            name=f"Document Package - {transaction.street_address} - TX{transaction.id}",
            roles=['Seller', 'Broker'],  # Unified roles for merged template
            external_id=f"tx-{transaction.id}-merged-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        )
        
        merged_template_id = merged_template.get('id')
        
        # Step 3: Build combined fields from ALL documents for each submitter
        all_seller_fields = []
        all_broker_fields = []
        
        for doc in documents:
            # Get seller fields for this document
            seller_fields = build_docuseal_fields(
                doc.field_data or {},
                doc.template_slug,
                agent_data=None,
                submitter_role='Seller'
            )
            all_seller_fields.extend(seller_fields)
            
            # Get broker fields for this document
            broker_fields = build_docuseal_fields(
                doc.field_data or {},
                doc.template_slug,
                agent_data,
                submitter_role='Broker'
            )
            all_broker_fields.extend(broker_fields)
        
        # Step 4: Build submitters with combined fields
        broker_email = listing_agent.display_email if listing_agent else current_user.email
        broker_name = listing_agent.display_name if listing_agent else f"{current_user.first_name} {current_user.last_name}"
        
        submitters = [
            {
                'role': 'Seller',
                'email': seller.display_email,
                'name': seller.display_name,
                'fields': all_seller_fields
            },
            {
                'role': 'Broker',
                'email': broker_email,
                'name': broker_name,
                'fields': all_broker_fields
            }
        ]
        
        # Step 5: Create ONE submission from the merged template
        result = create_submission(
            template_slug=None,  # We're using template_id directly
            submitters=submitters,
            field_values=None,
            send_email=True,
            message={
                'subject': f'Documents Ready for Signature - {transaction.street_address}',
                'body': f'Please review and sign your documents for {transaction.full_address}. Click here to sign: {{{{submitter.link}}}}'
            },
            template_id=merged_template_id  # Use merged template directly
        )
        
        submission_id = result.get('id')
        
        # Step 6: Update ALL documents with the same submission ID
        for doc in documents:
            doc.status = 'sent'
            doc.docuseal_submission_id = str(submission_id)
            doc.sent_at = datetime.utcnow()
            
            # Create signature records for each signer
            for submitter_data in result.get('submitters', []):
                participant = None
                role = submitter_data.get('role')
                if role == 'Seller':
                    participant = seller
                elif role == 'Broker':
                    participant = listing_agent
                
                signature = DocumentSignature(
                    document_id=doc.id,
                    participant_id=participant.id if participant else None,
                    signer_email=submitter_data.get('email', ''),
                    signer_name=submitter_data.get('name', ''),
                    signer_role=role or 'Signer',
                    status='sent',
                    docuseal_submitter_slug=submitter_data.get('slug', ''),
                    sent_at=datetime.utcnow()
                )
                db.session.add(signature)
        
        db.session.commit()
        
        doc_count = len(documents)
        if DOCUSEAL_MOCK_MODE:
            flash(f'[MOCK MODE] {doc_count} document(s) sent as ONE envelope! Submission ID: {submission_id}', 'success')
        else:
            flash(f'{doc_count} document(s) sent as one envelope to signers!', 'success')
        
        return redirect(url_for('transactions.view_transaction', id=id))
        
    except DocuSealError as e:
        flash(f'Error sending documents: {str(e)}', 'error')
        return redirect(url_for('transactions.preview_all_documents', id=id))
    except Exception as e:
        db.session.rollback()
        flash(f'Unexpected error: {str(e)}', 'error')
        return redirect(url_for('transactions.preview_all_documents', id=id))


def build_prefill_data(transaction, participants):
    """Build prefill data from transaction and participants."""
    data = {
        # Property info
        'property_address': transaction.street_address or '',
        'property_city': transaction.city or '',
        'property_state': transaction.state or 'TX',
        'property_zip': transaction.zip_code or '',
        'property_county': transaction.county or '',
        'property_full_address': f"{transaction.street_address or ''}, {transaction.city or ''}, {transaction.state or 'TX'} {transaction.zip_code or ''}".strip(', '),
        
        # Broker info (Origen Realty defaults)
        'broker_name': 'Origen Realty',
        'broker_license': '',  # Can be set in config or org settings later
    }
    
    # Helper to get phone from participant (check contact first, then direct phone field)
    def get_phone(participant):
        if participant.contact_id and participant.contact:
            return participant.contact.phone or ''
        return participant.phone or ''
    
    # Add seller info (primary seller participant)
    seller = next((p for p in participants if p.role == 'seller' and p.is_primary), None)
    if seller:
        data['seller_name'] = seller.display_name
        data['seller_legal_name'] = seller.display_name  # Can be overwritten in form
        data['seller_email'] = seller.display_email or ''
        data['seller_phone'] = get_phone(seller)
        
        # If linked to a contact, get additional info
        if seller.contact:
            contact = seller.contact
            # Build mailing address if different from property
            if contact.street_address:
                mailing_parts = [contact.street_address]
                if contact.city:
                    mailing_parts.append(contact.city)
                if contact.state:
                    mailing_parts.append(contact.state)
                if contact.zip_code:
                    mailing_parts.append(contact.zip_code)
                data['seller_mailing_address'] = ', '.join(mailing_parts)
    
    # Add co-seller info
    co_sellers = [p for p in participants if p.role == 'co_seller']
    if co_sellers:
        co_seller = co_sellers[0]
        data['co_seller_name'] = co_seller.display_name
        data['co_seller_email'] = co_seller.display_email or ''
        data['co_seller_phone'] = get_phone(co_seller)
        
        # Combine names for legal name field if both exist
        if seller:
            data['seller_legal_name'] = f"{seller.display_name} and {co_seller.display_name}"
    
    # Add listing agent info
    agent = next((p for p in participants if p.role == 'listing_agent'), None)
    if agent:
        data['agent_name'] = agent.display_name
        data['agent_email'] = agent.display_email or ''
        data['agent_phone'] = get_phone(agent)
        
        # If linked to a user, get license info
        if agent.user:
            user = agent.user
            data['agent_license'] = user.license_number or ''
            data['licensed_supervisor'] = user.licensed_supervisor or ''
    
    # Add buyer's agent info if present
    buyers_agent = next((p for p in participants if p.role == 'buyers_agent'), None)
    if buyers_agent:
        data['buyers_agent_name'] = buyers_agent.display_name
        data['buyers_agent_email'] = buyers_agent.display_email or ''
        data['buyers_agent_phone'] = get_phone(buyers_agent)
        data['buyers_agent_company'] = buyers_agent.company or ''
    
    # Add title company info if present
    title_company = next((p for p in participants if p.role == 'title_company'), None)
    if title_company:
        data['title_company_name'] = title_company.display_name
        data['title_company_email'] = title_company.display_email or ''
        data['title_company_phone'] = get_phone(title_company)
    
    # Add intake data if available (with intake_ prefix)
    if transaction.intake_data:
        for key, value in transaction.intake_data.items():
            data[f'intake_{key}'] = value
    
    # Set defaults for listing agreement from intake data
    if transaction.intake_data:
        intake = transaction.intake_data
        
        # Map intake responses to listing agreement defaults
        if intake.get('has_hoa'):
            data['has_hoa'] = 'yes' if intake['has_hoa'] else 'no'
        if intake.get('special_districts'):
            data['has_special_districts'] = 'yes' if intake['special_districts'] else 'no'
        if intake.get('flood_hazard'):
            data['is_flood_hazard'] = 'yes' if intake['flood_hazard'] else 'no'
    
    return data


# =============================================================================
# DOCUMENT PREVIEW
# =============================================================================

@transactions_bp.route('/<int:id>/documents/<int:doc_id>/preview')
@login_required
@transactions_required
def document_preview(id, doc_id):
    """
    Preview a filled document before sending for signature.
    
    Creates a DocuSeal submission with send_email=false for the agent to review
    the document with pre-filled values. This is a "preview" submission that
    gets replaced when the agent confirms and sends.
    """
    from services.docuseal_service import (
        create_submission, build_docuseal_fields, get_template_id,
        DOCUSEAL_MOCK_MODE, DOCUSEAL_API_URL
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        abort(403)
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        abort(404)
    
    # Document must be filled to preview
    if doc.status not in ['filled', 'generated', 'draft']:
        flash('Please fill out the document form first.', 'error')
        return redirect(url_for('transactions.document_form', id=id, doc_id=doc_id))
    
    # Check if template is configured in DocuSeal
    template_id = get_template_id(doc.template_slug)
    if not template_id and not DOCUSEAL_MOCK_MODE:
        flash('This document template is not yet configured for e-signature.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    try:
        from services.docuseal_service import get_template_submitter_roles
        
        # Build agent data for pre-filling broker fields
        agent_data = {
            'name': f"{current_user.first_name} {current_user.last_name}",
            'email': current_user.email,
            'license_number': getattr(current_user, 'license_number', '') or '',
            'phone': getattr(current_user, 'phone', '') or ''
        }
        
        # Get participants for this transaction
        participants = transaction.participants.all()
        seller = next((p for p in participants if p.role == 'seller' and p.is_primary), None)
        all_sellers = [p for p in participants if p.role == 'seller']
        additional_sellers = [s for s in all_sellers if not s.is_primary and s.display_email]
        
        # Get the actual roles this template uses
        template_roles = get_template_submitter_roles(doc.template_slug)
        
        # Build preview submitters based on template's actual roles
        preview_submitters = []
        
        for role in template_roles:
            if role == 'Seller':
                seller_fields = build_docuseal_fields(
                    doc.field_data or {},
                    doc.template_slug,
                    agent_data=None,
                    submitter_role='Seller'
                )
                if seller and seller.display_email:
                    preview_submitters.append({
                        'role': 'Seller',
                        'email': seller.display_email,
                        'name': seller.display_name,
                        'fields': seller_fields
                    })
                else:
                    # Use agent as placeholder for preview
                    preview_submitters.append({
                        'role': 'Seller',
                        'email': current_user.email,
                        'name': f"{current_user.first_name} {current_user.last_name}",
                        'fields': seller_fields
                    })
            
            elif role.startswith('Seller ') and role != 'Seller':
                # Additional seller roles (Seller 2, Seller 3, etc.)
                # Only include if we have an actual additional seller
                try:
                    seller_num = int(role.split(' ')[1])
                    seller_index = seller_num - 2  # Seller 2 -> index 0
                    if seller_index < len(additional_sellers):
                        add_seller = additional_sellers[seller_index]
                        add_seller_fields = build_docuseal_fields(
                            doc.field_data or {},
                            doc.template_slug,
                            agent_data=None,
                            submitter_role=role
                        )
                        preview_submitters.append({
                            'role': role,
                            'email': add_seller.display_email,
                            'name': add_seller.display_name,
                            'fields': add_seller_fields
                        })
                    # If no additional seller, skip this role - DocuSeal will deactivate those fields
                except (ValueError, IndexError):
                    pass
            
            elif role == 'Broker':
                broker_fields = build_docuseal_fields(
                    doc.field_data or {},
                    doc.template_slug,
                    agent_data,
                    submitter_role='Broker'
                )
                preview_submitters.append({
                    'role': 'Broker',
                    'email': current_user.email,
                    'name': f"{current_user.first_name} {current_user.last_name}",
                    'fields': broker_fields
                })
            
            elif role == 'Buyer':
                buyer_fields = build_docuseal_fields(
                    doc.field_data or {},
                    doc.template_slug,
                    agent_data=None,
                    submitter_role='Buyer'
                )
                preview_submitters.append({
                    'role': 'Buyer',
                    'email': current_user.email,
                    'name': f"{current_user.first_name} {current_user.last_name} (Preview)",
                    'fields': buyer_fields
                })
        
        # Create submission with send_email=false for preview
        submission = create_submission(
            template_slug=doc.template_slug,
            submitters=preview_submitters,
            field_values=None,  # Already in submitters.fields
            send_email=False,  # Don't send emails - this is just for preview
            message=None
        )
        
        # Find the agent's submitter slug for embedding
        agent_submitter = next(
            (s for s in submission.get('submitters', []) if s.get('role') == 'Broker'),
            submission.get('submitters', [{}])[0] if submission.get('submitters') else {}
        )
        
        embed_slug = agent_submitter.get('slug', '')
        embed_src = agent_submitter.get('embed_src', f"https://docuseal.com/s/{embed_slug}")
        
        # Store preview submission ID so we can archive it later
        doc.docuseal_submission_id = submission.get('id')
        doc.status = 'generated'  # Mark as generated/ready for review
        db.session.commit()
        
        return render_template(
            'transactions/document_preview.html',
            transaction=transaction,
            document=doc,
            embed_src=embed_src,
            embed_slug=embed_slug,
            submission_id=submission.get('id'),
            mock_mode=DOCUSEAL_MOCK_MODE
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'Error creating preview: {str(e)}', 'error')
        return redirect(url_for('transactions.document_form', id=id, doc_id=doc_id))


# =============================================================================
# E-SIGNATURE (DocuSeal Integration)
# =============================================================================

@transactions_bp.route('/<int:id>/documents/<int:doc_id>/send', methods=['POST'])
@login_required
@transactions_required
def send_for_signature(id, doc_id):
    """Send a document for e-signature via DocuSeal."""
    from services.docuseal_service import (
        create_submission, build_submitters_from_participants, 
        build_docuseal_fields, DOCUSEAL_MOCK_MODE
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    # Check document is ready to send (must be filled or generated/previewed)
    if doc.status not in ['filled', 'generated']:
        return jsonify({
            'success': False, 
            'error': 'Please fill out the document form before sending for signature'
        }), 400
    
    try:
        from services.docuseal_service import get_template_submitter_roles
        
        # Build agent data for pre-filling broker fields
        agent_data = {
            'name': f"{current_user.first_name} {current_user.last_name}",
            'email': current_user.email,
            'license_number': getattr(current_user, 'license_number', '') or '',
            'phone': getattr(current_user, 'phone', '') or ''
        }
        
        # Get the actual roles this template uses
        template_roles = get_template_submitter_roles(doc.template_slug)
        
        # Get participants for signing
        participants = transaction.participants.all()
        submitters = build_submitters_from_participants(participants, transaction)
        
        # Filter submitters to only include roles that the template actually has
        # This prevents sending "Broker" when template only has "Seller" and "Seller 2"
        submitters = [s for s in submitters if s.get('role') in template_roles]
        
        if not submitters:
            return jsonify({
                'success': False,
                'error': 'No signers found. Add participants with email addresses.'
            }), 400
        
        # Add pre-filled fields to each submitter based on their role
        # DocuSeal requires each submitter to only receive fields that belong to them
        for submitter in submitters:
            role = submitter.get('role', '')
            if role == 'Broker':
                submitter['fields'] = build_docuseal_fields(
                    doc.field_data or {},
                    doc.template_slug,
                    agent_data,
                    submitter_role='Broker'
                )
            elif role.startswith('Seller'):
                # Seller, Seller 2, etc. get their role-specific fields
                submitter['fields'] = build_docuseal_fields(
                    doc.field_data or {},
                    doc.template_slug,
                    agent_data=None,
                    submitter_role=role
                )
            else:
                # Other roles (Buyer, etc.)
                submitter['fields'] = build_docuseal_fields(
                    doc.field_data or {},
                    doc.template_slug,
                    agent_data=None,
                    submitter_role=role
                )
        
        # Create submission in DocuSeal with send_email=True
        submission = create_submission(
            template_slug=doc.template_slug,
            submitters=submitters,
            field_values=None,  # Fields are in submitters now
            send_email=True,  # Actually send the emails
            message={
                'subject': f'Document Ready for Signature: {doc.template_name}',
                'body': f'Please sign the {doc.template_name} for {transaction.street_address}.\n\nClick here to sign: {{{{submitter.link}}}}'
            }
        )
        
        # Update document with DocuSeal submission info
        doc.docuseal_submission_id = submission['id']
        doc.sent_at = db.func.now()
        doc.status = 'sent'
        
        # Create signature records for each submitter
        for i, sub in enumerate(submission.get('submitters', [])):
            # Find matching participant
            participant = next(
                (p for p in participants if p.display_email == sub.get('email')),
                None
            )
            
            signature = DocumentSignature(
                document_id=doc.id,
                participant_id=participant.id if participant else None,
                signer_email=sub.get('email'),
                signer_name=sub.get('name', ''),
                signer_role=sub.get('role', 'Signer'),
                status='sent',
                sign_order=i + 1,
                docuseal_submitter_slug=sub.get('slug'),
                sent_at=db.func.now()
            )
            db.session.add(signature)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Document sent for signature!',
            'submission_id': submission['id'],
            'submitters': len(submitters),
            'mock_mode': DOCUSEAL_MOCK_MODE
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/status')
@login_required
@transactions_required
def check_signature_status(id, doc_id):
    """Check the signature status of a document."""
    from services.docuseal_service import get_submission, DOCUSEAL_MOCK_MODE
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    if not doc.docuseal_submission_id:
        return jsonify({
            'success': False,
            'error': 'Document has not been sent for signature'
        }), 400
    
    try:
        # Get submission status from DocuSeal
        submission = get_submission(doc.docuseal_submission_id)
        
        # Get signature records
        signatures = DocumentSignature.query.filter_by(document_id=doc.id).all()
        
        signer_status = []
        for sig in signatures:
            # Find matching submitter in DocuSeal response
            submitter_info = next(
                (s for s in submission.get('submitters', []) 
                 if s.get('slug') == sig.docuseal_submitter_slug),
                {}
            )
            
            signer_status.append({
                'id': sig.id,
                'participant_id': sig.participant_id,
                'status': submitter_info.get('status', 'pending'),
                'viewed_at': submitter_info.get('viewed_at'),
                'signed_at': submitter_info.get('signed_at')
            })
        
        return jsonify({
            'success': True,
            'submission_id': doc.docuseal_submission_id,
            'overall_status': submission.get('status', 'pending'),
            'signers': signer_status,
            'mock_mode': DOCUSEAL_MOCK_MODE
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/void', methods=['POST'])
@login_required
@transactions_required
def void_document(id, doc_id):
    """
    Void a sent document and reset it to 'filled' status so it can be re-sent.
    This clears the DocuSeal submission and allows the agent to preview/send again.
    """
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    if doc.status not in ['sent', 'generated']:
        return jsonify({
            'success': False,
            'error': f'Cannot void document in "{doc.status}" status. Only sent or generated documents can be voided.'
        }), 400
    
    try:
        # Clear DocuSeal submission info
        doc.docuseal_submission_id = None
        doc.sent_at = None
        doc.status = 'filled'  # Reset to filled so they can preview again
        
        # Delete any signature records
        DocumentSignature.query.filter_by(document_id=doc.id).delete()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Document voided. You can now edit and resend.',
            'new_status': 'filled'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/resend', methods=['POST'])
@login_required
@transactions_required
def resend_signature_request(id, doc_id):
    """
    Resend signature request emails to submitters who haven't signed yet.
    This uses the existing DocuSeal submission without creating a new one.
    """
    from services.docuseal_service import resend_signature_emails, DOCUSEAL_MOCK_MODE
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    if doc.status != 'sent':
        return jsonify({
            'success': False,
            'error': f'Cannot resend document in "{doc.status}" status. Document must be in "sent" status.'
        }), 400
    
    if not doc.docuseal_submission_id:
        return jsonify({
            'success': False,
            'error': 'Document has not been sent for signature'
        }), 400
    
    try:
        # Resend emails to pending submitters
        result = resend_signature_emails(
            submission_id=doc.docuseal_submission_id,
            message={
                'subject': f'Reminder: Please Sign - {doc.template_name}',
                'body': f'This is a reminder to sign the {doc.template_name} for {transaction.street_address}. Click here to sign: {{{{submitter.link}}}}'
            }
        )
        
        return jsonify({
            'success': True,
            'resent_count': result.get('resent_count', 0),
            'message': result.get('message', 'Emails resent'),
            'mock_mode': DOCUSEAL_MOCK_MODE
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/simulate-sign', methods=['POST'])
@login_required
@transactions_required
def simulate_signature(id, doc_id):
    """
    Simulate signing completion for testing (mock mode only).
    This allows testing the full flow without real DocuSeal.
    """
    from services.docuseal_service import (
        _mock_simulate_signing, DOCUSEAL_MOCK_MODE
    )
    
    if not DOCUSEAL_MOCK_MODE:
        return jsonify({
            'success': False,
            'error': 'Simulation only available in mock mode'
        }), 400
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    if not doc.docuseal_submission_id:
        return jsonify({
            'success': False,
            'error': 'Document has not been sent for signature'
        }), 400
    
    try:
        # Simulate the signing
        _mock_simulate_signing(doc.docuseal_submission_id, 'completed')
        
        # Update document status
        doc.status = 'signed'
        doc.signed_at = db.func.now()
        
        # Update signature records
        signatures = DocumentSignature.query.filter_by(document_id=doc.id).all()
        for sig in signatures:
            sig.signed_at = db.func.now()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Signature simulated successfully!',
            'new_status': 'signed'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/download')
@login_required
@transactions_required
def download_signed_document(id, doc_id):
    """Get the download URL for a signed document."""
    from services.docuseal_service import get_signed_document_urls, DOCUSEAL_MOCK_MODE
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    if doc.status != 'signed':
        return jsonify({
            'success': False,
            'error': 'Document has not been signed yet'
        }), 400
    
    try:
        documents = get_signed_document_urls(doc.docuseal_submission_id)
        
        return jsonify({
            'success': True,
            'documents': documents,
            'mock_mode': DOCUSEAL_MOCK_MODE
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# WEBHOOK ENDPOINT
# =============================================================================

# =============================================================================
# DOCUSEAL ADMIN/DEBUG ENDPOINTS
# =============================================================================

@transactions_bp.route('/admin/docuseal/template/<int:template_id>')
@login_required
@transactions_required
def view_docuseal_template(template_id):
    """
    Admin endpoint to view DocuSeal template fields.
    Useful for creating field mappings between CRM form and DocuSeal.
    """
    from services.docuseal_service import get_template, DOCUSEAL_MODE, DOCUSEAL_MOCK_MODE
    
    try:
        template = get_template(template_id)
        
        # Extract key information for mapping
        fields = template.get('fields', [])
        submitters = template.get('submitters', [])
        
        # Group fields by submitter
        fields_by_submitter = {}
        for submitter in submitters:
            submitter_uuid = submitter.get('uuid')
            submitter_fields = [f for f in fields if f.get('submitter_uuid') == submitter_uuid]
            fields_by_submitter[submitter.get('name')] = submitter_fields
        
        return jsonify({
            'success': True,
            'mode': DOCUSEAL_MODE,
            'mock_mode': DOCUSEAL_MOCK_MODE,
            'template': {
                'id': template.get('id'),
                'name': template.get('name'),
                'slug': template.get('slug'),
            },
            'submitters': submitters,
            'fields': fields,
            'fields_by_submitter': fields_by_submitter,
            'field_names': [f.get('name') for f in fields],
            'total_fields': len(fields)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'mode': DOCUSEAL_MODE,
            'mock_mode': DOCUSEAL_MOCK_MODE
        }), 500


@transactions_bp.route('/admin/docuseal/status')
@login_required
@transactions_required
def docuseal_status():
    """Check DocuSeal configuration status."""
    from services.docuseal_service import (
        DOCUSEAL_MODE, DOCUSEAL_MOCK_MODE, DOCUSEAL_API_KEY, 
        TEMPLATE_MAP, list_templates
    )
    
    # Check which templates are configured
    configured_templates = {k: v for k, v in TEMPLATE_MAP.items() if v is not None}
    
    # Try to list templates from DocuSeal
    try:
        if not DOCUSEAL_MOCK_MODE:
            available_templates = list_templates(limit=10)
            available = [{'id': t.get('id'), 'name': t.get('name')} for t in available_templates]
        else:
            available = 'Mock mode - no API call made'
    except Exception as e:
        available = f'Error: {str(e)}'
    
    return jsonify({
        'success': True,
        'mode': DOCUSEAL_MODE,
        'mock_mode': DOCUSEAL_MOCK_MODE,
        'api_key_set': bool(DOCUSEAL_API_KEY),
        'api_key_preview': f"{DOCUSEAL_API_KEY[:8]}..." if DOCUSEAL_API_KEY else None,
        'configured_templates': configured_templates,
        'available_templates': available
    })


# =============================================================================
# WEBHOOK ENDPOINT
# =============================================================================

@transactions_bp.route('/webhook/docuseal', methods=['POST'])
def docuseal_webhook():
    """
    Receive webhooks from DocuSeal for signature events.
    
    Configure this URL in DocuSeal: https://yourdomain.com/transactions/webhook/docuseal
    
    Events:
    - form.viewed: Signer opened the document
    - form.started: Signer began filling
    - form.completed: All signers finished
    """
    from services.docuseal_service import process_webhook
    
    try:
        payload = request.get_json()
        
        if not payload:
            return jsonify({'error': 'No payload'}), 400
        
        # Process the webhook
        result = process_webhook(payload)
        event_type = result.get('event_type')
        submission_id = result.get('submission_id')
        
        # Find the document by submission ID
        doc = TransactionDocument.query.filter_by(
            docuseal_submission_id=str(submission_id)
        ).first()
        
        if not doc:
            # Log but don't error - might be from a different source
            return jsonify({'received': True, 'matched': False})
        
        # Update based on event type
        if event_type == 'form.viewed':
            # Update signature record
            pass  # Optionally track viewed_at
            
        elif event_type == 'form.started':
            # Signer started filling
            pass
            
        elif event_type == 'form.completed':
            # All signers finished!
            doc.status = 'signed'
            doc.signed_at = db.func.now()
            
            # Update all signature records
            signatures = DocumentSignature.query.filter_by(document_id=doc.id).all()
            for sig in signatures:
                sig.signed_at = db.func.now()
            
            db.session.commit()
        
        return jsonify({
            'received': True,
            'event': event_type,
            'document_id': doc.id if doc else None
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

