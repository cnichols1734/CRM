# routes/transactions/crud.py
"""
Transaction CRUD routes (create, read, update, delete).
"""

import logging
from datetime import datetime as dt
from flask import request, render_template, redirect, url_for, flash, abort

logger = logging.getLogger(__name__)
from flask_login import login_required, current_user
from models import (
    db, Transaction, TransactionType, TransactionParticipant,
    TransactionDocument, DocumentSignature, AuditEvent, Contact, ContactFile, Task,
    SellerListingProfile, SellerOffer, SellerOfferActivity, SellerAcceptedContract,
    SellerContractMilestone, SellerCommissionTerms, SellerListingPriceChange,
    SellerOfferDocument, SellerOfferVersion, SellerContractDocument
)
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import func, case, and_
from services import audit_service
from services.offer_metering import metering_for_transaction
from services.offer_side import (
    labels_for_side,
    side_for_transaction,
    status_label,
    supports_offers,
)
from services.seller_workflow import offer_urgency
from . import transactions_bp
from .decorators import transactions_required


def _merge_extraction_status(current_status, candidate_status):
    """Return the highest-priority extraction state for seller offer surfaces."""
    priority = {
        'failed': 4,
        'processing': 3,
        'pending': 2,
        'complete': 1,
    }
    if not candidate_status:
        return current_status
    if not current_status:
        return candidate_status
    return (
        candidate_status
        if priority.get(candidate_status, 0) > priority.get(current_status, 0)
        else current_status
    )


