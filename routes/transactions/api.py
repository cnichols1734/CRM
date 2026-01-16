# routes/transactions/api.py
"""
Transaction API endpoints (JSON responses).
"""

from datetime import datetime, timedelta
from flask import request, jsonify
from flask_login import login_required, current_user
from models import db, Transaction, Contact
from services import audit_service
from config import Config
from . import transactions_bp
from .decorators import transactions_required


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
# RENTCAST PROPERTY INTELLIGENCE
# =============================================================================

@transactions_bp.route('/<int:id>/rentcast-data', methods=['POST'])
@login_required
@transactions_required
def fetch_rentcast_data(id):
    """
    Fetch or return cached RentCast property intelligence data.
    
    For buyer transactions only. Implements a cooldown period to prevent
    excessive API usage (default 48 hours between fetches).
    
    Returns:
        - success: bool
        - data: dict (property data)
        - fetched_at: ISO timestamp
        - cached: bool (True if returning cached data within cooldown)
        - message: str (optional message for cached responses)
    """
    from services.rentcast_service import fetch_property_data
    
    transaction = Transaction.query.get_or_404(id)
    
    # Authorization check
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Only allow for buyer transactions
    if transaction.transaction_type.name != 'buyer':
        return jsonify({
            'success': False, 
            'error': 'Property Intelligence is only available for Buyer Representation transactions.'
        }), 400
    
    # Check if we have cached data within the cooldown period
    refresh_hours = Config.RENTCAST_REFRESH_HOURS
    now = datetime.utcnow()
    
    if transaction.rentcast_data and transaction.rentcast_fetched_at:
        time_since_fetch = now - transaction.rentcast_fetched_at
        cooldown_period = timedelta(hours=refresh_hours)
        
        if time_since_fetch < cooldown_period:
            # Return cached data - still within cooldown
            return jsonify({
                'success': True,
                'data': transaction.rentcast_data,
                'fetched_at': transaction.rentcast_fetched_at.isoformat(),
                'cached': True,
                'message': 'You have the most current data available.'
            })
    
    # Fetch fresh data from RentCast API
    result = fetch_property_data(
        street_address=transaction.street_address,
        city=transaction.city,
        state=transaction.state,
        zip_code=transaction.zip_code
    )
    
    if not result['success']:
        return jsonify({
            'success': False,
            'error': result['error']
        }), 400
    
    # Store the data in the transaction
    try:
        transaction.rentcast_data = result['data']
        transaction.rentcast_fetched_at = now
        db.session.commit()
        
        # Log the fetch event
        audit_service.log_event(
            event_type='rentcast_data_fetched',
            transaction_id=transaction.id,
            actor_id=current_user.id,
            description=f"Fetched property intelligence data for {transaction.street_address}",
            event_data={'address_used': result.get('address_used')}
        )
        
        return jsonify({
            'success': True,
            'data': result['data'],
            'fetched_at': now.isoformat(),
            'cached': False
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/rentcast-data', methods=['GET'])
@login_required
@transactions_required
def get_rentcast_data(id):
    """
    Get cached RentCast data for a transaction (if available).
    Does not trigger a new API fetch - use POST for that.
    """
    transaction = Transaction.query.get_or_404(id)
    
    # Authorization check
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    if transaction.rentcast_data and transaction.rentcast_fetched_at:
        return jsonify({
            'success': True,
            'data': transaction.rentcast_data,
            'fetched_at': transaction.rentcast_fetched_at.isoformat(),
            'has_data': True
        })
    else:
        return jsonify({
            'success': True,
            'data': None,
            'fetched_at': None,
            'has_data': False
        })
