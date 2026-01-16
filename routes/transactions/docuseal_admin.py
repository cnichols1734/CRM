# routes/transactions/docuseal_admin.py
"""
DocuSeal admin, webhook, and debug endpoints.
"""

from datetime import datetime
from flask import request, jsonify
from flask_login import login_required
from models import db, Transaction, TransactionDocument, DocumentSignature
from services import audit_service
from . import transactions_bp
from .decorators import transactions_required
from .helpers import download_and_store_signed_document


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

            # Download and store the signed document in Supabase
            documents_list = payload.get('data', {}).get('documents', [])
            if documents_list:
                try:
                    download_and_store_signed_document(doc, documents_list)
                except Exception as e:
                    # Log error but don't fail the webhook - status still updated
                    import logging
                    logging.getLogger(__name__).error(
                        f"Failed to store signed document for doc {doc.id}: {e}"
                    )

            # Log document signed event
            audit_service.log_document_signed(doc, signature, {
                'signer_email': signer_email,
                'signer_role': signer_role,
                'submission_id': submission_id,
                'signed_file_stored': bool(doc.signed_file_path)
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