def _order_offer_package_documents(offer_documents):
    """Order offer documents so AI-split children render under their parent packet."""
    if not offer_documents:
        return offer_documents

    by_doc_id = {}
    parents = []
    children_by_parent = {}
    orphans = []

    for offer_document in offer_documents:
        doc = offer_document.document
        if not doc:
            orphans.append(offer_document)
            continue
        by_doc_id[doc.id] = offer_document
        if doc.parent_document_id:
            children_by_parent.setdefault(doc.parent_document_id, []).append(offer_document)
        else:
            parents.append(offer_document)

    for parent_id, child_list in children_by_parent.items():
        child_list.sort(key=lambda od: (
            od.document.page_start if od.document and od.document.page_start else 0,
            od.document.page_end if od.document and od.document.page_end else 0,
            od.created_at or od.id,
        ))

    ordered = []
    seen_parent_ids = set()
    for offer_document in parents:
        ordered.append(offer_document)
        seen_parent_ids.add(offer_document.document.id)
        for child in children_by_parent.get(offer_document.document.id, []):
            ordered.append(child)

    for parent_id, child_list in children_by_parent.items():
        if parent_id in seen_parent_ids:
            continue
        for child in child_list:
            ordered.append(child)

    ordered.extend(orphans)
    return ordered


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
    
    # Base query - ALWAYS filter by organization, then by creator/assignee unless admin viewing all
    if show_all:
        query = Transaction.query.filter_by(organization_id=current_user.organization_id)
    else:
        from services.transaction_auth import transactions_visible_query
        query = transactions_visible_query(current_user)
    
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
    document_progress = {}
    stage_age = {}
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

        # Document progress metrics per transaction
        doc_rows = db.session.query(
            TransactionDocument.transaction_id.label('transaction_id'),
            func.count(TransactionDocument.id).label('total_docs'),
            func.coalesce(
                func.sum(case((TransactionDocument.status == 'signed', 1), else_=0)),
                0
            ).label('signed_docs'),
            func.coalesce(
                func.sum(
                    case(
                        (and_(
                            TransactionDocument.is_placeholder.is_(True),
                            TransactionDocument.status == 'pending'
                        ), 1),
                        else_=0
                    )
                ),
                0
            ).label('pending_placeholders')
        ).filter(
            TransactionDocument.transaction_id.in_(tx_ids)
        ).group_by(
            TransactionDocument.transaction_id
        ).all()

        for row in doc_rows:
            document_progress[row.transaction_id] = {
                'total': int(row.total_docs or 0),
                'signed': int(row.signed_docs or 0),
                'pending_placeholders': int(row.pending_placeholders or 0),
                'pending_signatures': 0
            }

        pending_signature_rows = db.session.query(
            TransactionDocument.transaction_id.label('transaction_id'),
            func.count(DocumentSignature.id).label('pending_signatures')
        ).join(
            DocumentSignature,
            DocumentSignature.document_id == TransactionDocument.id
        ).filter(
            TransactionDocument.transaction_id.in_(tx_ids),
            TransactionDocument.status != 'voided',
            DocumentSignature.status.notin_(['signed', 'declined'])
        ).group_by(
            TransactionDocument.transaction_id
        ).all()

        for row in pending_signature_rows:
            tx_progress = document_progress.setdefault(row.transaction_id, {
                'total': 0,
                'signed': 0,
                'pending_placeholders': 0,
                'pending_signatures': 0
            })
            tx_progress['pending_signatures'] = int(row.pending_signatures or 0)

        # Stage age: days since the latest status-change audit event for each transaction
        latest_status_change_sq = db.session.query(
            AuditEvent.transaction_id.label('transaction_id'),
            func.max(AuditEvent.created_at).label('status_changed_at')
        ).filter(
            AuditEvent.transaction_id.in_(tx_ids),
            AuditEvent.event_type == AuditEvent.TRANSACTION_STATUS_CHANGED
        ).group_by(
            AuditEvent.transaction_id
        ).subquery()

        stage_started_lookup = {
            row.transaction_id: row.status_changed_at
            for row in db.session.query(
                latest_status_change_sq.c.transaction_id,
                latest_status_change_sq.c.status_changed_at
            ).all()
        }

        now = dt.utcnow()
        for tx in transactions:
            stage_started_at = stage_started_lookup.get(tx.id) or tx.created_at
            age_days = max((now.date() - stage_started_at.date()).days, 0)
            stage_age[tx.id] = {
                'days': age_days,
                'started_at': stage_started_at
            }
    
    # Get transaction types for filter dropdown (org-scoped, cached)
    from services.cache_helpers import get_org_transaction_types
    from feature_flags import org_has_feature
    transaction_types = get_org_transaction_types(current_user.organization_id)
    vtc_pilot = org_has_feature('BOB_VTC_PILOT', current_user.organization)
    
    return render_template(
        'transactions/list.html',
        transactions=transactions,
        transaction_types=transaction_types,
        transaction_contacts=transaction_contacts,
        document_progress=document_progress,
        stage_age=stage_age,
        status_filter=status_filter,
        type_filter=type_filter,
        search_query=search_query,
        show_all=show_all,
        vtc_pilot=vtc_pilot,
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
        
        # Determine default status based on transaction type.
        # Buyer/tenant work starts with search; listing/referral work starts in prep.
        search_first_types = {'buyer', 'tenant'}
        default_status = 'showing' if tx_type and tx_type.name in search_first_types else 'preparing_to_list'
        
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

        if tx_type and tx_type.name == 'seller':
            from services.listing_prep_checklist import seed_listing_prep_checklist
            seed_listing_prep_checklist(
                transaction,
                current_user.organization_id,
                actor_id=current_user.id,
            )

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
    from services.transaction_auth import can_view_transaction

    # Load transaction with transaction_type - SCOPED TO ORGANIZATION
    transaction = Transaction.query.options(
        joinedload(Transaction.transaction_type)
    ).filter_by(id=id, organization_id=current_user.organization_id).first_or_404()

    if not can_view_transaction(transaction, current_user).allowed:
        abort(403)
    
    # Load participants with contacts in one query
    participants = TransactionParticipant.query.options(
        joinedload(TransactionParticipant.contact)
    ).filter_by(transaction_id=id).all()
    
    # Load documents sorted by created_at
    documents = TransactionDocument.query.filter_by(
        transaction_id=id
    ).order_by(TransactionDocument.created_at).all()
    listing_documents = documents
    
    # Get files from all contacts associated with this transaction
    contact_ids = [p.contact_id for p in participants if p.contact_id]
    contact_files = []
    if contact_ids:
        contact_files = ContactFile.query.filter(
            ContactFile.contact_id.in_(contact_ids)
        ).order_by(ContactFile.created_at.desc()).all()
    
    # For seller transactions, extract listing info from the listing agreement document
    listing_info = None
    listing_extraction_status = None
    listing_info_overrides = {}
    seller_listing_profile = None
    listing_doc = next((d for d in documents if d.template_slug == 'listing-agreement'), None)
    has_listing_agreement = listing_doc is not None
    if transaction.transaction_type.name == 'seller':
        offer_scoped_document_ids = {
            row[0] for row in db.session.query(SellerOfferDocument.transaction_document_id).filter(
                SellerOfferDocument.transaction_id == transaction.id,
                SellerOfferDocument.organization_id == current_user.organization_id,
            ).all()
        }
        contract_scoped_document_ids = {
            row[0] for row in db.session.query(SellerContractDocument.transaction_document_id).filter(
                SellerContractDocument.transaction_id == transaction.id,
                SellerContractDocument.organization_id == current_user.organization_id,
            ).all()
        }
        non_listing_document_ids = offer_scoped_document_ids | contract_scoped_document_ids
        listing_documents = [
            doc for doc in documents
            if doc.id not in non_listing_document_ids
        ]

        extra_data = transaction.extra_data or {}
        listing_info_overrides = extra_data.get('listing_info_overrides') or {}
        seller_listing_profile = SellerListingProfile.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id,
        ).first()
        from services.transaction_helpers import build_listing_info
        listing_info = build_listing_info(
            documents,
            listing_info_overrides,
            transaction=transaction,
            listing_profile=seller_listing_profile,
        )
        if listing_doc:
            listing_extraction_status = listing_doc.extraction_status
    
    # Get lockbox combo from extra_data (always available for seller transactions)
    lockbox_combo = None
    seller_offers = []
    active_seller_offers = []
    seller_offer_versions_by_offer = {}
    seller_offer_documents_by_offer = {}
    seller_offer_activities_by_offer = {}
    seller_offer_extraction_status = {}
    urgent_seller_offer = None
    primary_seller_contract = None
    backup_seller_contracts = []
    seller_contract_documents_by_contract = {}
    seller_contract_milestones = []
    seller_commission_terms = None
    seller_price_changes = []
    offer_side = side_for_transaction(transaction)
    offer_labels = labels_for_side(offer_side) if offer_side else None
    offer_metering = None

    # Offer threads attach to buyer and seller transactions (shared SellerOffer tables).
    if supports_offers(transaction):
        seller_offers = SellerOffer.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id
        ).order_by(SellerOffer.received_at.desc()).all()
        seller_offers.sort(key=lambda offer: (
            offer_urgency(offer)['rank'],
            offer.response_deadline_at or dt.max
        ))
        active_seller_offers = [
            offer for offer in seller_offers
            if offer.status in ('new', 'reviewing', 'needs_review', 'countered')
        ]
        offer_ids = [offer.id for offer in seller_offers]
        if offer_ids:
            seller_offer_versions_by_offer = {offer_id: [] for offer_id in offer_ids}
            seller_offer_documents_by_offer = {offer_id: [] for offer_id in offer_ids}
            seller_offer_activities_by_offer = {offer_id: [] for offer_id in offer_ids}
            offer_versions = SellerOfferVersion.query.filter(
                SellerOfferVersion.offer_id.in_(offer_ids),
                SellerOfferVersion.organization_id == current_user.organization_id
            ).order_by(
                SellerOfferVersion.offer_id.asc(),
                SellerOfferVersion.version_number.desc()
            ).all()
            for version in offer_versions:
                seller_offer_versions_by_offer.setdefault(version.offer_id, []).append(version)

            offer_documents = SellerOfferDocument.query.filter(
                SellerOfferDocument.offer_id.in_(offer_ids),
                SellerOfferDocument.organization_id == current_user.organization_id
            ).order_by(
                SellerOfferDocument.offer_id.asc(),
                SellerOfferDocument.created_at.desc()
            ).all()
            for offer_document in offer_documents:
                seller_offer_documents_by_offer.setdefault(offer_document.offer_id, []).append(offer_document)
                document = offer_document.document
                if document and document.extraction_status:
                    seller_offer_extraction_status[offer_document.offer_id] = _merge_extraction_status(
                        seller_offer_extraction_status.get(offer_document.offer_id),
                        document.extraction_status,
                    )

            for offer_id, docs in list(seller_offer_documents_by_offer.items()):
                seller_offer_documents_by_offer[offer_id] = _order_offer_package_documents(docs)

            offer_activities = SellerOfferActivity.query.filter(
                SellerOfferActivity.offer_id.in_(offer_ids),
                SellerOfferActivity.organization_id == current_user.organization_id
            ).order_by(SellerOfferActivity.created_at.desc()).all()
            for activity in offer_activities:
                bucket = seller_offer_activities_by_offer.setdefault(activity.offer_id, [])
                if len(bucket) < 8:
                    bucket.append(activity)

        version_ids = [offer.current_version_id for offer in seller_offers if offer.current_version_id]
        if version_ids:
            current_versions = SellerOfferVersion.query.filter(
                SellerOfferVersion.id.in_(version_ids),
                SellerOfferVersion.organization_id == current_user.organization_id
            ).all()
            for version in current_versions:
                if version.document:
                    seller_offer_extraction_status[version.offer_id] = _merge_extraction_status(
                        seller_offer_extraction_status.get(version.offer_id),
                        version.document.extraction_status,
                    )
        urgent_seller_offer = active_seller_offers[0] if active_seller_offers else None
        offer_metering = metering_for_transaction(
            transaction.id,
            current_user.organization_id,
        )

    # Listing contracts, commission, and price history remain seller-only.
    if transaction.transaction_type.name == 'seller':
        extra_data = transaction.extra_data or {}
        lockbox_combo = extra_data.get('lockbox_combo')
        primary_seller_contract = SellerAcceptedContract.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id,
            position='primary',
            status='active'
        ).first()
        backup_seller_contracts = SellerAcceptedContract.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id,
            position='backup',
            status='active'
        ).order_by(SellerAcceptedContract.backup_position.asc()).all()
        seller_contracts = ([primary_seller_contract] if primary_seller_contract else []) + backup_seller_contracts
        contract_ids = [contract.id for contract in seller_contracts]
        if contract_ids:
            docs_by_contract = {}
            contract_documents = SellerContractDocument.query.filter(
                SellerContractDocument.accepted_contract_id.in_(contract_ids),
                SellerContractDocument.organization_id == current_user.organization_id
            ).order_by(
                SellerContractDocument.accepted_contract_id.asc(),
                SellerContractDocument.created_at.asc()
            ).all()
            for contract_document in contract_documents:
                docs_by_contract.setdefault(contract_document.accepted_contract_id, []).append(contract_document)
            seller_contract_documents_by_contract = {
                contract.id: _order_offer_package_documents(docs_by_contract.get(contract.id, []))
                for contract in seller_contracts
            }
        if primary_seller_contract:
            seller_contract_milestones = SellerContractMilestone.query.filter_by(
                accepted_contract_id=primary_seller_contract.id,
                organization_id=current_user.organization_id
            ).order_by(SellerContractMilestone.due_at.asc()).all()
        seller_commission_terms = SellerCommissionTerms.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id
        ).first()
        seller_price_changes = SellerListingPriceChange.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id
        ).order_by(SellerListingPriceChange.changed_at.desc()).limit(5).all()

    from services.transaction_helpers import (
        build_contract_terms,
        resolve_header_price_display,
    )
    contract_terms = build_contract_terms(
        transaction,
        accepted_contract=primary_seller_contract,
        documents=documents,
    )
    header_price = resolve_header_price_display(
        transaction,
        listing_info=listing_info,
        contract_terms=contract_terms,
    )

    # Get RentCast data for buyer transactions
    rentcast_data = None
    rentcast_fetched_at = None
    if transaction.transaction_type.name == 'buyer':
        rentcast_data = transaction.rentcast_data
        rentcast_fetched_at = transaction.rentcast_fetched_at
    
    # Determine intake schema availability and document workflow mode
    from services.intake_service import get_intake_schema
    intake_schema = get_intake_schema(
        transaction.transaction_type.name,
        transaction.ownership_status
    )
    has_intake_schema = intake_schema is not None
    document_workflow_mode = intake_schema.get('document_workflow', 'docuseal') if intake_schema else None
    
    # Load tasks linked to this transaction (pending/overdue first, then completed)
    transaction_tasks = Task.query.filter_by(
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).order_by(Task.status.asc(), Task.due_date.asc()).all()

    # BOB post-upload document review reports (banner / toast / inbox)
    document_review_reports = []
    document_review_toasts = []
    document_review_attention = []
    pending_change_proposals = []
    pending_proposal_document_ids = set()
    try:
        from services.document_review import list_open_reports, pending_toasts
        document_review_reports = list_open_reports(
            transaction.id, current_user.organization_id,
        )
        document_review_toasts = pending_toasts(
            transaction.id, current_user.organization_id,
        )
        document_review_attention = [
            r for r in document_review_reports
            if r.severity in ('attention', 'critical')
        ]
    except Exception:
        # Table may not exist until migration; never break the transaction page.
        pass

    try:
        from services.proposal_service import ProposalService
        pending_change_proposals = ProposalService.list_pending_proposals(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id,
        )
        pending_proposal_document_ids = {
            proposal.source_document_id
            for proposal in pending_change_proposals
            if proposal.source_document_id is not None
        }
    except Exception:
        pending_change_proposals = []
        pending_proposal_document_ids = set()

    # Phase 1A control tower: requirements + optional milestone backfill for pilot
    control_tower_requirements = []
    control_tower_open_requirements = []
    control_tower_completed_requirements = []
    control_tower_focus = []
    control_tower_summary = {
        'open': 0,
        'completed': 0,
        'overdue': 0,
        'due_soon': 0,
        'missing_documents': 0,
    }
    transaction_assignments = []
    org_assignable_users = []
    can_assign_roles = False
    vtc_pilot = False
    try:
        from feature_flags import org_has_feature
        from models import TransactionAssignment, User
        from services.requirements_service import RequirementsService
        from services.transaction_auth import (
            CAP_ASSIGN,
            has_capability,
            is_org_break_glass,
        )

        vtc_pilot = org_has_feature('BOB_VTC_PILOT', current_user.organization)
        control_tower_requirements = RequirementsService.list_requirements(transaction.id)

        # Derived timing for display only — never backfill or commit on GET.
        # Explicit backfill lives on POST /requirements/backfill.
        for req in control_tower_requirements:
            derived = RequirementsService.derive_timing_state(
                req.due_at, req.work_status, now=dt.utcnow(),
            )
            # Non-column attribute so the ORM row is not dirtied.
            req.display_timing_state = derived

            if req.work_status in ('completed', 'not_applicable', 'superseded'):
                control_tower_completed_requirements.append(req)
                continue

            control_tower_open_requirements.append(req)
            if derived == 'overdue':
                control_tower_summary['overdue'] += 1
            elif derived == 'due_soon':
                control_tower_summary['due_soon'] += 1

        control_tower_summary['completed'] = len(control_tower_completed_requirements)

        transaction_assignments = (
            TransactionAssignment.query.filter_by(
                transaction_id=transaction.id,
                organization_id=current_user.organization_id,
            )
            .order_by(TransactionAssignment.created_at.asc())
            .all()
        )
        can_assign_roles = (
            is_org_break_glass(current_user)
            or has_capability(transaction, CAP_ASSIGN, current_user).allowed
            or transaction.created_by_id == current_user.id
        )
        if can_assign_roles or vtc_pilot:
            org_assignable_users = (
                User.query.filter_by(organization_id=current_user.organization_id)
                .order_by(User.first_name.asc(), User.last_name.asc())
                .limit(100)
                .all()
            )
    except Exception:
        logger.exception('Control tower load failed for tx=%s', transaction.id)

    # One operational queue: BOB document exceptions, overdue work, upcoming
    # deadlines, and one aggregate missing-document action. Avoid repeating the
    # same missing-doc warning in every document review report.
    focus_items = []
    for report in document_review_attention:
        finding = next(iter(report.findings or []), {})
        focus_items.append({
            'kind': 'document_review',
            'rank': 0 if report.severity == 'critical' else 2,
            'sort_at': report.created_at or dt.max,
            'title': finding.get('message') or report.title,
            'subtitle': (
                f'{report.document.review_filename} · '
                f'{report.document.review_document_type}'
                if report.document else 'Uploaded document'
            ),
            'status_label': 'Critical' if report.severity == 'critical' else 'Review',
            'report': report,
        })

    for proposal in pending_change_proposals:
        source_doc = next((doc for doc in documents if doc.id == proposal.source_document_id), None)
        focus_items.append({
            'kind': 'proposal',
            'rank': 3,
            'sort_at': proposal.created_at or dt.max,
            'title': 'Review BOB’s suggested transaction updates',
            'subtitle': source_doc.template_name if source_doc else 'Uploaded document',
            'status_label': 'Approve',
            'proposal': proposal,
        })

    for req in control_tower_open_requirements:
        timing = getattr(req, 'display_timing_state', None) or req.timing_state
        rank = 1 if timing == 'overdue' else 4 if timing == 'due_soon' else 6
        focus_items.append({
            'kind': 'requirement',
            'rank': rank,
            'sort_at': req.due_at or dt.max,
            'title': req.title,
            'subtitle': (req.phase_key or 'transaction work').replace('_', ' ').title(),
            'status_label': (
                'Overdue' if timing == 'overdue' else
                'Due soon' if timing == 'due_soon' else
                'In progress' if req.work_status == 'in_progress' else
                'Pending'
            ),
            'timing': timing,
            'requirement': req,
        })

    missing_documents = [
        doc for doc in documents
        if doc.is_placeholder and doc.status in ('pending', 'draft')
    ]
    control_tower_summary['missing_documents'] = len(missing_documents)
    if missing_documents:
        focus_items.append({
            'kind': 'missing_documents',
            'rank': 5,
            'sort_at': dt.max,
            'title': f'Upload {len(missing_documents)} required document' + ('s' if len(missing_documents) != 1 else ''),
            'subtitle': 'Document checklist',
            'status_label': 'Upload',
            'missing_documents': missing_documents,
        })

    control_tower_focus = sorted(
        focus_items,
        key=lambda item: (item['rank'], item['sort_at']),
    )[:5]
    control_tower_summary['open'] = (
        len(control_tower_open_requirements)
        + len(document_review_attention)
        + len(pending_change_proposals)
        + (1 if missing_documents else 0)
    )

    # Merged checklist: requirements + folded document placeholders as one list.
    checklist = []
    checklist_folded_doc_ids = set()
    listing_prep_groups = []
    listing_description = ''
    listing_description_source = ''
    listing_description_ai_ready = False
    try:
        from services.checklist_service import build_checklist
        from services.listing_prep_checklist import (
            listing_description_source as description_source_for,
            listing_description_text,
            listing_prep_groups as build_listing_prep_groups,
            sync_listing_prep_checklist,
        )
        from services.requirements_service import RequirementsService as _ReqSvc
        from config import Config

        tx_side_name = (transaction.transaction_type.name or '').lower()
        if tx_side_name == 'seller':
            sync_listing_prep_checklist(
                transaction,
                actor_id=current_user.id,
                documents=documents,
            )
            db.session.commit()
            listing_prep_groups = build_listing_prep_groups(transaction)
            listing_description = listing_description_text(seller_listing_profile)
            listing_description_source = description_source_for(seller_listing_profile)
            listing_description_ai_ready = bool(Config.OPENAI_API_KEY)

        checklist = build_checklist(transaction, current_user.organization_id)
        now_utc = dt.utcnow()
        for item in checklist:
            if item.get('kind') == 'requirement':
                item['timing_state'] = _ReqSvc.derive_timing_state(
                    item.get('due_at'), item.get('work_status'), now=now_utc,
                )
            doc_summary = item.get('document')
            if item.get('kind') == 'requirement' and doc_summary and doc_summary.get('id'):
                checklist_folded_doc_ids.add(doc_summary['id'])
    except Exception:
        logger.exception('Checklist load failed for tx=%s', transaction.id)
        checklist = []
        checklist_folded_doc_ids = set()
        listing_prep_groups = []

    # Amendments card: only when an active primary accepted contract exists.
    amendments = None
    active_primary_for_amendments = primary_seller_contract
    if active_primary_for_amendments is None:
        active_primary_for_amendments = SellerAcceptedContract.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id,
            position='primary',
            status='active',
        ).first()
    if active_primary_for_amendments is not None:
        try:
            from services import amendment_service

            amendment_rows = amendment_service.list_for_transaction(
                transaction.id,
                current_user.organization_id,
            )
            amendments = []
            for row in amendment_rows:
                label = (row.amendment_type or 'amendment').replace('_', ' ').replace('-', ' ').strip()
                label = (label[:1].upper() + label[1:]) if label else 'Amendment'
                changed_count = sum(
                    1 for entry in amendment_service.diff_against_contract(row)
                    if entry.get('changed')
                )
                amendments.append({
                    'id': row.id,
                    'label': label,
                    'status': row.status,
                    'created_at': row.created_at,
                    'changed_count': changed_count,
                })
        except Exception:
            logger.exception('Amendments list failed for tx=%s', transaction.id)
            amendments = []

    # Whole-transaction stage from signals already loaded above — no new queries.
    from services.transaction_stage import stage_for_transaction, surface_visibility
    from services.document_package_workspace import build_document_packages

    stage = stage_for_transaction(
        transaction,
        has_listing_agreement=has_listing_agreement,
        open_offers=active_seller_offers,
        primary_contract=primary_seller_contract,
    )
    document_packages = None
    try:
        document_packages = build_document_packages(transaction)
    except Exception:
        logger.exception('document_packages build failed for tx=%s', transaction.id)
        document_packages = None

    # One "what do I do next" pointer. Preparing-to-list checklist first,
    # then questionnaire, then overdue contract dates.
    next_step = None
    try:
        from services.listing_prep_checklist import first_open_listing_prep_item

        tx_side_name = (transaction.transaction_type.name or '').lower()
        open_prep = (
            first_open_listing_prep_item(listing_prep_groups)
            if tx_side_name == 'seller' and transaction.status == 'preparing_to_list'
            else None
        )
        dated_deadlines = [
            item for item in checklist
            if item.get('kind') == 'requirement' and item.get('due_at')
        ]
        has_checklist_target = bool(
            (tx_side_name == 'seller' and transaction.status == 'preparing_to_list' and listing_prep_groups)
            or (tx_side_name == 'seller' and transaction.status != 'preparing_to_list' and dated_deadlines)
            or (tx_side_name != 'seller' and any(item.get('kind') == 'requirement' for item in checklist))
        )

        if open_prep:
            next_step = {
                'title': open_prep['title'],
                'description': 'Next item on the Preparing to List checklist.',
                'cta': 'Open checklist',
                'url': '#transaction-checklist',
            }
        elif (
            tx_side_name == 'seller'
            and has_intake_schema
            and not transaction.intake_data
        ):
            next_step = {
                'title': 'Finish the property questionnaire',
                'description': 'A few questions about HOA, year built, and districts.',
                'cta': 'Start questionnaire',
                'url': url_for('transactions.intake_questionnaire', id=transaction.id),
            }
        elif control_tower_summary.get('overdue') and has_checklist_target:
            overdue = control_tower_summary['overdue']
            next_step = {
                'title': f'{overdue} deadline{"s are" if overdue != 1 else " is"} overdue',
                'description': 'Update the date or mark the item done.',
                'cta': 'Open checklist',
                'url': '#transaction-checklist',
            }
    except Exception:
        logger.exception('Next-step banner failed for tx=%s', transaction.id)
        next_step = None

    listing_source_documents = []
    if transaction.transaction_type.name == 'seller':
        listing_source_documents = [
            doc for doc in documents
            if doc.template_slug == 'listing-agreement'
            and (doc.signed_file_path or doc.source_file_path)
        ]
        listing_source_documents.sort(
            key=lambda d: (
                1 if isinstance(d.field_data, dict) and d.field_data else 0,
                d.created_at or dt.min,
            ),
            reverse=True,
        )

    return render_template(
        'transactions/detail.html',
        transaction=transaction,
        participants=participants,
        documents=documents,
        listing_documents=listing_documents,
        document_packages=document_packages,
        contact_files=contact_files,
        listing_info=listing_info,
        listing_extraction_status=listing_extraction_status,
        listing_info_overrides=listing_info_overrides,
        listing_source_documents=listing_source_documents,
        listing_prep_groups=listing_prep_groups,
        listing_description=listing_description,
        listing_description_source=listing_description_source,
        listing_description_ai_ready=listing_description_ai_ready,
        has_listing_agreement=has_listing_agreement,
        header_price=header_price,
        contract_terms=contract_terms,
        amendments=amendments,
        stage=stage,
        surface_visibility=surface_visibility,
        lockbox_combo=lockbox_combo,
        seller_listing_profile=seller_listing_profile,
        seller_offers=seller_offers,
        active_seller_offers=active_seller_offers,
        seller_offer_versions_by_offer=seller_offer_versions_by_offer,
        seller_offer_documents_by_offer=seller_offer_documents_by_offer,
        seller_offer_activities_by_offer=seller_offer_activities_by_offer,
        seller_offer_extraction_status=seller_offer_extraction_status,
        urgent_seller_offer=urgent_seller_offer,
        offer_side=offer_side,
        offer_labels=offer_labels,
        offer_metering=offer_metering,
        status_label=status_label,
        primary_seller_contract=primary_seller_contract,
        backup_seller_contracts=backup_seller_contracts,
        seller_contract_documents_by_contract=seller_contract_documents_by_contract,
        seller_contract_milestones=seller_contract_milestones,
        seller_commission_terms=seller_commission_terms,
        seller_price_changes=seller_price_changes,
        offer_urgency=offer_urgency,
        rentcast_data=rentcast_data,
        rentcast_fetched_at=rentcast_fetched_at,
        has_intake_schema=has_intake_schema,
        document_workflow_mode=document_workflow_mode,
        transaction_tasks=transaction_tasks,
        document_review_reports=document_review_reports,
        document_review_toasts=document_review_toasts,
        document_review_attention=document_review_attention,
        pending_change_proposals=pending_change_proposals,
        pending_proposal_document_ids=pending_proposal_document_ids,
        control_tower_requirements=control_tower_requirements,
        control_tower_open_requirements=control_tower_open_requirements,
        control_tower_completed_requirements=control_tower_completed_requirements,
        control_tower_focus=control_tower_focus,
        control_tower_summary=control_tower_summary,
        checklist=checklist,
        checklist_folded_doc_ids=checklist_folded_doc_ids,
        next_step=next_step,
        transaction_assignments=transaction_assignments,
        org_assignable_users=org_assignable_users,
        can_assign_roles=can_assign_roles,
        vtc_pilot=vtc_pilot,
        now=dt.utcnow()
    )


