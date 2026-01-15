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
    TransactionDocument, DocumentSignature, Contact, User, AuditEvent, ContactFile
)
from feature_flags import can_access_transactions
from services import audit_service

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
    """List all transactions for the current user (or all for admins)."""
    # Get filter params
    status_filter = request.args.get('status', '')
    type_filter = request.args.get('type', '')
    search_query = request.args.get('q', '').strip()
    
    # Admin view toggle - allow admins to see all transactions
    show_all = request.args.get('view') == 'all' and current_user.role == 'admin'
    
    # Base query - filter by user unless admin viewing all
    if show_all:
        query = Transaction.query
    else:
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
        search_query=search_query,
        show_all=show_all
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
    
    # Check if a contact_id was passed to pre-select
    preselected_contact = None
    contact_id = request.args.get('contact_id', type=int)
    if contact_id:
        preselected_contact = Contact.query.filter_by(
            id=contact_id, 
            user_id=current_user.id
        ).first()
    
    return render_template(
        'transactions/create.html',
        transaction_types=transaction_types,
        contacts=contacts,
        preselected_contact=preselected_contact
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
        
        # Get the transaction type to determine participant role and default status
        tx_type = TransactionType.query.get(int(transaction_type_id))
        
        # Determine default status based on transaction type
        # Buyer transactions start with 'showing', sellers start with 'preparing_to_list'
        default_status = 'showing' if tx_type and tx_type.name == 'buyer' else 'preparing_to_list'
        
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
            status=default_status
        )
        db.session.add(transaction)
        db.session.flush()  # Get the transaction ID
        
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
        
        # Log transaction creation
        audit_service.log_transaction_created(transaction)

        # Log participant additions
        for participant in transaction.participants.all():
            audit_service.log_participant_added(transaction, participant)

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
    
    # Ensure user owns this transaction or is admin
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    # Get participants grouped by role
    participants = transaction.participants.all()
    
    # Get documents
    documents = transaction.documents.order_by(TransactionDocument.created_at).all()
    
    # Get files from all contacts associated with this transaction
    contact_ids = [p.contact_id for p in participants if p.contact_id]
    contact_files = []
    if contact_ids:
        contact_files = ContactFile.query.filter(
            ContactFile.contact_id.in_(contact_ids)
        ).order_by(ContactFile.created_at.desc()).all()
    
    # For seller transactions, extract listing info from the listing agreement document
    listing_info = None
    if transaction.transaction_type.name == 'seller':
        # Find the listing agreement document
        listing_doc = transaction.documents.filter_by(template_slug='listing-agreement').first()
        if listing_doc and listing_doc.status != 'pending' and listing_doc.field_data:
            field_data = listing_doc.field_data
            # Build listing info from field data
            # Buyer side commission: prefer percentage, fallback to flat fee
            buyer_commission = field_data.get('buyer_agent_percent')
            if buyer_commission:
                buyer_commission = f"{buyer_commission}%"
            else:
                buyer_flat = field_data.get('buyer_agent_flat')
                if buyer_flat:
                    buyer_commission = f"${buyer_flat}"
            
            # Format dates as "January 14, 2026"
            def format_date(date_str):
                if not date_str:
                    return None
                try:
                    from datetime import datetime
                    dt = datetime.strptime(date_str, '%Y-%m-%d')
                    return dt.strftime('%B %d, %Y')
                except (ValueError, TypeError):
                    return date_str
            
            listing_info = {
                'list_price': field_data.get('list_price'),
                'listing_start_date': format_date(field_data.get('listing_start_date')),
                'listing_end_date': format_date(field_data.get('listing_end_date')),
                'total_commission': field_data.get('total_commission'),
                'buyer_commission': buyer_commission,
            }
    
    # Get lockbox combo from extra_data (always available for seller transactions)
    lockbox_combo = None
    if transaction.transaction_type.name == 'seller':
        extra_data = transaction.extra_data or {}
        lockbox_combo = extra_data.get('lockbox_combo')
    
    return render_template(
        'transactions/detail.html',
        transaction=transaction,
        participants=participants,
        documents=documents,
        contact_files=contact_files,
        listing_info=listing_info,
        lockbox_combo=lockbox_combo
    )


