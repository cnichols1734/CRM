# routes/transactions/crud.py
"""
Transaction CRUD routes (create, read, update, delete).
"""

from datetime import datetime as dt
from flask import request, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import (
    db, Transaction, TransactionType, TransactionParticipant,
    TransactionDocument, Contact, ContactFile
)
from sqlalchemy.orm import joinedload, selectinload
from services import audit_service
from . import transactions_bp
from .decorators import transactions_required


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
    
    # Admin view toggle - allow admins to see all transactions in their org
    show_all = request.args.get('view') == 'all' and current_user.org_role in ('admin', 'owner')
    
    # Base query - ALWAYS filter by organization, then by user unless admin viewing all
    if show_all:
        query = Transaction.query.filter_by(organization_id=current_user.organization_id)
    else:
        query = Transaction.query.filter_by(
            organization_id=current_user.organization_id,
            created_by_id=current_user.id
        )
    
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
    
    # Order by most recent first (participants is dynamic, can't eager load)
    transactions = query.options(
        joinedload(Transaction.transaction_type)
    ).order_by(Transaction.created_at.desc()).all()
    
    # Pre-fetch all primary participants for these transactions in one query
    tx_ids = [tx.id for tx in transactions]
    transaction_contacts = {}
    if tx_ids:
        primary_participants = TransactionParticipant.query.options(
            joinedload(TransactionParticipant.contact)
        ).filter(
            TransactionParticipant.transaction_id.in_(tx_ids),
            TransactionParticipant.is_primary == True,
            TransactionParticipant.role.in_(['seller', 'buyer', 'landlord', 'tenant', 'referral_client'])
        ).all()
        
        for p in primary_participants:
            transaction_contacts[p.transaction_id] = {
                'name': p.display_name,
                'email': p.display_email,
                'contact_id': p.contact_id
            }
    
    # Get transaction types for filter dropdown (org-scoped, cached)
    from services.cache_helpers import get_org_transaction_types
    transaction_types = get_org_transaction_types(current_user.organization_id)
    
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
    # Get transaction types for selection (org-scoped, cached)
    from services.cache_helpers import get_org_transaction_types
    transaction_types = get_org_transaction_types(current_user.organization_id)
    
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
        
        # Validate all selected contacts have required fields (name and email)
        for contact_id in contact_ids:
            contact = Contact.query.get(int(contact_id))
            if not contact or contact.user_id != current_user.id:
                flash('One or more selected contacts could not be found.', 'error')
                return redirect(url_for('transactions.new_transaction'))
            
            if not contact.first_name or not contact.last_name:
                flash(f'Contact "{contact.first_name or ""} {contact.last_name or ""}" is missing a name. Please update the contact first.', 'error')
                return redirect(url_for('transactions.new_transaction'))
            
            if not contact.email:
                flash(f'Contact "{contact.first_name} {contact.last_name}" is missing an email address. Please update the contact first.', 'error')
                return redirect(url_for('transactions.new_transaction'))
        
        # Get the transaction type to determine participant role and default status
        tx_type = TransactionType.query.get(int(transaction_type_id))
        
        # Determine default status based on transaction type
        # Buyer transactions start with 'showing', sellers start with 'preparing_to_list'
        default_status = 'showing' if tx_type and tx_type.name == 'buyer' else 'preparing_to_list'
        
        # Create the transaction
        transaction = Transaction(
            organization_id=current_user.organization_id,
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
                    organization_id=current_user.organization_id,
                    transaction_id=transaction.id,
                    contact_id=contact.id,
                    role=participant_role if i == 0 else f'co_{participant_role}',
                    is_primary=(i == 0)
                )
                db.session.add(participant)
        
        # Add current user as listing agent (for seller/landlord transactions)
        if tx_type.name in ['seller', 'landlord']:
            agent_participant = TransactionParticipant(
                organization_id=current_user.organization_id,
                transaction_id=transaction.id,
                user_id=current_user.id,
                role='listing_agent',
                is_primary=True
            )
            db.session.add(agent_participant)
        elif tx_type.name in ['buyer', 'tenant']:
            agent_participant = TransactionParticipant(
                organization_id=current_user.organization_id,
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
    from datetime import datetime
    
    # Load transaction with transaction_type - SCOPED TO ORGANIZATION
    transaction = Transaction.query.options(
        joinedload(Transaction.transaction_type)
    ).filter_by(id=id, organization_id=current_user.organization_id).first_or_404()
    
    # Ensure user owns this transaction or is org admin
    if transaction.created_by_id != current_user.id and current_user.org_role not in ('admin', 'owner'):
        abort(403)
    
    # Load participants with contacts in one query
    participants = TransactionParticipant.query.options(
        joinedload(TransactionParticipant.contact)
    ).filter_by(transaction_id=id).all()
    
    # Load documents sorted by created_at
    documents = TransactionDocument.query.filter_by(
        transaction_id=id
    ).order_by(TransactionDocument.created_at).all()
    
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
        # Find the listing agreement document (use Python filter on already-loaded documents)
        listing_doc = next((d for d in documents if d.template_slug == 'listing-agreement'), None)
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
                    dt_obj = datetime.strptime(date_str, '%Y-%m-%d')
                    return dt_obj.strftime('%B %d, %Y')
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
    
    # Get RentCast data for buyer transactions
    rentcast_data = None
    rentcast_fetched_at = None
    if transaction.transaction_type.name == 'buyer':
        rentcast_data = transaction.rentcast_data
        rentcast_fetched_at = transaction.rentcast_fetched_at
    
    return render_template(
        'transactions/detail.html',
        transaction=transaction,
        participants=participants,
        documents=documents,
        contact_files=contact_files,
        listing_info=listing_info,
        lockbox_combo=lockbox_combo,
        rentcast_data=rentcast_data,
        rentcast_fetched_at=rentcast_fetched_at
    )


@transactions_bp.route('/<int:id>/edit')
@login_required
@transactions_required
def edit_transaction(id):
    """Show edit form for a transaction."""
    transaction = Transaction.query.filter_by(
        id=id, organization_id=current_user.organization_id
    ).first_or_404()
    
    if transaction.created_by_id != current_user.id and current_user.org_role not in ('admin', 'owner'):
        abort(403)
    
    # Get transaction types (org-scoped)
    # Cached transaction types
    from services.cache_helpers import get_org_transaction_types
    transaction_types = get_org_transaction_types(current_user.organization_id)
    
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
    transaction = Transaction.query.filter_by(
        id=id, organization_id=current_user.organization_id
    ).first_or_404()
    
    if transaction.created_by_id != current_user.id and current_user.org_role not in ('admin', 'owner'):
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
    transaction = Transaction.query.filter_by(
        id=id, organization_id=current_user.organization_id
    ).first_or_404()

    if transaction.created_by_id != current_user.id and current_user.org_role not in ('admin', 'owner'):
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
