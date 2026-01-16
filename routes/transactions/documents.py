# routes/transactions/documents.py
"""
Document form and filling routes.
"""

from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, Transaction, TransactionDocument
from services import audit_service
from . import transactions_bp
from .decorators import transactions_required
from .helpers import build_prefill_data


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


# =============================================================================
# UPLOAD SCANNED SIGNED DOCUMENT
# =============================================================================

@transactions_bp.route('/<int:id>/documents/<int:doc_id>/upload-scan', methods=['POST'])
@login_required
@transactions_required
def upload_scanned_document(id, doc_id):
    """
    Upload a scanned signed document for physical signature workflow.
    
    Accepts a PDF file upload when an agent has printed, gotten physical
    signatures, and scanned the signed document.
    """
    from datetime import datetime
    from services.supabase_storage import upload_scanned_document as upload_scan
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    # Document must be filled/generated to upload a scan
    if doc.status not in ['filled', 'generated']:
        return jsonify({
            'success': False,
            'error': f'Cannot upload scan for document in "{doc.status}" status. Document must be filled first.'
        }), 400
    
    # Check if file was uploaded
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    
    # Validate file type (PDF only for legal documents)
    allowed_extensions = {'pdf'}
    file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    
    if file_ext not in allowed_extensions:
        return jsonify({
            'success': False,
            'error': 'Only PDF files are allowed for scanned documents'
        }), 400
    
    # Read file data
    file_data = file.read()
    file_size = len(file_data)
    
    # Validate file size (max 25MB for scanned documents)
    max_size = 25 * 1024 * 1024  # 25 MB
    if file_size > max_size:
        return jsonify({
            'success': False,
            'error': 'File too large. Maximum size is 25MB.'
        }), 400
    
    try:
        # Upload to Supabase
        result = upload_scan(
            transaction_id=transaction.id,
            doc_id=doc.id,
            file_data=file_data,
            original_filename=file.filename,
            content_type='application/pdf'
        )
        
        # Update document record
        doc.signed_file_path = result['path']
        doc.signed_file_size = result['size']
        doc.signed_at = datetime.utcnow()
        doc.status = 'signed'
        doc.signing_method = 'physical'
        
        # Log audit event
        audit_service.log_document_signed_physical(
            document=doc,
            file_size=file_size,
            original_filename=file.filename,
            actor_id=current_user.id
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Scanned document uploaded successfully',
            'document': {
                'id': doc.id,
                'status': doc.status,
                'signing_method': doc.signing_method,
                'signed_at': doc.signed_at.isoformat() if doc.signed_at else None,
                'file_size': file_size
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