@transactions_bp.route('/<int:id>/edit')
@login_required
@transactions_required
def edit_transaction(id):
    """Show edit form for a transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    try:
        # Get transaction address for flash message
        address = transaction.street_address
        transaction_id = transaction.id

        # Log deletion before actually deleting
        audit_service.log_transaction_deleted(transaction_id, address)

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

    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)

    try:
        # Track changes for audit
        old_status = transaction.status
        changed_fields = []

        # Check each field for changes
        new_address = request.form.get('street_address', transaction.street_address)
        if new_address != transaction.street_address:
            changed_fields.append('street_address')
        transaction.street_address = new_address

        new_city = request.form.get('city') or None
        if new_city != transaction.city:
            changed_fields.append('city')
        transaction.city = new_city

        new_state = request.form.get('state', transaction.state)
        if new_state != transaction.state:
            changed_fields.append('state')
        transaction.state = new_state

        new_zip = request.form.get('zip_code') or None
        if new_zip != transaction.zip_code:
            changed_fields.append('zip_code')
        transaction.zip_code = new_zip

        new_county = request.form.get('county') or None
        if new_county != transaction.county:
            changed_fields.append('county')
        transaction.county = new_county

        new_ownership = request.form.get('ownership_status') or None
        if new_ownership != transaction.ownership_status:
            changed_fields.append('ownership_status')
        transaction.ownership_status = new_ownership

        new_status = request.form.get('status', transaction.status)
        if new_status != transaction.status:
            changed_fields.append('status')
        transaction.status = new_status

        # Parse expected close date if provided
        expected_close = request.form.get('expected_close_date')
        new_expected = dt.strptime(expected_close, '%Y-%m-%d').date() if expected_close else None
        if new_expected != transaction.expected_close_date:
            changed_fields.append('expected_close_date')
        transaction.expected_close_date = new_expected

        # Parse actual close date if provided
        actual_close = request.form.get('actual_close_date')
        new_actual = dt.strptime(actual_close, '%Y-%m-%d').date() if actual_close else None
        if new_actual != transaction.actual_close_date:
            changed_fields.append('actual_close_date')
        transaction.actual_close_date = new_actual

        # Log audit events
        if changed_fields:
            # Log status change separately if status changed
            if 'status' in changed_fields and old_status != new_status:
                audit_service.log_transaction_status_changed(transaction, old_status, new_status)
                changed_fields.remove('status')

            # Log other field changes
            if changed_fields:
                audit_service.log_transaction_updated(transaction, changed_fields)

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
        'first_name': c.first_name,
        'last_name': c.last_name,
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    new_status = data.get('status')
    
    valid_statuses = ['preparing_to_list', 'showing', 'active', 'under_contract', 'closed', 'cancelled']
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    
    try:
        old_status = transaction.status
        transaction.status = new_status
        
        # Log the status change
        if old_status != new_status:
            audit_service.log_transaction_status_changed(transaction, old_status, new_status)
        
        db.session.commit()
        return jsonify({'success': True, 'status': new_status})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/lockbox-combo', methods=['POST'])
@login_required
@transactions_required
def update_lockbox_combo(id):
    """Update the lockbox combo for a seller transaction."""
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Only allow for seller transactions
    if transaction.transaction_type.name != 'seller':
        return jsonify({'success': False, 'error': 'Lockbox combo only available for seller transactions'}), 400
    
    data = request.get_json()
    lockbox_combo = data.get('lockbox_combo', '').strip()
    
    try:
        # Initialize extra_data if None
        if transaction.extra_data is None:
            transaction.extra_data = {}
        
        # Update the lockbox combo
        extra_data = dict(transaction.extra_data)  # Make a mutable copy
        extra_data['lockbox_combo'] = lockbox_combo
        transaction.extra_data = extra_data
        
        db.session.commit()
        return jsonify({'success': True, 'lockbox_combo': lockbox_combo})
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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

        # Log audit event
        audit_service.log_intake_saved(transaction, intake_data)

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


@transactions_bp.route('/<int:id>/intake/preview-changes', methods=['POST'])
@login_required
@transactions_required
def preview_document_changes(id):
    """
    Preview what documents will be added/removed/kept based on intake answers.
    Returns a diff with clear explanations of WHY each change is happening.
    """
    from services.intake_service import get_intake_schema, evaluate_document_rules, validate_intake_data
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Get the schema
    schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    
    if not schema:
        return jsonify({'success': False, 'error': 'Schema not found'}), 404
    
    # Build question labels lookup
    question_labels = {}
    for section in schema.get('sections', []):
        for question in section.get('questions', []):
            question_labels[question['id']] = question['label']
    
    # Parse incoming intake data from request
    data = request.get_json() if request.is_json else None
    if data is None:
        new_intake_data = {}
        for section in schema.get('sections', []):
            for question in section.get('questions', []):
                field_id = question['id']
                value = request.form.get(field_id)
                if question['type'] == 'boolean':
                    new_intake_data[field_id] = value == 'true' or value == 'yes'
                else:
                    new_intake_data[field_id] = value
    else:
        new_intake_data = data.get('intake_data', {})
    
    # Get old intake data for comparison
    old_intake_data = transaction.intake_data or {}
    
    # Validate
    is_valid, missing = validate_intake_data(schema, new_intake_data)
    if not is_valid:
        return jsonify({
            'success': False, 
            'error': 'Please answer all required questions',
            'missing': missing
        }), 400
    
    # Find which questions changed
    changed_questions = {}
    for field_id in new_intake_data:
        old_val = old_intake_data.get(field_id)
        new_val = new_intake_data.get(field_id)
        if old_val != new_val:
            # Format values for display
            def format_val(v):
                if v is True:
                    return 'Yes'
                if v is False:
                    return 'No'
                if v is None:
                    return 'Not answered'
                return str(v)
            
            changed_questions[field_id] = {
                'label': question_labels.get(field_id, field_id),
                'old_value': format_val(old_val),
                'new_value': format_val(new_val)
            }
    
    # Build a map of document slug -> triggering rule condition
    doc_rules = {}
    for rule in schema.get('document_rules', []):
        slug = rule['slug']
        if rule.get('always'):
            doc_rules[slug] = {'always': True, 'name': rule['name']}
        elif 'condition' in rule:
            cond = rule['condition']
            doc_rules[slug] = {
                'field': cond.get('field'),
                'name': rule['name'],
                'condition': cond
            }
    
    # Evaluate document rules with new answers
    required_docs = evaluate_document_rules(schema, new_intake_data)
    
    # Get existing documents
    existing_docs = {doc.template_slug: doc for doc in transaction.documents.all()}
    existing_slugs = set(existing_docs.keys())
    
    # Get required slugs
    required_slugs = {doc['slug'] for doc in required_docs}
    required_docs_by_slug = {doc['slug']: doc for doc in required_docs}
    
    # Compute diff
    to_keep = existing_slugs & required_slugs
    to_remove = existing_slugs - required_slugs
    to_add = required_slugs - existing_slugs
    
    # Helper to build explanation for a document change
    def get_change_explanation(slug, is_addition):
        rule = doc_rules.get(slug, {})
        if rule.get('always'):
            return None  # Always-included docs don't need explanation
        
        field = rule.get('field')
        if field and field in changed_questions:
            change = changed_questions[field]
            if is_addition:
                return f"You changed \"{change['label']}\" from {change['old_value']} to {change['new_value']}"
            else:
                return f"You changed \"{change['label']}\" from {change['old_value']} to {change['new_value']}"
        return None
    
    # Check for blocked removals (sent/signed docs)
    blocked_removals = []
    safe_removals = []
    for slug in to_remove:
        doc = existing_docs[slug]
        explanation = get_change_explanation(slug, False)
        
        if doc.status in ('sent', 'signed'):
            blocked_removals.append({
                'slug': slug,
                'name': doc.template_name,
                'status': doc.status,
                'explanation': explanation,
                'blocked_reason': f'This document is already {doc.status} and cannot be automatically removed. Void it first if you need to remove it.'
            })
        else:
            safe_removals.append({
                'slug': slug,
                'name': doc.template_name,
                'status': doc.status,
                'explanation': explanation
            })
    
    # Build additions list with explanations
    additions = []
    for slug in to_add:
        doc_info = required_docs_by_slug[slug]
        explanation = get_change_explanation(slug, True)
        additions.append({
            'slug': slug,
            'name': doc_info['name'],
            'explanation': explanation
        })
    
    # Build keep list
    kept = []
    for slug in to_keep:
        doc = existing_docs[slug]
        kept.append({
            'slug': slug,
            'name': doc.template_name,
            'status': doc.status
        })
    
    # Determine if this is initial generation or update
    is_initial = len(existing_slugs) == 0
    has_changes = len(to_add) > 0 or len(safe_removals) > 0
    
    return jsonify({
        'success': True,
        'is_initial': is_initial,
        'has_changes': has_changes,
        'summary': {
            'total_docs': len(required_docs),
            'adding': len(additions),
            'removing': len(safe_removals),
            'keeping': len(kept),
            'blocked': len(blocked_removals)
        },
        'additions': additions,
        'removals': safe_removals,
        'kept': kept,
        'blocked': blocked_removals,
        'changed_questions': list(changed_questions.values())
    })


@transactions_bp.route('/<int:id>/intake/generate-package', methods=['POST'])
@login_required
@transactions_required
def generate_document_package(id):
    """Generate the document package based on intake answers."""
    from services.intake_service import get_intake_schema, evaluate_document_rules, validate_intake_data
    from services.documents import DocumentLoader, FieldResolver
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
        # =================================================================
        # SMART DIFF-BASED SYNC
        # Instead of deleting all docs, compare old vs new and only
        # add/remove what changed. Preserves filled data and signatures.
        # =================================================================
        
        # Get existing documents indexed by slug
        existing_docs = {doc.template_slug: doc for doc in transaction.documents.all()}
        existing_slugs = set(existing_docs.keys())
        
        # Get required slugs from new rules
        required_slugs = {doc['slug'] for doc in required_docs}
        required_docs_by_slug = {doc['slug']: doc for doc in required_docs}
        
        # Compute diff
        to_keep = existing_slugs & required_slugs
        to_remove = existing_slugs - required_slugs
        to_add = required_slugs - existing_slugs
        
        # Track results for user feedback
        added_count = 0
        removed_count = 0
        blocked_removals = []
        
        # =================================================================
        # HANDLE REMOVALS (with safety check for sent/signed docs)
        # =================================================================
        for slug in to_remove:
            doc = existing_docs[slug]
            
            # Safety check: don't auto-remove docs that are sent or signed
            if doc.status in ('sent', 'signed'):
                blocked_removals.append(doc.template_name)
                continue
            
            # Log removal before deleting
            audit_service.log_document_removed(
                transaction_id=transaction.id,
                document_id=doc.id,
                template_name=doc.template_name
            )
            
            # Delete the document (cascade handles signatures)
            db.session.delete(doc)
            removed_count += 1
        
        # =================================================================
        # HANDLE ADDITIONS (create new TransactionDocument records)
        # =================================================================
        for slug in to_add:
            doc_info = required_docs_by_slug[slug]
            
            # Check if this is a preview-only document
            definition = DocumentLoader.get(slug)
            is_preview = definition and definition.is_pdf_preview
            
            tx_doc = TransactionDocument(
                transaction_id=transaction.id,
                template_slug=slug,
                template_name=doc_info['name'],
                included_reason=doc_info['reason'] if not doc_info.get('always') else None,
                status='filled' if is_preview else 'pending'
            )
            
            # Auto-populate field_data for preview-only documents
            if is_preview and definition:
                context = {
                    'user': current_user,
                    'transaction': transaction,
                    'form': {}
                }
                resolved_fields = FieldResolver.resolve(definition, context)
                field_data = {}
                for field in resolved_fields:
                    if field.value:
                        field_data[field.field_key] = field.value
                tx_doc.field_data = field_data
            
            db.session.add(tx_doc)
            db.session.flush()  # Get the ID for audit log
            
            # Log addition
            audit_service.log_document_added(tx_doc, tx_doc.included_reason)
            added_count += 1
        
        # =================================================================
        # LOG PACKAGE SYNC EVENT (if this is a regeneration)
        # =================================================================
        if existing_slugs:
            # This is a re-sync, not initial generation
            # Calculate actually removed (excluding blocked)
            actually_removed = [s for s in to_remove if existing_docs[s].status not in ('sent', 'signed')]
            
            audit_service.log_event(
                event_type=AuditEvent.DOCUMENT_PACKAGE_SYNCED,
                transaction_id=transaction.id,
                event_data={
                    'added': list(to_add),
                    'removed': actually_removed,
                    'kept': list(to_keep),
                    'blocked': blocked_removals
                }
            )
        else:
            # Initial generation
            all_docs = transaction.documents.all()
            audit_service.log_document_package_generated(transaction, all_docs)

        db.session.commit()
        
        # Build user feedback message
        messages = []
        if added_count:
            messages.append(f'{added_count} document(s) added')
        if removed_count:
            messages.append(f'{removed_count} document(s) removed')
        if to_keep and not added_count and not removed_count:
            messages.append('No changes needed')
        if not existing_slugs:
            messages = [f'{len(required_docs)} document(s) generated']
        
        if blocked_removals:
            flash(f'Warning: Could not remove {", ".join(blocked_removals)} because they are already sent/signed. Void them first if needed.', 'warning')
        
        flash(f'Document package updated: {", ".join(messages)}!', 'success')
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
        db.session.flush()  # Get doc ID

        # Log audit event
        audit_service.log_document_added(doc, reason)

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

    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    doc = TransactionDocument.query.get_or_404(doc_id)

    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404

    try:
        # Log audit event before deletion
        audit_service.log_document_removed(transaction.id, doc.id, doc.template_name)

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
    from services.documents import (
        DocumentLoader, DocumentType, FieldResolver, RoleBuilder, DocuSealClient
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        abort(404)
    
    # Get document definition from new system
    definition = DocumentLoader.get(doc.template_slug)
    
    # Check if this is a preview-only document (like IABS)
    if definition and definition.is_pdf_preview:
        # Build context for field resolution
        context = {
            'user': current_user,
            'transaction': transaction,
            'form': doc.field_data or {}
        }
        
        # Resolve fields from definition
        resolved_fields = FieldResolver.resolve(definition, context)
        
        # Store resolved field data for display
        field_data_for_display = {}
        for field in resolved_fields:
            if field.value:
                field_data_for_display[field.field_key] = field.value
        
        # Update document field_data
        # For IABS check agent_name, for others just check if we have any resolved fields
        if not doc.field_data or (doc.template_slug == 'iabs' and not doc.field_data.get('agent_name')):
            doc.field_data = field_data_for_display
            doc.status = 'filled'
            db.session.commit()
        elif field_data_for_display:
            doc.field_data = field_data_for_display
            if doc.status == 'pending':
                doc.status = 'filled'
                db.session.commit()
        
        preview_info = {
            'embed_src': None,
            'mock_mode': DocuSealClient.is_mock_mode(),
            'error': None
        }
        
        if not DocuSealClient.is_mock_mode():
            try:
                # Build submitters using new system
                submitters = RoleBuilder.build_for_preview(
                    definition, resolved_fields, context
                )
                
                # Create preview submission
                preview_result = DocuSealClient.create_preview_submission(
                    definition.docuseal_template_id,
                    submitters
                )
                
                if preview_result and preview_result.get('slug'):
                    preview_info['embed_src'] = f"https://docuseal.com/s/{preview_result['slug']}"
            except Exception as e:
                preview_info['error'] = str(e)
        
        # Build config object for template compatibility
        config = type('Config', (), {
            'name': definition.name,
            'color': definition.display.color,
            'icon': definition.display.icon
        })()
        
        # Use appropriate preview template based on document type
        # IABS needs agent/supervisor info display, others just need simple preview
        if doc.template_slug == 'iabs':
            template_name = 'transactions/iabs_preview.html'
        else:
            template_name = 'transactions/simple_preview.html'
        
        return render_template(
            template_name,
            transaction=transaction,
            document=doc,
            config=config,
            preview_info=preview_info
        )
    
    # Get participants for the form
    participants = transaction.participants.all()
    
    # Prefill data from transaction and intake
    prefill_data = build_prefill_data(transaction, participants)
    
    # Merge with any existing field data
    if doc.field_data:
        prefill_data.update(doc.field_data)
    
    # Use form template from definition if available
    if definition and definition.is_form_driven and definition.form:
        template_name = f"transactions/{definition.form.template}"
        return render_template(
            template_name,
            transaction=transaction,
            document=doc,
            participants=participants,
            prefill_data=prefill_data
        )
    
    # Fallback to hardcoded templates for documents not yet in new system
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
        
        # Track changed fields for audit
        old_fields = set(doc.field_data.keys()) if doc.field_data else set()
        new_fields = set(field_data.keys())
        changed_fields = list(new_fields - old_fields) if old_fields != new_fields else list(new_fields)

        # Save field data
        doc.field_data = field_data
        doc.status = 'filled'

        # Log audit event
        audit_service.log_document_filled(doc, changed_fields)

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

# Document slugs with specialized form UIs are defined in documents/definitions/*.yml
# Use DocumentLoader.get_sorted() to get them dynamically


@transactions_bp.route('/<int:id>/documents/fill-all')
@login_required
@transactions_required
def fill_all_documents(id):
    """
    Show a combined form experience for filling multiple documents at once.
    Includes documents with specialized form UIs and preview-only documents.
    Form UI documents are shown first, followed by preview-only documents as PDF embeds.
    """
    from services.documents import (
        DocumentLoader, DocumentType, FieldResolver, RoleBuilder, DocuSealClient
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    # Get all loaded document definitions
    all_definitions = DocumentLoader.get_sorted()
    form_driven_slugs = [d.slug for d in all_definitions if d.is_form_driven]
    preview_slugs = [d.slug for d in all_definitions if d.is_pdf_preview]
    
    # Get all documents for this transaction that have specialized forms
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(form_driven_slugs)
    ).order_by(TransactionDocument.created_at).all()
    
    # Get preview-only documents (like IABS)
    preview_documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(preview_slugs)
    ).order_by(TransactionDocument.created_at).all()
    
    if not documents and not preview_documents:
        flash('No documents available to fill. Use individual document fill for other documents.', 'info')
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
    
    # Build doc_configs from new system for template compatibility
    doc_configs = {}
    for doc in documents:
        definition = DocumentLoader.get(doc.template_slug)
        if definition:
            # Create a compatible config object
            doc_configs[doc.template_slug] = type('Config', (), {
                'slug': definition.slug,
                'name': definition.name,
                'partial_template': f"transactions/partials/{definition.form.partial}" if definition.form else None,
                'color': definition.display.color,
                'icon': definition.display.icon,
                'sort_order': definition.display.sort_order,
                'section_color_var': definition.display.color,
                'badge_classes': f"bg-opacity-10 text-opacity-90",
                'gradient_class': f"from-opacity-50 to-opacity-60"
            })()
    
    # Create preview submissions for preview-only documents using new system
    preview_data = []
    for doc in preview_documents:
        definition = DocumentLoader.get(doc.template_slug)
        if not definition:
            continue
        
        # Build context for field resolution
        context = {
            'user': current_user,
            'transaction': transaction,
            'form': doc.field_data or {}
        }
        
        # Resolve fields from definition
        resolved_fields = FieldResolver.resolve(definition, context)
        
        # Store resolved field data
        field_data_for_display = {}
        for field in resolved_fields:
            if field.value:
                field_data_for_display[field.field_key] = field.value
        
        # Update document field_data and status
        doc.field_data = field_data_for_display
        doc.status = 'filled'
        db.session.commit()
        
        # Build compatible config object
        config = type('Config', (), {
            'name': definition.name,
            'color': definition.display.color,
            'icon': definition.display.icon
        })()
        
        preview_info = {
            'doc': doc,
            'config': config,
            'embed_src': None,
            'mock_mode': DocuSealClient.is_mock_mode(),
            'error': None
        }
        
        if not DocuSealClient.is_mock_mode():
            try:
                # Build submitters using new system
                submitters = RoleBuilder.build_for_preview(
                    definition, resolved_fields, context
                )
                
                # Create preview submission
                preview_result = DocuSealClient.create_preview_submission(
                    definition.docuseal_template_id,
                    submitters
                )
                
                if preview_result and preview_result.get('slug'):
                    preview_info['embed_src'] = f"https://docuseal.com/s/{preview_result['slug']}"
            except Exception as e:
                preview_info['error'] = str(e)
        
        preview_data.append(preview_info)
    
    return render_template(
        'transactions/fill_all_documents.html',
        transaction=transaction,
        documents=documents,
        participants=participants,
        prefill_data=prefill_data,
        doc_configs=doc_configs,  # Pass configs for dynamic template rendering
        preview_data=preview_data,  # Preview-only documents with embed URLs
        has_preview_docs=len(preview_data) > 0
    )


@transactions_bp.route('/<int:id>/documents/fill-all', methods=['POST'])
@login_required
@transactions_required
def save_all_documents(id):
    """
    Save form data for multiple documents at once.
    Form fields are prefixed with doc slug to separate document-specific data.
    """
    from services.documents import DocumentLoader
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    # Get form-driven document slugs from new system
    all_definitions = DocumentLoader.get_sorted()
    form_driven_slugs = [d.slug for d in all_definitions if d.is_form_driven]
    
    # Get documents with specialized forms
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(form_driven_slugs)
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
    from services.documents import (
        DocumentLoader, FieldResolver, RoleBuilder, DocuSealClient
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    # Get all document slugs from new system
    all_definitions = DocumentLoader.get_sorted()
    all_valid_slugs = [d.slug for d in all_definitions]
    
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(all_valid_slugs),
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
        # Get document definition from new system
        definition = DocumentLoader.get(doc.template_slug)
        
        # Build config object for template compatibility
        config = None
        if definition:
            config = type('Config', (), {
                'slug': definition.slug,
                'name': definition.name,
                'color': definition.display.color,
                'icon': definition.display.icon,
                'section_color_var': definition.display.color
            })()
        
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
        if not DocuSealClient.is_mock_mode() and definition:
            try:
                # Build context for field resolution
                context = {
                    'user': current_user,
                    'transaction': transaction,
                    'form': doc.field_data or {}
                }
                
                # Resolve fields using new system
                resolved_fields = FieldResolver.resolve(definition, context)
                
                # Build submitters using new system
                submitters = RoleBuilder.build_for_preview(
                    definition, resolved_fields, context
                )
                
                # Create preview submission using new DocuSealClient
                preview_result = DocuSealClient.create_preview_submission(
                    definition.docuseal_template_id,
                    submitters
                )
                
                if preview_result and preview_result.get('slug'):
                    doc_preview['embed_slug'] = preview_result['slug']
                    doc_preview['embed_src'] = f"https://docuseal.com/s/{preview_result['slug']}"
                
                # Update document status
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
        doc_configs={},  # No longer needed - config is in preview_docs
        mock_mode=DocuSealClient.is_mock_mode()
    )


@transactions_bp.route('/<int:id>/documents/send-all', methods=['POST'])
@login_required
@transactions_required
def send_all_for_signature(id):
    """
    Send all filled documents as ONE envelope using DocuSeal's merge templates API.
    This merges multiple templates into one and sends a single email to signers.
    
    Handles multiple roles across documents:
    - Seller: Primary seller (required)
    - Seller 2: Co-seller (optional)
    - Agent: Listing agent, auto-completed with pre-filled data
    - Broker: Same as agent for most docs, auto-completed
    """
    from services.documents import (
        DocumentLoader, FieldResolver, RoleBuilder, DocuSealClient
    )
    from services.documents.types import Submitter
    from services.documents.exceptions import DocuSealAPIError
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    # Get all document slugs from new system
    all_definitions = DocumentLoader.get_sorted()
    all_valid_slugs = [d.slug for d in all_definitions]
    
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(all_valid_slugs),
        TransactionDocument.status.in_(['filled', 'draft', 'generated'])
    ).order_by(TransactionDocument.created_at).all()
    
    if not documents:
        flash('No documents ready to send. Please fill out the documents first.', 'warning')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    # Get participants
    participants = transaction.participants.all()
    
    # Get key participants
    seller = next((p for p in participants if p.role == 'seller' and p.is_primary), None)
    co_seller = next((p for p in participants if p.role == 'co_seller'), None)
    listing_agent = next((p for p in participants if p.role == 'listing_agent'), None)
    
    if not seller or not seller.display_email:
        flash('No seller with email found. Please add seller contact information.', 'error')
        return redirect(url_for('transactions.preview_all_documents', id=id))
    
    try:
        # Step 1: Collect all template IDs, unique roles, and resolve fields for each document
        template_ids = []
        unique_docuseal_roles = set()
        auto_complete_roles = set()  # Roles that should be auto-completed (Agent, Broker)
        
        # Fields grouped by docuseal_role (not role_key)
        fields_by_docuseal_role = {}
        
        for doc in documents:
            definition = DocumentLoader.get(doc.template_slug)
            if not definition or not definition.docuseal_template_id:
                continue
            
            template_ids.append(definition.docuseal_template_id)
            
            # Collect unique roles from this document
            for role_def in definition.roles:
                unique_docuseal_roles.add(role_def.docuseal_role)
                if role_def.auto_complete:
                    auto_complete_roles.add(role_def.docuseal_role)
            
            # Build context for field resolution
            context = {
                'user': current_user,
                'transaction': transaction,
                'form': doc.field_data or {}
            }
            
            # Resolve fields using new system
            resolved_fields = FieldResolver.resolve(definition, context)
            
            # Group fields by docuseal_role (look up role_key -> docuseal_role mapping)
            for field in resolved_fields:
                # Skip manual/signature fields - these are filled by the signer
                if field.is_manual:
                    continue
                
                # Skip fields with no value
                if field.value is None:
                    continue
                
                # Find the role definition for this field's role_key
                role_def = definition.get_role(field.role_key)
                if role_def:
                    docuseal_role = role_def.docuseal_role
                    if docuseal_role not in fields_by_docuseal_role:
                        fields_by_docuseal_role[docuseal_role] = []
                    
                    docuseal_field = {'name': field.docuseal_field, 'default_value': str(field.value)}
                    fields_by_docuseal_role[docuseal_role].append(docuseal_field)
        
        if not template_ids:
            flash('No valid templates found for the documents.', 'error')
            return redirect(url_for('transactions.preview_all_documents', id=id))
        
        # Step 2: Merge templates into one combined template
        # DON'T specify roles - let DocuSeal combine them automatically
        # This preserves pre-filled field values from original templates (e.g., Broker info in IABS)
        merged_template = DocuSealClient.merge_templates(
            template_ids=template_ids,
            name=f"Document Package - {transaction.street_address} - TX{transaction.id}",
            roles=None,  # Let DocuSeal preserve original roles and their pre-filled values
            external_id=f"tx-{transaction.id}-merged-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        )
        
        merged_template_id = merged_template.get('id')
        
        # Step 3: Build submitters for each unique role
        agent_email = listing_agent.display_email if listing_agent else current_user.email
        agent_name = listing_agent.display_name if listing_agent else f"{current_user.first_name} {current_user.last_name}"
        
        submitters = []
        participant_by_role = {}  # Track participant for each role for signature records
        
        for docuseal_role in unique_docuseal_roles:
            # Determine email/name based on role
            if docuseal_role == 'Seller':
                email = seller.display_email
                name = seller.display_name
                participant_by_role['Seller'] = seller
            elif docuseal_role == 'Seller 2':
                # Skip Seller 2 if no co-seller
                if not co_seller or not co_seller.display_email:
                    continue
                email = co_seller.display_email
                name = co_seller.display_name
                participant_by_role['Seller 2'] = co_seller
            elif docuseal_role in ['Agent', 'Broker']:
                # Agent and Broker both use the listing agent/current user
                email = agent_email
                name = agent_name
                participant_by_role[docuseal_role] = listing_agent
            else:
                # Unknown role - skip
                continue
            
            # Check if this role should be auto-completed
            is_auto_complete = docuseal_role in auto_complete_roles
            
            submitters.append(Submitter(
                role=docuseal_role,
                email=email,
                name=name,
                fields=fields_by_docuseal_role.get(docuseal_role, []),
                completed=is_auto_complete
            ))
        
        if not submitters:
            flash('No valid submitters could be created. Please check participant information.', 'error')
            return redirect(url_for('transactions.preview_all_documents', id=id))
        
        # Step 4: Create ONE submission from the merged template
        result = DocuSealClient.create_submission(
            merged_template_id,
            submitters,
            send_email=True,
            message={
                'subject': f'Documents Ready for Signature - {transaction.street_address}',
                'body': f'Please review and sign your documents for {transaction.full_address}. Click here to sign: {{{{submitter.link}}}}'
            }
        )
        
        submission_id = result.get('id')
        
        # Step 5: Update ALL documents with the same submission ID
        for doc in documents:
            doc.status = 'sent'
            doc.docuseal_submission_id = str(submission_id)
            doc.sent_at = datetime.utcnow()
            doc.sent_by_id = current_user.id  # Track who sent

            # Create signature records for each signer
            for submitter_data in result.get('submitters', []):
                role = submitter_data.get('role')
                participant = participant_by_role.get(role)

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

        # Log audit event for envelope sent
        signer_info = [{'email': s.email, 'role': s.role} for s in submitters]
        audit_service.log_envelope_sent(transaction, documents, submitters, submission_id)

        db.session.commit()
        
        doc_count = len(documents)
        if DocuSealClient.is_mock_mode():
            flash(f'[MOCK MODE] {doc_count} document(s) sent as ONE envelope! Submission ID: {submission_id}', 'success')
        else:
            flash(f'{doc_count} document(s) sent as one envelope to signers!', 'success')
        
        return redirect(url_for('transactions.view_transaction', id=id))
    
    except DocuSealAPIError as e:
        db.session.rollback()
        error_detail = e.response_body if e.response_body else str(e)
        flash(f'DocuSeal error: {error_detail}', 'error')
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
    from services.documents import (
        DocumentLoader, DocumentType, FieldResolver, RoleBuilder, DocuSealClient
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        abort(403)
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        abort(404)
    
    # Document must be filled to preview
    if doc.status not in ['filled', 'generated', 'draft']:
        flash('Please fill out the document form first.', 'error')
        return redirect(url_for('transactions.document_form', id=id, doc_id=doc_id))
    
    # Get document definition from new system
    definition = DocumentLoader.get(doc.template_slug)
    
    if not definition:
        flash('This document template is not yet configured.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    # Check if template is configured in DocuSeal
    if not definition.docuseal_template_id and not DocuSealClient.is_mock_mode():
        flash('This document template is not yet configured for e-signature.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    try:
        # Build context for field resolution
        context = {
            'user': current_user,
            'transaction': transaction,
            'form': doc.field_data or {}
        }
        
        # Resolve fields using new system
        resolved_fields = FieldResolver.resolve(definition, context)
        
        # Build submitters using new system
        submitters = RoleBuilder.build_for_preview(
            definition, resolved_fields, context
        )
        
        # Create preview submission using new DocuSealClient
        preview_result = DocuSealClient.create_preview_submission(
            definition.docuseal_template_id,
            submitters
        )
        
        embed_slug = preview_result.get('slug', '')
        embed_src = f"https://docuseal.com/s/{embed_slug}" if embed_slug else ''
        
        # Store preview submission ID so we can archive it later
        doc.docuseal_submission_id = preview_result.get('id')
        doc.status = 'generated'  # Mark as generated/ready for review
        db.session.commit()
        
        return render_template(
            'transactions/document_preview.html',
            transaction=transaction,
            document=doc,
            embed_src=embed_src,
            embed_slug=embed_slug,
            submission_id=preview_result.get('id'),
            mock_mode=DocuSealClient.is_mock_mode()
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
    from services.documents import (
        DocumentLoader, FieldResolver, RoleBuilder, DocuSealClient
    )
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
    
    # Get document definition from new system
    definition = DocumentLoader.get(doc.template_slug)
    
    if not definition:
        return jsonify({
            'success': False,
            'error': 'Document template not configured'
        }), 400
    
    try:
        # Build context for field resolution
        context = {
            'user': current_user,
            'transaction': transaction,
            'form': doc.field_data or {}
        }
        
        # Resolve fields using new system
        resolved_fields = FieldResolver.resolve(definition, context)
        
        # Build submitters for sending (not preview) using new system
        submitters = RoleBuilder.build_for_send(
            definition, resolved_fields, context
        )
        
        if not submitters:
            return jsonify({
                'success': False,
                'error': 'No signers found. Add participants with email addresses.'
            }), 400
        
        # Create submission in DocuSeal with send_email=True
        submission = DocuSealClient.create_submission(
            definition.docuseal_template_id,
            submitters,
            send_email=True,
            message={
                'subject': f'Document Ready for Signature: {doc.template_name}',
                'body': f'Please sign the {doc.template_name} for {transaction.street_address}.\n\nClick here to sign: {{{{submitter.link}}}}'
            }
        )
        
        # Update document with DocuSeal submission info
        doc.docuseal_submission_id = submission['id']
        doc.sent_at = db.func.now()
        doc.status = 'sent'
        
        # Get participants for signature record linking
        participants = transaction.participants.all()
        
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
        
        # Track who sent the document
        doc.sent_by_id = current_user.id
        
        # Log audit event for document sent (must be before commit)
        signer_info = [{'email': s.get('email'), 'name': s.get('name', ''), 'role': s.get('role')} for s in submission.get('submitters', [])]
        audit_service.log_document_sent(doc, signer_info, submission['id'])
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Document sent for signature!',
            'submission_id': submission['id'],
            'submitters': len(submitters),
            'mock_mode': DocuSealClient.is_mock_mode()
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
        # Log audit event before voiding
        audit_service.log_document_voided(doc)

        # Clear DocuSeal submission info
        doc.docuseal_submission_id = None
        doc.sent_at = None
        doc.sent_by_id = None
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

    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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

        # Log audit event
        audit_service.log_document_resent(doc, result.get('resent_count', 0))

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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
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

        # Extract signer info from payload
        submitter_data = payload.get('data', {})
        signer_email = submitter_data.get('email')
        signer_role = submitter_data.get('role')

        # Find the document by submission ID
        doc = TransactionDocument.query.filter_by(
            docuseal_submission_id=str(submission_id)
        ).first()

        if not doc:
            # Log webhook received even if no matching doc (for debugging)
            audit_service.log_webhook_received(None, None, event_type, payload)
            return jsonify({'received': True, 'matched': False})

        # Log webhook received for audit trail
        audit_service.log_webhook_received(doc.transaction_id, doc.id, event_type, payload)

        # Find matching signature record if possible
        signature = None
        if signer_email:
            signature = DocumentSignature.query.filter_by(
                document_id=doc.id,
                signer_email=signer_email
            ).first()

        # Update based on event type
        if event_type == 'form.viewed':
            # Update signature record with viewed timestamp
            if signature:
                signature.viewed_at = datetime.utcnow()
                signature.status = 'viewed'

            # Log document viewed event
            audit_service.log_document_viewed(doc, signature, {
                'signer_email': signer_email,
                'signer_role': signer_role,
                'submission_id': submission_id
            })

            db.session.commit()

        elif event_type == 'form.started':
            # Signer started filling - just log
            pass

        elif event_type == 'form.completed':
            # Check if this is a single signer completion or all signers
            # For now assume all signers finished
            doc.status = 'signed'
            doc.signed_at = datetime.utcnow()

            # Update all signature records
            signatures = DocumentSignature.query.filter_by(document_id=doc.id).all()
            for sig in signatures:
                sig.signed_at = datetime.utcnow()
                sig.status = 'signed'

            # Log document signed event
            audit_service.log_document_signed(doc, signature, {
                'signer_email': signer_email,
                'signer_role': signer_role,
                'submission_id': submission_id
            })

            db.session.commit()

        elif event_type == 'form.declined':
            # Signer declined to sign
            decline_reason = submitter_data.get('decline_reason', '')
            signer_name = submitter_data.get('name', '')
            
            doc.status = 'declined'
            
            # Update the specific signature record that declined
            if signature:
                signature.status = 'declined'
            
            # Log document declined event
            audit_service.log_document_declined(doc, signature, {
                'signer_email': signer_email,
                'signer_name': signer_name,
                'signer_role': signer_role,
                'decline_reason': decline_reason,
                'submission_id': submission_id
            })

            db.session.commit()

        return jsonify({
            'received': True,
            'event': event_type,
            'document_id': doc.id if doc else None
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
