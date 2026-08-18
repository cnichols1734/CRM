# routes/transactions/api.py
"""
Transaction API endpoints (JSON responses).
"""

import hashlib
import logging
from datetime import datetime, timedelta
from flask import abort, request, jsonify, render_template
from flask_login import login_required, current_user
from models import (
    db,
    Contact,
    TransactionDocument,
    PartnerContact,
    PartnerOrganization,
    SellerOfferDocument,
)
from services import audit_service
from services.document_review import list_open_reports, pending_toasts
from services.partners import PARTNER_TYPES, partner_search_payload, partner_type_for_role
from services.proposal_service import ProposalService
from services.transaction_auth import CAP_EDIT, CAP_VIEW, get_transaction_for_user
from services.transaction_helpers import build_listing_info
from config import Config
from . import transactions_bp
from .decorators import transactions_required

logger = logging.getLogger(__name__)


def _require_tx(transaction_id, capability=CAP_VIEW):
    tx, decision = get_transaction_for_user(transaction_id, capability=capability)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    return tx


def format_contact_address(contact):
    """Build a single-line address from a contact's stored fields."""
    street = (contact.street_address or '').strip()
    city = (contact.city or '').strip()
    state = (contact.state or '').strip()
    zip_code = (contact.zip_code or '').strip()
    city_state_zip = ', '.join(part for part in [city, ' '.join(part for part in [state, zip_code] if part)] if part)
    if street and city_state_zip:
        return f'{street}, {city_state_zip}'
    return street or city_state_zip


def contact_search_payload(contact):
    """JSON payload for the transaction contact picker."""
    return {
        'id': contact.id,
        'first_name': contact.first_name,
        'last_name': contact.last_name,
        'name': f'{contact.first_name} {contact.last_name}',
        'email': contact.email,
        'phone': contact.phone,
        'street_address': contact.street_address or '',
        'city': contact.city or '',
        'state': contact.state or '',
        'zip_code': contact.zip_code or '',
        'full_address': format_contact_address(contact),
    }


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
    
    return jsonify([contact_search_payload(c) for c in contacts])


@transactions_bp.route('/api/partners/search')
@login_required
@transactions_required
def search_partners():
    """Search org-wide Partner Directory records for the transaction participant picker.

    The `type` param is used to *rank* matching-type results first, not to
    exclude other types.  This way a user searching for "Austin" with role
    Lender will still find "Austin Title" even though it's categorized as a
    title company.
    """
    query = request.args.get('q', '').strip()
    role = request.args.get('role', '').strip()
    preferred_type = request.args.get('type', '').strip() or partner_type_for_role(role)
    if preferred_type not in PARTNER_TYPES:
        preferred_type = None

    base_filter = PartnerOrganization.query.filter_by(
        organization_id=current_user.organization_id,
        is_active=True,
    )

    if query:
        search = f'%{query}%'
        base_filter = base_filter.filter(db.or_(
            PartnerOrganization.name.ilike(search),
            PartnerOrganization.email.ilike(search),
            PartnerOrganization.phone.ilike(search),
            PartnerOrganization.city.ilike(search),
        ))

    # Sort preferred type first, then alphabetical
    if preferred_type:
        type_sort = db.case(
            (PartnerOrganization.partner_type == preferred_type, 0),
            else_=1,
        )
        partners = base_filter.order_by(type_sort, PartnerOrganization.name.asc()).limit(15).all()
    else:
        partners = base_filter.order_by(PartnerOrganization.name.asc()).limit(15).all()

    results = []
    seen = set()

    for partner in partners:
        results.append(partner_search_payload(partner))
        seen.add((partner.id, None))
        contacts = partner.contacts.filter_by(is_active=True).order_by(
            PartnerContact.last_name.asc(),
            PartnerContact.first_name.asc(),
        ).limit(4).all()
        for contact in contacts:
            results.append(partner_search_payload(partner, contact))
            seen.add((partner.id, contact.id))

    if query:
        contact_query = PartnerContact.query.join(PartnerOrganization).filter(
            PartnerContact.organization_id == current_user.organization_id,
            PartnerContact.is_active.is_(True),
            PartnerOrganization.is_active.is_(True),
        )
        search = f'%{query}%'
        contact_query = contact_query.filter(db.or_(
            PartnerContact.first_name.ilike(search),
            PartnerContact.last_name.ilike(search),
            PartnerContact.email.ilike(search),
            PartnerContact.phone.ilike(search),
            PartnerOrganization.name.ilike(search),
        ))

        for contact in contact_query.order_by(PartnerContact.last_name.asc()).limit(12).all():
            key = (contact.partner_organization_id, contact.id)
            if key not in seen:
                results.append(partner_search_payload(contact.partner_organization, contact))
                seen.add(key)

    return jsonify(results[:20])


