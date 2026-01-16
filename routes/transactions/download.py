# routes/transactions/download.py
"""
Document download and print routes.
"""

from datetime import datetime
from flask import jsonify
from flask_login import login_required, current_user
from models import db, Transaction, TransactionDocument
from . import transactions_bp
from .decorators import transactions_required


# =============================================================================
# DOCUMENT DOWNLOAD
# =============================================================================

@transactions_bp.route('/<int:id>/documents/<int:doc_id>/download')
@login_required
@transactions_required
def download_signed_document(id, doc_id):
    """
    Get the download URL for a signed document.
    
    Prefers locally stored copy in Supabase, falls back to DocuSeal API.
    """
    from services.docuseal_service import get_signed_document_urls, DOCUSEAL_MOCK_MODE
    from services.supabase_storage import get_transaction_document_url
    
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
        # Prefer locally stored copy if available
        if doc.signed_file_path:
            signed_url = get_transaction_document_url(doc.signed_file_path, expires_in=3600)
            return jsonify({
                'success': True,
                'documents': [{
                    'name': f'{doc.template_name}_signed.pdf',
                    'url': signed_url
                }],
                'source': 'local',
                'file_size': doc.signed_file_size,
                'mock_mode': False
            })
        
        # Fall back to DocuSeal API
        documents = get_signed_document_urls(doc.docuseal_submission_id)
        
        return jsonify({
            'success': True,
            'documents': documents,
            'source': 'docuseal',
            'mock_mode': DOCUSEAL_MOCK_MODE
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/view-signed')
@login_required
@transactions_required
def view_stored_signed_document(id, doc_id):
    """
    Get a signed URL for viewing the locally stored signed document.
    
    Returns a direct URL for embedding/viewing in browser.
    """
    from services.supabase_storage import get_transaction_document_url, format_file_size
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    if not doc.signed_file_path:
        return jsonify({
            'success': False,
            'error': 'No local copy available. Use download endpoint to fetch from DocuSeal.'
        }), 404
    
    try:
        # Generate signed URL valid for 1 hour
        signed_url = get_transaction_document_url(doc.signed_file_path, expires_in=3600)
        
        return jsonify({
            'success': True,
            'url': signed_url,
            'filename': f'{doc.template_name}_signed.pdf',
            'file_size': doc.signed_file_size,
            'file_size_formatted': format_file_size(doc.signed_file_size) if doc.signed_file_size else None,
            'downloaded_at': doc.signed_file_downloaded_at.isoformat() if doc.signed_file_downloaded_at else None
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# PRINT PDF
# =============================================================================

@transactions_bp.route('/<int:id>/documents/print-all-pdf')
@login_required
@transactions_required
def get_all_documents_print_pdf(id):
    """
    Get a SINGLE combined PDF URL for ALL filled documents in a transaction.
    
    This merges all templates into one, creates a preview submission,
    and returns the combined PDF URL for printing.
    """
    from services.documents import (
        DocumentLoader, FieldResolver, RoleBuilder, DocuSealClient
    )
    from services.documents.types import Submitter
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    # Check if in mock mode
    if DocuSealClient.is_mock_mode():
        return jsonify({
            'success': False,
            'error': 'PDF printing not available in test mode. Deploy to production to test.'
        }), 400
    
    # Get all document slugs from new system
    all_definitions = DocumentLoader.get_sorted()
    all_valid_slugs = [d.slug for d in all_definitions]
    
    documents = transaction.documents.filter(
        TransactionDocument.template_slug.in_(all_valid_slugs),
        TransactionDocument.status.in_(['filled', 'draft', 'generated'])
    ).order_by(TransactionDocument.created_at).all()
    
    if not documents:
        return jsonify({
            'success': False,
            'error': 'No filled documents found to print.'
        }), 404
    
    try:
        # Step 1: Collect all template IDs and resolve fields for each document
        template_ids = []
        unique_docuseal_roles = set()
        fields_by_docuseal_role = {}
        
        for doc in documents:
            definition = DocumentLoader.get(doc.template_slug)
            if not definition or not definition.docuseal_template_id:
                continue
            
            template_ids.append(definition.docuseal_template_id)
            
            # Collect unique roles
            for role_def in definition.roles:
                unique_docuseal_roles.add(role_def.docuseal_role)
            
            # Build context for field resolution
            context = {
                'user': current_user,
                'transaction': transaction,
                'form': doc.field_data or {}
            }
            
            # Resolve fields
            resolved_fields = FieldResolver.resolve(definition, context)
            
            # Group fields by docuseal_role
            for field in resolved_fields:
                if field.is_manual or field.value is None:
                    continue
                
                role_def = definition.get_role(field.role_key)
                if role_def:
                    docuseal_role = role_def.docuseal_role
                    if docuseal_role not in fields_by_docuseal_role:
                        fields_by_docuseal_role[docuseal_role] = []
                    
                    docuseal_field = {'name': field.docuseal_field, 'default_value': str(field.value)}
                    fields_by_docuseal_role[docuseal_role].append(docuseal_field)
        
        if not template_ids:
            return jsonify({
                'success': False,
                'error': 'No valid templates found for the documents.'
            }), 404
        
        # Step 2: Merge templates into one combined template
        merged_template = DocuSealClient.merge_templates(
            template_ids=template_ids,
            name=f"Print Package - {transaction.street_address} - TX{transaction.id}",
            roles=None,  # Let DocuSeal preserve original roles
            external_id=f"tx-{transaction.id}-print-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        )
        
        merged_template_id = merged_template.get('id')
        
        # Step 3: Build submitters for preview (use placeholder emails for all roles)
        participants = transaction.participants.all()
        seller = next((p for p in participants if p.role == 'seller' and p.is_primary), None)
        co_seller = next((p for p in participants if p.role == 'co_seller'), None)
        listing_agent = next((p for p in participants if p.role == 'listing_agent'), None)
        
        agent_email = listing_agent.display_email if listing_agent else current_user.email
        agent_name = listing_agent.display_name if listing_agent else f"{current_user.first_name} {current_user.last_name}"
        
        submitters = []
        for docuseal_role in unique_docuseal_roles:
            if docuseal_role == 'Seller':
                email = seller.display_email if seller else f"seller-preview@preview.local"
                name = seller.display_name if seller else "Seller"
            elif docuseal_role == 'Seller 2':
                if not co_seller or not co_seller.display_email:
                    continue
                email = co_seller.display_email
                name = co_seller.display_name
            elif docuseal_role in ['Agent', 'Broker']:
                email = agent_email
                name = agent_name
            else:
                continue
            
            submitters.append(Submitter(
                role=docuseal_role,
                email=email,
                name=name,
                fields=fields_by_docuseal_role.get(docuseal_role, [])
            ))
        
        if not submitters:
            return jsonify({
                'success': False,
                'error': 'No valid submitters could be created.'
            }), 400
        
        # Step 4: Create preview submission (no email sent)
        result = DocuSealClient.create_submission(
            merged_template_id,
            submitters,
            send_email=False
        )
        
        submission_id = result.get('id')
        
        if not submission_id:
            return jsonify({
                'success': False,
                'error': 'Failed to create combined document submission.'
            }), 500
        
        # Step 5: Get the combined PDF (merge=True merges all docs into single PDF)
        pdf_documents = DocuSealClient.get_submission_documents(submission_id, merge=True)
        
        if not pdf_documents:
            return jsonify({
                'success': False,
                'error': 'No PDF generated from combined documents.'
            }), 404
        
        # Handle dict vs list response
        if isinstance(pdf_documents, list):
            pdf_doc = pdf_documents[0]
        elif isinstance(pdf_documents, dict):
            if 'documents' in pdf_documents:
                doc_list = pdf_documents['documents']
                pdf_doc = doc_list[0] if doc_list else None
            else:
                pdf_doc = next(iter(pdf_documents.values()), None)
        else:
            pdf_doc = None
        
        if not pdf_doc or not pdf_doc.get('url'):
            return jsonify({
                'success': False,
                'error': 'Could not extract PDF URL from response.'
            }), 404
        
        return jsonify({
            'success': True,
            'url': pdf_doc.get('url'),
            'filename': f'{transaction.street_address.replace(" ", "_")}_All_Documents.pdf',
            'document_count': len(documents),
            'page_count': 'combined'
        })
        
    except Exception as e:
        print(f"Error creating combined print PDF: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/print-pdf')
@login_required
@transactions_required
def get_document_print_pdf(id, doc_id):
    """
    Get the PDF URL for a filled document for printing.
    
    This fetches the PDF from DocuSeal using the submission ID.
    The PDF includes all the filled field values.
    
    Works for documents in 'filled', 'generated', or 'sent' status.
    """
    from services.documents.docuseal_client import DocuSealClient
    
    transaction = Transaction.query.get_or_404(id)
    
    if transaction.created_by_id != current_user.id and current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    doc = TransactionDocument.query.get_or_404(doc_id)
    
    if doc.transaction_id != transaction.id:
        return jsonify({'success': False, 'error': 'Document not found'}), 404
    
    # Check if document has been filled
    if doc.status == 'pending':
        return jsonify({
            'success': False,
            'error': 'Document has not been filled yet. Please fill the form first.'
        }), 400
    
    # Need submission ID to fetch PDF
    if not doc.docuseal_submission_id:
        return jsonify({
            'success': False,
            'error': 'No DocuSeal submission found. Please preview the document first.'
        }), 400
    
    try:
        # Check if in mock mode
        if DocuSealClient.is_mock_mode():
            return jsonify({
                'success': False,
                'error': 'PDF printing not available in test mode. Deploy to production to test.'
            }), 400
        
        # Get documents from DocuSeal submission
        submission_id = doc.docuseal_submission_id
        documents = DocuSealClient.get_submission_documents(int(submission_id))
        
        if not documents:
            return jsonify({
                'success': False,
                'error': 'No PDF documents found in submission.'
            }), 404
        
        # DocuSeal may return a list or a dict - handle both formats
        if isinstance(documents, list):
            pdf_doc = documents[0]
        elif isinstance(documents, dict):
            # If it's a dict, get the first value or look for specific keys
            if 'documents' in documents:
                # Nested format: {'documents': [...]}
                doc_list = documents['documents']
                pdf_doc = doc_list[0] if doc_list else None
            else:
                # Direct dict format - get first value
                pdf_doc = next(iter(documents.values()), None)
        else:
            pdf_doc = None
        
        if not pdf_doc:
            return jsonify({
                'success': False,
                'error': 'Could not extract PDF document from response.'
            }), 404
        
        # Handle different response formats for url/name
        pdf_url = pdf_doc.get('url') if isinstance(pdf_doc, dict) else None
        pdf_name = pdf_doc.get('name', f'{doc.template_name}.pdf') if isinstance(pdf_doc, dict) else f'{doc.template_name}.pdf'
        
        if not pdf_url:
            return jsonify({
                'success': False,
                'error': f'No URL found in PDF document. Response: {pdf_doc}'
            }), 404
        
        return jsonify({
            'success': True,
            'url': pdf_url,
            'filename': pdf_name,
            'document_count': len(documents) if isinstance(documents, list) else 1
        })
        
    except Exception as e:
        print(f"Error getting print PDF for doc {doc_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
