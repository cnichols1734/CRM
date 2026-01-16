# routes/transactions/signing.py
"""
Document e-signature routes (DocuSeal integration).
"""

from datetime import datetime
from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, Transaction, TransactionDocument, DocumentSignature
from services import audit_service
from . import transactions_bp
from .decorators import transactions_required


# =============================================================================
# PREVIEW ALL DOCUMENTS
# =============================================================================

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
                    # Store submission ID for PDF printing
                    doc_preview['submission_id'] = preview_result.get('id')
                    doc.docuseal_submission_id = preview_result.get('id')
                
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


# =============================================================================
# SEND ALL DOCUMENTS
# =============================================================================

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
        
        # Get valid roles and fields from merged template
        merged_submitters = merged_template.get('submitters', [])
        merged_fields = merged_template.get('fields', [])
        
        # Build set of valid role names from merged template
        valid_roles = {s.get('name') for s in merged_submitters}
        
        # Build mapping: submitter_uuid -> role name
        submitter_uuid_to_role = {s.get('uuid'): s.get('name') for s in merged_submitters}
        
        # Build set of valid field names per role from merged template
        valid_fields_by_role = {}
        for field in merged_fields:
            field_name = field.get('name')
            submitter_uuid = field.get('submitter_uuid')
            role_name = submitter_uuid_to_role.get(submitter_uuid)
            if role_name and field_name:
                if role_name not in valid_fields_by_role:
                    valid_fields_by_role[role_name] = set()
                valid_fields_by_role[role_name].add(field_name)
        
        # Step 3: Build submitters for each unique role
        agent_email = listing_agent.display_email if listing_agent else current_user.email
        agent_name = listing_agent.display_name if listing_agent else f"{current_user.first_name} {current_user.last_name}"
        
        # Normalize Agent/Broker - merged templates may consolidate roles
        # Check what roles actually exist in the merged template
        has_agent = 'Agent' in valid_roles
        has_broker = 'Broker' in valid_roles
        
        # Merge Broker fields into Agent if only Agent exists in merged template
        if 'Broker' in unique_docuseal_roles and has_agent and not has_broker:
            broker_fields = fields_by_docuseal_role.get('Broker', [])
            agent_fields = fields_by_docuseal_role.get('Agent', [])
            fields_by_docuseal_role['Agent'] = agent_fields + broker_fields
            unique_docuseal_roles.discard('Broker')
            # Also merge auto_complete status
            if 'Broker' in auto_complete_roles:
                auto_complete_roles.add('Agent')
                auto_complete_roles.discard('Broker')
        elif 'Broker' in unique_docuseal_roles and not has_broker:
            # Broker doesn't exist in merged template, try Agent
            if has_agent:
                fields_by_docuseal_role['Agent'] = fields_by_docuseal_role.get('Broker', [])
                unique_docuseal_roles.add('Agent')
            unique_docuseal_roles.discard('Broker')
            if 'Broker' in auto_complete_roles:
                auto_complete_roles.add('Agent')
                auto_complete_roles.discard('Broker')
        
        # Filter fields to only those that exist in the merged template
        for role_name in list(fields_by_docuseal_role.keys()):
            valid_field_names = valid_fields_by_role.get(role_name, set())
            if valid_field_names:
                fields_by_docuseal_role[role_name] = [
                    f for f in fields_by_docuseal_role[role_name]
                    if f.get('name') in valid_field_names
                ]
        
        # Only use roles that exist in the merged template
        unique_docuseal_roles = unique_docuseal_roles & valid_roles
        
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
            elif docuseal_role == 'Agent':
                # Agent (and normalized Broker) uses the listing agent/current user
                email = agent_email
                name = agent_name
                participant_by_role['Agent'] = listing_agent
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