@transactions_bp.route('/<int:id>/extraction-status')
@login_required
@transactions_required
def extraction_status(id):
    """Check document extraction status and return listing info if ready."""
    from flask import jsonify
    from services.transaction_helpers import build_contract_terms, build_listing_info

    from services.transaction_auth import CAP_VIEW, get_transaction_for_user

    transaction, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not transaction:
        abort(403 if decision.reason != 'not_found' else 404)

    documents = TransactionDocument.query.filter_by(
        transaction_id=transaction.id
    ).all()

    listing_doc = next((d for d in documents if d.template_slug == 'listing-agreement'), None)

    status = None
    error = None
    if listing_doc:
        status = listing_doc.extraction_status
        error = listing_doc.extraction_error

    listing_info_overrides = (transaction.extra_data or {}).get('listing_info_overrides') or {}
    listing_profile = None
    if transaction.transaction_type.name == 'seller':
        listing_profile = SellerListingProfile.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id,
        ).first()
    listing_info = build_listing_info(
        documents,
        listing_info_overrides,
        transaction=transaction,
        listing_profile=listing_profile,
    ) if transaction.transaction_type.name == 'seller' else None

    accepted_contract = None
    if transaction.transaction_type.name == 'seller':
        accepted_contract = SellerAcceptedContract.query.filter_by(
            transaction_id=transaction.id,
            organization_id=current_user.organization_id,
            position='primary',
            status='active',
        ).first()
    contract_terms = build_contract_terms(
        transaction,
        accepted_contract=accepted_contract,
        documents=documents,
    )

    return jsonify({
        'extraction_status': status,
        'extraction_error': error,
        'ready': status in ('complete', 'failed'),
        'listing_info': listing_info,
        'contract_terms': contract_terms,
        'has_listing_agreement': listing_doc is not None,
    })