@transactions_bp.route('/api/<int:id>/signers')
@login_required
@transactions_required
def get_signers(id):
    """Get list of signers for a transaction document."""
    transaction = _require_tx(id, CAP_VIEW)

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
    transaction = _require_tx(id, CAP_EDIT)

    data = request.get_json()
    new_status = data.get('status')
    
    status_options_by_type = {
        'seller': ['preparing_to_list', 'active', 'under_contract', 'closed', 'cancelled'],
        'buyer': ['showing', 'under_contract', 'closed', 'cancelled'],
        'landlord': ['preparing_to_list', 'active', 'under_contract', 'closed', 'cancelled'],
        'tenant': ['showing', 'under_contract', 'closed', 'cancelled'],
        'referral': ['preparing_to_list', 'active', 'under_contract', 'closed', 'cancelled'],
    }
    tx_type_name = transaction.transaction_type.name if transaction.transaction_type else 'seller'
    valid_statuses = status_options_by_type.get(tx_type_name, status_options_by_type['seller'])
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    
    try:
        old_status = transaction.status
        transaction.status = new_status
        
        # Log the status change
        if old_status != new_status:
            audit_service.log_transaction_status_changed(transaction, old_status, new_status)
        
        # Auto-create first seller check-in task when listing goes active
        if (new_status == 'active' and old_status != 'active'
                and tx_type_name in ('seller', 'landlord')):
            try:
                from services.listing_checkin_service import (
                    create_seller_checkin_task, should_auto_create_next,
                )
                if should_auto_create_next(transaction):
                    create_seller_checkin_task(transaction, current_user)
            except Exception as e:
                logger.warning("Auto-checkin creation failed for transaction %s: %s", id, e)
        
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
    transaction = _require_tx(id, CAP_EDIT)

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


@transactions_bp.route('/<int:id>/listing-info-overrides', methods=['POST'])
@login_required
@transactions_required
def update_listing_info_overrides(id):
    """Save manual listing-info overrides for a seller transaction."""
    transaction = _require_tx(id, CAP_EDIT)

    if transaction.transaction_type.name != 'seller':
        return jsonify({'success': False, 'error': 'Listing info edits only available for seller transactions'}), 400

    data = request.get_json(silent=True) or {}
    allowed_fields = {
        'list_price',
        'listing_start_date',
        'listing_end_date',
        'total_commission',
        'listing_side_commission',
        'buyer_commission',
        'protection_period_days',
        'financing_types',
        'has_hoa',
    }
    cleaned = {}
    for field in allowed_fields:
        value = data.get(field)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            cleaned[field] = normalized

    try:
        extra_data = dict(transaction.extra_data or {})
        if cleaned:
            extra_data['listing_info_overrides'] = cleaned
        else:
            extra_data.pop('listing_info_overrides', None)
        transaction.extra_data = extra_data

        documents = TransactionDocument.query.filter_by(transaction_id=transaction.id).all()
        listing_info = build_listing_info(documents, cleaned)

        db.session.commit()
        return jsonify({'success': True, 'listing_info': listing_info, 'overrides': cleaned})
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

    transaction = _require_tx(id, CAP_EDIT)

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
    transaction = _require_tx(id, CAP_VIEW)

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


@transactions_bp.route('/<int:id>/live', methods=['GET'])
@login_required
@transactions_required
def transaction_live(id):
    """Unified live-status payload for the transaction detail page poller."""
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    reports = list_open_reports(tx.id, current_user.organization_id)
    proposals = ProposalService.list_pending_proposals(
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    )
    proposal_document_ids = {
        proposal.source_document_id
        for proposal in proposals
        if proposal.source_document_id is not None
    }
    attention_count = sum(
        1 for report in reports if report.severity in ('attention', 'critical')
    )

    doc_rows = db.session.query(
        TransactionDocument.id,
        TransactionDocument.extraction_status,
        TransactionDocument.template_slug,
    ).filter_by(transaction_id=tx.id).all()

    doc_states = []
    listing_status = None
    pending_count = 0
    processing_count = 0
    for doc_id, extraction_status, template_slug in doc_rows:
        doc_states.append((doc_id, extraction_status))
        if template_slug == 'listing-agreement':
            listing_status = extraction_status
        if extraction_status == 'pending':
            pending_count += 1
        elif extraction_status == 'processing':
            processing_count += 1

    in_flight = any(
        status in ('pending', 'processing') for _, status in doc_states
    )

    offers_pending = (
        db.session.query(db.func.count(TransactionDocument.id))
        .select_from(SellerOfferDocument)
        .join(
            TransactionDocument,
            TransactionDocument.id == SellerOfferDocument.transaction_document_id,
        )
        .filter(
            SellerOfferDocument.transaction_id == tx.id,
            TransactionDocument.extraction_status.in_(('pending', 'processing')),
        )
        .scalar()
    ) or 0

    sorted_reports = sorted(reports, key=lambda r: r.id)
    sorted_proposals = sorted(proposals, key=lambda p: p.id)
    fingerprint = '|'.join([
        ','.join(f'{r.id}:{r.status}:{r.severity}' for r in sorted_reports),
        ','.join(str(p.id) for p in sorted_proposals),
        ','.join(f'{d_id}:{d_status}' for d_id, d_status in sorted(doc_states)),
    ])
    version = hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()

    return jsonify({
        'version': version,
        'in_flight': in_flight,
        'reviews': {
            'reports': [r.to_dict() for r in reports],
            'pending_toasts': [
                r.to_dict() for r in pending_toasts(tx.id, current_user.organization_id)
            ],
            'report_count': len(reports),
            'attention_count': attention_count,
            'html': render_template(
                'transactions/_document_review_reports.html',
                document_review_reports=reports,
                pending_proposal_document_ids=proposal_document_ids,
            ),
        },
        'proposals': {
            'count': len(proposals),
            'document_ids': sorted(proposal_document_ids),
        },
        'extraction': {
            'listing_status': listing_status,
            'pending_count': pending_count,
            'processing_count': processing_count,
        },
        'offers': {
            'pending_extraction_count': int(offers_pending),
        },
    })
