# routes/transactions/documents.py
"""
Document form and filling routes.
"""

from flask import request, jsonify, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, Transaction, TransactionDocument, DocumentSignature
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
            organization_id=current_user.organization_id,
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


# =============================================================================
# UPLOAD EXTERNAL DOCUMENT (AD-HOC SIGNING)
# =============================================================================

@transactions_bp.route('/<int:id>/documents/upload-external', methods=['POST'])
@login_required
@transactions_required
def upload_external_document(id):
    """
    Upload an external document for ad-hoc signing.
    
    This is used when an agent receives a document from another party
    (e.g., buyer's agent) that needs signatures from our client.
    
    Creates a new TransactionDocument with document_source='external'.
    The agent will then use the field editor to place signature fields.
    """
    from datetime import datetime
    from services.supabase_storage import upload_external_document as upload_external
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Check if file was uploaded
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    
    # Get document name from form
    document_name = request.form.get('document_name', '').strip()
    if not document_name:
        # Use filename without extension as default name
        document_name = file.filename.rsplit('.', 1)[0] if '.' in file.filename else file.filename
    
    # Validate file type (PDF only)
    allowed_extensions = {'pdf'}
    file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    
    if file_ext not in allowed_extensions:
        return jsonify({
            'success': False,
            'error': 'Only PDF files are allowed'
        }), 400
    
    # Read file data
    file_data = file.read()
    file_size = len(file_data)
    
    # Validate file size (max 25MB)
    max_size = 25 * 1024 * 1024
    if file_size > max_size:
        return jsonify({
            'success': False,
            'error': 'File too large. Maximum size is 25MB.'
        }), 400
    
    try:
        # Upload to Supabase
        result = upload_external(
            transaction_id=transaction.id,
            file_data=file_data,
            original_filename=file.filename,
            content_type='application/pdf'
        )
        
        # Create TransactionDocument record
        doc = TransactionDocument(
            organization_id=current_user.organization_id,
            transaction_id=transaction.id,
            template_slug='external',  # Special slug for external docs
            template_name=document_name,
            status='pending',  # Will become 'ready' after fields are placed
            document_source='external',
            source_file_path=result['path'],
            field_placements=[]  # Will be populated in field editor
        )
        
        db.session.add(doc)
        db.session.commit()
        
        # Log audit event
        audit_service.log_event(
            event_type='document_uploaded_external',
            transaction_id=transaction.id,
            document_id=doc.id,
            description=f"External document uploaded: {document_name}",
            event_data={
                'document_name': document_name,
                'original_filename': file.filename,
                'file_size': file_size,
                'storage_path': result['path']
            },
            source='app',
            actor_id=current_user.id
        )
        
        return jsonify({
            'success': True,
            'message': 'External document uploaded successfully',
            'document': {
                'id': doc.id,
                'name': doc.template_name,
                'status': doc.status,
                'source': doc.document_source
            },
            'redirect_url': url_for('transactions.document_field_editor', id=transaction.id, doc_id=doc.id)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/editor')
@login_required
@transactions_required
def document_field_editor(id, doc_id):
    """
    Visual field editor for placing signature fields on a document.
    
    Used for external documents and hybrid wet+esign flows.
    """
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        flash('Document not found.', 'error')
        return redirect(url_for('transactions.view_transaction', id=id))
    
    # Get participants for the signer dropdown
    participants = transaction.participants.all()
    
    # Get the PDF URL for display
    pdf_url = None
    if doc.source_file_path:
        from services.supabase_storage import get_transaction_document_url
        pdf_url = get_transaction_document_url(doc.source_file_path, expires_in=3600)
    elif doc.signed_file_path:
        # For hybrid flow - editing a wet-signed doc
        from services.supabase_storage import get_transaction_document_url
        pdf_url = get_transaction_document_url(doc.signed_file_path, expires_in=3600)
    
    return render_template(
        'transactions/document_field_editor.html',
        transaction=transaction,
        document=doc,
        participants=participants,
        pdf_url=pdf_url,
        field_placements=doc.field_placements or []
    )


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/field-placements', methods=['POST'])
@login_required
@transactions_required
def save_field_placements(id, doc_id):
    """
    Save field placements for a document.
    
    Called from the visual field editor when the user saves their work.
    """
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    try:
        data = request.get_json()
        field_placements = data.get('field_placements', [])
        page_dimensions = data.get('page_dimensions', {})
        render_scale = data.get('render_scale', 1.5)
        
        # Store field placements along with page dimensions and scale
        # These are needed to convert pixel coords to normalized coords for DocuSeal
        doc.field_placements = {
            'fields': field_placements,
            'page_dimensions': page_dimensions,
            'render_scale': render_scale
        }
        
        # Update status if fields are placed
        if field_placements and doc.status == 'pending':
            doc.status = 'filled'  # Ready for signature
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Field placements saved',
            'field_count': len(field_placements)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/send-adhoc', methods=['POST'])
@login_required
@transactions_required
def send_adhoc_document(id, doc_id):
    """
    Send an external/hybrid document for signature via DocuSeal.
    
    Uses the /submissions/pdf endpoint to send an arbitrary PDF with
    custom field placements.
    """
    from datetime import datetime
    from services.supabase_storage import get_document_as_base64
    from services.documents.docuseal_client import DocuSealClient
    from services.documents.types import Submitter
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    # Extract field placements data
    # Handle both old format (list) and new format (dict with fields, page_dimensions, render_scale)
    placements_data = doc.field_placements or {}
    if isinstance(placements_data, list):
        # Old format: direct list of fields
        field_placements = placements_data
        page_dimensions = {}
        render_scale = 1.5
    else:
        # New format: dict with metadata
        field_placements = placements_data.get('fields', [])
        page_dimensions = placements_data.get('page_dimensions', {})
        render_scale = placements_data.get('render_scale', 1.5)
    
    # Must have field placements
    if not field_placements or len(field_placements) == 0:
        return jsonify({
            'success': False,
            'error': 'No signature fields placed. Please add at least one field.'
        }), 400
    
    # Check for actionable fields
    actionable_types = ['signature', 'initials', 'date', 'auto_date']
    has_actionable = any(f.get('type') in actionable_types for f in field_placements)
    
    if not has_actionable:
        return jsonify({
            'success': False,
            'error': 'Please add at least one signature, initials, or date field.'
        }), 400
    
    try:
        # Get PDF content as base64
        pdf_path = doc.source_file_path or doc.signed_file_path
        if not pdf_path:
            return jsonify({'success': False, 'error': 'PDF file not found'}), 404
        
        pdf_base64 = get_document_as_base64(pdf_path)
        
        # Group field placements by role
        roles_in_doc = set(f.get('role') for f in field_placements)
        
        # Get participants for those roles
        participants = {p.role: p for p in transaction.participants.all()}
        
        # Build submitters list
        submitters = []
        missing_emails = []
        for role in roles_in_doc:
            if role in participants:
                p = participants[role]
                # Use display_email to get email from linked contact/user or direct field
                email = p.display_email
                if not email:
                    missing_emails.append(f"{p.display_name} ({role.replace('_', ' ').title()})")
                    continue
                submitters.append(Submitter(
                    role=role.replace('_', ' ').title(),  # Convert to display format
                    email=email,
                    name=p.display_name,
                    fields=[]  # Fields are specified in documents array for ad-hoc submissions
                ))
        
        if missing_emails:
            return jsonify({
                'success': False,
                'error': f"Missing email address for: {', '.join(missing_emails)}. Please add email addresses to these participants before sending."
            }), 400
        
        if not submitters:
            return jsonify({
                'success': False,
                'error': 'No valid signers found for the placed fields.'
            }), 400
        
        # Build fields for DocuSeal API
        # DocuSeal expects normalized coordinates (0.0 to 1.0) as fractions of page size
        fields = []
        for placement in field_placements:
            field_name = f"{placement.get('type')}_{placement.get('role')}_{placement.get('id')}"
            
            # Map our field types to DocuSeal types
            docuseal_type = placement.get('type')
            if docuseal_type == 'auto_date':
                docuseal_type = 'date'
            elif docuseal_type == 'full_name':
                docuseal_type = 'text'
            elif docuseal_type == 'email':
                docuseal_type = 'text'
            
            # Get page dimensions for normalization
            page_num = placement.get('page', 1)
            page_key = str(page_num)  # JSON keys are strings
            page_dims = page_dimensions.get(page_key, {})
            
            # Convert from rendered pixels to normalized coordinates (0.0 to 1.0)
            # Our coordinates are in rendered pixels (at render_scale)
            # Native PDF dimensions are stored in page_dimensions
            if page_dims:
                # Native PDF page dimensions (at scale 1.0)
                page_width = page_dims.get('width', 612)  # Default letter width in points
                page_height = page_dims.get('height', 792)  # Default letter height in points
                
                # Our rendered coordinates are at render_scale, convert to native scale first
                native_x = placement.get('x', 0) / render_scale
                native_y = placement.get('y', 0) / render_scale
                native_w = placement.get('w', 100) / render_scale
                native_h = placement.get('h', 30) / render_scale
                
                # Now normalize to 0.0-1.0 fractions
                norm_x = native_x / page_width
                norm_y = native_y / page_height
                norm_w = native_w / page_width
                norm_h = native_h / page_height
            else:
                # Fallback: assume standard letter size (612x792 points) at scale 1.5
                page_width = 612
                page_height = 792
                native_x = placement.get('x', 0) / render_scale
                native_y = placement.get('y', 0) / render_scale
                native_w = placement.get('w', 100) / render_scale
                native_h = placement.get('h', 30) / render_scale
                norm_x = native_x / page_width
                norm_y = native_y / page_height
                norm_w = native_w / page_width
                norm_h = native_h / page_height
            
            fields.append({
                'name': field_name,
                'type': docuseal_type,
                'role': placement.get('role', '').replace('_', ' ').title(),
                'required': placement.get('required', True),
                'areas': [{
                    'x': round(norm_x, 4),
                    'y': round(norm_y, 4),
                    'w': round(norm_w, 4),
                    'h': round(norm_h, 4),
                    'page': page_num
                }]
            })
        
        # Create submission via DocuSeal
        result = DocuSealClient.create_submission_from_pdf(
            pdf_base64=pdf_base64,
            document_name=doc.template_name,
            fields=fields,
            submitters=submitters,
            send_email=True
        )
        
        # Update document record
        doc.docuseal_submission_id = str(result.get('id'))
        doc.status = 'sent'
        doc.sent_at = datetime.utcnow()
        doc.sent_by_id = current_user.id
        
        # Create signature records for tracking
        # Match submitters we sent with the response to get slugs
        submitter_results = result.get('submitters', [])
        for i, submitter in enumerate(submitters):
            # Get the corresponding result (by index or by matching role)
            sub_result = submitter_results[i] if i < len(submitter_results) else {}
            
            sig = DocumentSignature(
                organization_id=current_user.organization_id,
                document_id=doc.id,
                signer_email=submitter.email,  # Use our submitter data, not DocuSeal response
                signer_name=submitter.name,
                signer_role=submitter.role,
                status='sent',
                sent_at=datetime.utcnow(),
                docuseal_submitter_slug=sub_result.get('slug')
            )
            
            # Try to link to participant
            for p in transaction.participants.all():
                p_email = p.display_email
                if p_email and p_email.lower() == submitter.email.lower():
                    sig.participant_id = p.id
                    break
            
            db.session.add(sig)
        
        # Log audit event
        audit_service.log_event(
            event_type='document_sent_adhoc',
            transaction_id=transaction.id,
            document_id=doc.id,
            description=f"Ad-hoc document sent for signature: {doc.template_name}",
            event_data={
                'document_source': doc.document_source,
                'submission_id': result.get('id'),
                'signers': [s.email for s in submitters],
                'field_count': len(fields)
            },
            source='app',
            actor_id=current_user.id
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Document sent for signature',
            'submission_id': result.get('id')
        })
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/convert-hybrid', methods=['POST'])
@login_required
@transactions_required
def convert_to_hybrid(id, doc_id):
    """
    Convert a wet-signed document to hybrid mode for additional e-signatures.
    
    This allows a document that was printed, wet-signed by one party, and scanned
    to be sent for e-signature to remaining parties.
    """
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    # Document must be wet-signed with a stored file
    if doc.signing_method != 'physical':
        return jsonify({
            'success': False,
            'error': 'Only wet-signed documents can be converted for additional e-signatures.'
        }), 400
    
    if not doc.signed_file_path:
        return jsonify({
            'success': False,
            'error': 'No scanned document found. Please upload the wet-signed scan first.'
        }), 400
    
    try:
        # Convert to hybrid mode
        doc.document_source = 'hybrid'
        # Use the signed file as the source for the field editor
        doc.source_file_path = doc.signed_file_path
        # Reset field placements for the new signers
        doc.field_placements = None
        # Keep status as signed but will change to sent when e-signatures are requested
        
        # Log audit event
        audit_service.log_event(
            event_type='document_converted_hybrid',
            transaction_id=transaction.id,
            document_id=doc.id,
            description=f"Wet-signed document converted for additional e-signatures: {doc.template_name}",
            event_data={
                'original_signing_method': doc.signing_method,
                'source_file_path': doc.source_file_path
            },
            source='app',
            actor_id=current_user.id
        )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Document converted to hybrid mode',
            'redirect_url': url_for('transactions.document_field_editor', id=transaction.id, doc_id=doc.id)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