@transactions_bp.route('/<int:id>/edit')
@login_required
@transactions_required
def edit_transaction(id):
    """Show edit form for a transaction."""
    from services.transaction_auth import CAP_EDIT, get_transaction_for_user

    transaction, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not transaction:
        abort(403 if decision.reason != 'not_found' else 404)
    
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

        # BOB VTC rows use NOT NULL FKs. ORM nullify-on-delete raises
        # NotNullViolation on Postgres (seller_contract_documents etc.).
        from services.transaction_helpers import purge_transaction_dependent_rows

        purge_transaction_dependent_rows(transaction_id)

        # Remaining ORM cascades: participants, documents/signatures,
        # listing profile, showings, price changes.
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
    from services.transaction_auth import CAP_EDIT, get_transaction_for_user

    transaction, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not transaction:
        abort(403 if decision.reason != 'not_found' else 404)

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
            db.session.rollback()
            flash('Invalid status for this transaction type.', 'error')
            return redirect(url_for('transactions.edit_transaction', id=transaction.id))
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

            # Log other field changes
            if changed_fields:
                audit_service.log_transaction_updated(transaction, changed_fields)

        db.session.commit()
        flash('Transaction updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating transaction: {str(e)}', 'error')
    
    return redirect(url_for('transactions.view_transaction', id=id))
