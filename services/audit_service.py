"""
Audit Service - Centralized audit trail logging for transactions and documents.

Provides helper functions to log audit events consistently throughout the application.
All significant actions in the document generation and e-signature flow are tracked.
"""

from flask import request
from flask_login import current_user
from models import db, AuditEvent


def get_request_context():
    """
    Extract IP address and user agent from the current request.
    Returns (ip_address, user_agent) tuple.
    """
    ip_address = None
    user_agent = None

    try:
        if request:
            # Get IP, handling proxies
            ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
            if ip_address and ',' in ip_address:
                ip_address = ip_address.split(',')[0].strip()

            user_agent = request.headers.get('User-Agent', '')[:500]  # Truncate if too long
    except RuntimeError:
        # Outside of request context
        pass

    return ip_address, user_agent


def get_current_actor_id():
    """Get the current user's ID if authenticated."""
    try:
        if current_user and current_user.is_authenticated:
            return current_user.id
    except RuntimeError:
        pass
    return None


def log_event(event_type, transaction_id=None, document_id=None, signature_id=None,
              description=None, event_data=None, source='app', actor_id=None):
    """
    Log an audit event with automatic context extraction.

    Args:
        event_type: One of the AuditEvent type constants
        transaction_id: ID of the related transaction
        document_id: ID of the related document (optional)
        signature_id: ID of the related signature (optional)
        description: Human-readable description of the event
        metadata: Dict of additional context data
        source: Source of the event ('app', 'webhook', 'system', 'api')
        actor_id: Override for the actor (defaults to current user)

    Returns:
        The created AuditEvent instance
    """
    ip_address, user_agent = get_request_context()

    if actor_id is None:
        actor_id = get_current_actor_id()

    event = AuditEvent.log(
        event_type=event_type,
        transaction_id=transaction_id,
        document_id=document_id,
        signature_id=signature_id,
        actor_id=actor_id,
        description=description,
        event_data=event_data or {},
        source=source,
        ip_address=ip_address,
        user_agent=user_agent
    )

    return event


# =============================================================================
# TRANSACTION EVENTS
# =============================================================================

def log_transaction_created(transaction, actor_id=None):
    """Log when a transaction is created."""
    return log_event(
        event_type=AuditEvent.TRANSACTION_CREATED,
        transaction_id=transaction.id,
        description=f"Transaction created for {transaction.street_address}",
        event_data={
            'transaction_type': transaction.transaction_type.name if transaction.transaction_type else None,
            'address': transaction.full_address,
            'status': transaction.status
        },
        actor_id=actor_id
    )


def log_transaction_updated(transaction, changed_fields, actor_id=None):
    """Log when a transaction is updated."""
    return log_event(
        event_type=AuditEvent.TRANSACTION_UPDATED,
        transaction_id=transaction.id,
        description=f"Transaction updated",
        event_data={
            'changed_fields': changed_fields
        },
        actor_id=actor_id
    )


def log_transaction_status_changed(transaction, old_status, new_status, actor_id=None):
    """Log when a transaction status changes."""
    return log_event(
        event_type=AuditEvent.TRANSACTION_STATUS_CHANGED,
        transaction_id=transaction.id,
        description=f"Status changed from '{old_status}' to '{new_status}'",
        event_data={
            'old_status': old_status,
            'new_status': new_status
        },
        actor_id=actor_id
    )


def log_transaction_deleted(transaction_id, address, actor_id=None):
    """Log when a transaction is deleted."""
    return log_event(
        event_type=AuditEvent.TRANSACTION_DELETED,
        transaction_id=transaction_id,
        description=f"Transaction deleted: {address}",
        event_data={
            'address': address
        },
        actor_id=actor_id
    )


# =============================================================================
# PARTICIPANT EVENTS
# =============================================================================

def log_participant_added(transaction, participant, actor_id=None):
    """Log when a participant is added to a transaction."""
    return log_event(
        event_type=AuditEvent.PARTICIPANT_ADDED,
        transaction_id=transaction.id,
        description=f"Added {participant.role}: {participant.display_name}",
        event_data={
            'participant_id': participant.id,
            'role': participant.role,
            'name': participant.display_name,
            'email': participant.display_email,
            'contact_id': participant.contact_id,
            'user_id': participant.user_id
        },
        actor_id=actor_id
    )


def log_participant_removed(transaction, participant, actor_id=None):
    """Log when a participant is removed from a transaction."""
    return log_event(
        event_type=AuditEvent.PARTICIPANT_REMOVED,
        transaction_id=transaction.id,
        description=f"Removed {participant.role}: {participant.display_name}",
        event_data={
            'participant_id': participant.id,
            'role': participant.role,
            'name': participant.display_name,
            'email': participant.display_email
        },
        actor_id=actor_id
    )


# =============================================================================
# DOCUMENT LIFECYCLE EVENTS
# =============================================================================

def log_document_added(document, reason=None, actor_id=None):
    """Log when a document is added to a transaction."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_ADDED,
        transaction_id=document.transaction_id,
        document_id=document.id,
        description=f"Document added: {document.template_name}",
        event_data={
            'template_slug': document.template_slug,
            'template_name': document.template_name,
            'included_reason': reason or document.included_reason
        },
        actor_id=actor_id
    )


def log_document_removed(transaction_id, document_id, template_name, actor_id=None):
    """Log when a document is removed from a transaction."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_REMOVED,
        transaction_id=transaction_id,
        document_id=document_id,
        description=f"Document removed: {template_name}",
        event_data={
            'template_name': template_name
        },
        actor_id=actor_id
    )


def log_document_filled(document, changed_fields=None, actor_id=None):
    """Log when a document form is filled/updated."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_FILLED,
        transaction_id=document.transaction_id,
        document_id=document.id,
        description=f"Document form saved: {document.template_name}",
        event_data={
            'template_slug': document.template_slug,
            'changed_fields': changed_fields,
            'field_count': len(document.field_data) if document.field_data else 0
        },
        actor_id=actor_id
    )


def log_document_generated(document, actor_id=None):
    """Log when a document preview is generated."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_GENERATED,
        transaction_id=document.transaction_id,
        document_id=document.id,
        description=f"Document preview generated: {document.template_name}",
        event_data={
            'template_slug': document.template_slug,
            'docuseal_submission_id': document.docuseal_submission_id
        },
        actor_id=actor_id
    )


def log_document_package_generated(transaction, documents, actor_id=None):
    """Log when a document package is generated from intake."""
    doc_names = [d.template_name for d in documents]
    return log_event(
        event_type=AuditEvent.DOCUMENT_PACKAGE_GENERATED,
        transaction_id=transaction.id,
        description=f"Document package generated with {len(documents)} documents",
        event_data={
            'document_count': len(documents),
            'documents': doc_names
        },
        actor_id=actor_id
    )


def log_intake_saved(transaction, intake_data, actor_id=None):
    """Log when intake questionnaire is saved."""
    return log_event(
        event_type=AuditEvent.INTAKE_SAVED,
        transaction_id=transaction.id,
        description="Intake questionnaire saved",
        event_data={
            'question_count': len(intake_data) if intake_data else 0,
            'questions_answered': list(intake_data.keys()) if intake_data else []
        },
        actor_id=actor_id
    )


# =============================================================================
# E-SIGNATURE EVENTS
# =============================================================================

def log_document_sent(document, signers, submission_id, actor_id=None):
    """Log when a document is sent for signature."""
    signer_info = [{
        'email': s.get('email'),
        'name': s.get('name', ''),
        'role': s.get('role')
    } for s in signers]
    
    # Build a readable list of recipients
    recipient_list = ', '.join([f"{s.get('name', s.get('email'))} ({s.get('role', 'Signer')})" for s in signers])
    
    return log_event(
        event_type=AuditEvent.DOCUMENT_SENT,
        transaction_id=document.transaction_id,
        document_id=document.id,
        description=f"Sent to: {recipient_list}",
        event_data={
            'template_slug': document.template_slug,
            'template_name': document.template_name,
            'docuseal_submission_id': submission_id,
            'signers': signer_info,
            'signer_count': len(signers),
            'recipient_summary': recipient_list
        },
        actor_id=actor_id
    )


def log_envelope_sent(transaction, documents, signers, submission_id, actor_id=None):
    """Log when multiple documents are sent as one envelope."""
    doc_names = [d.template_name for d in documents]
    signer_info = [{
        'email': s.email if hasattr(s, 'email') else s.get('email'),
        'name': s.name if hasattr(s, 'name') else s.get('name', ''),
        'role': s.role if hasattr(s, 'role') else s.get('role')
    } for s in signers]
    
    # Build a readable list of recipients
    recipient_list = ', '.join([
        f"{s.get('name') or s.get('email')} ({s.get('role', 'Signer')})" 
        for s in signer_info
    ])
    
    return log_event(
        event_type=AuditEvent.ENVELOPE_SENT,
        transaction_id=transaction.id,
        description=f"{len(documents)} docs sent to: {recipient_list}",
        event_data={
            'docuseal_submission_id': submission_id,
            'documents': doc_names,
            'document_count': len(documents),
            'signers': signer_info,
            'signer_count': len(signers),
            'recipient_summary': recipient_list
        },
        actor_id=actor_id
    )


def log_document_resent(document, resent_count, actor_id=None):
    """Log when signature request emails are resent."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_RESENT,
        transaction_id=document.transaction_id,
        document_id=document.id,
        description=f"Signature request resent for: {document.template_name}",
        event_data={
            'template_slug': document.template_slug,
            'docuseal_submission_id': document.docuseal_submission_id,
            'resent_count': resent_count
        },
        actor_id=actor_id
    )


def log_document_voided(document, actor_id=None):
    """Log when a document is voided/reset."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_VOIDED,
        transaction_id=document.transaction_id,
        document_id=document.id,
        description=f"Document voided: {document.template_name}",
        event_data={
            'template_slug': document.template_slug,
            'previous_submission_id': document.docuseal_submission_id
        },
        actor_id=actor_id
    )


def log_document_viewed(document, signature, webhook_data=None):
    """Log when a signer views a document (from webhook)."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_VIEWED,
        transaction_id=document.transaction_id,
        document_id=document.id,
        signature_id=signature.id if signature else None,
        description=f"Document viewed by {signature.signer_name if signature else 'signer'}",
        event_data={
            'signer_email': signature.signer_email if signature else None,
            'signer_role': signature.signer_role if signature else None,
            'webhook_data': webhook_data
        },
        source='webhook',
        actor_id=None  # Webhook events have no actor
    )


def log_document_signed(document, signature=None, webhook_data=None):
    """Log when a document is signed (from webhook or completion)."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_SIGNED,
        transaction_id=document.transaction_id,
        document_id=document.id,
        signature_id=signature.id if signature else None,
        description=f"Document signed: {document.template_name}" +
                    (f" by {signature.signer_name}" if signature else ""),
        event_data={
            'template_slug': document.template_slug,
            'signer_email': signature.signer_email if signature else None,
            'signer_role': signature.signer_role if signature else None,
            'docuseal_submission_id': document.docuseal_submission_id,
            'webhook_data': webhook_data
        },
        source='webhook' if webhook_data else 'app',
        actor_id=None  # Signature events have no app actor
    )


def log_document_signed_physical(document, file_size, original_filename=None, actor_id=None):
    """Log when a scanned signed document is uploaded (physical signature)."""
    return log_event(
        event_type=AuditEvent.DOCUMENT_SIGNED_PHYSICAL,
        transaction_id=document.transaction_id,
        document_id=document.id,
        description=f"Scanned signed document uploaded: {document.template_name}",
        event_data={
            'template_slug': document.template_slug,
            'template_name': document.template_name,
            'file_size': file_size,
            'original_filename': original_filename,
            'signed_file_path': document.signed_file_path,
            'signing_method': 'physical'
        },
        source='app',
        actor_id=actor_id
    )


def log_document_declined(document, signature=None, webhook_data=None):
    """Log when a signer declines to sign a document."""
    signer_name = webhook_data.get('signer_name', '') if webhook_data else ''
    signer_email = webhook_data.get('signer_email', '') if webhook_data else ''
    decline_reason = webhook_data.get('decline_reason', '') if webhook_data else ''
    
    description = f"Document declined: {document.template_name}"
    if signer_name:
        description += f" by {signer_name}"
    if decline_reason:
        description += f" - Reason: {decline_reason}"
    
    return log_event(
        event_type=AuditEvent.DOCUMENT_DECLINED,
        transaction_id=document.transaction_id,
        document_id=document.id,
        signature_id=signature.id if signature else None,
        description=description,
        event_data={
            'template_slug': document.template_slug,
            'template_name': document.template_name,
            'signer_email': signer_email,
            'signer_name': signer_name,
            'signer_role': webhook_data.get('signer_role') if webhook_data else None,
            'decline_reason': decline_reason,
            'docuseal_submission_id': document.docuseal_submission_id,
            'webhook_data': webhook_data
        },
        source='webhook',
        actor_id=None  # Webhook events have no app actor
    )


# =============================================================================
# WEBHOOK EVENTS
# =============================================================================

def log_webhook_received(transaction_id, document_id, event_type, raw_payload):
    """Log raw webhook receipt for audit trail."""
    # Sanitize payload - remove any sensitive data
    sanitized_payload = {
        'event_type': raw_payload.get('event_type'),
        'submission_id': raw_payload.get('submission_id'),
        'submitter_id': raw_payload.get('data', {}).get('id') if raw_payload.get('data') else None,
        'status': raw_payload.get('data', {}).get('status') if raw_payload.get('data') else None,
    }

    return log_event(
        event_type=AuditEvent.WEBHOOK_RECEIVED,
        transaction_id=transaction_id,
        document_id=document_id,
        description=f"Webhook received: {event_type}",
        event_data={
            'webhook_event_type': event_type,
            'payload_summary': sanitized_payload
        },
        source='webhook',
        actor_id=None
    )


# =============================================================================
# QUERY HELPERS
# =============================================================================

def get_transaction_history(transaction_id, limit=100, offset=0):
    """
    Get audit history for a transaction, ordered by most recent first.

    Args:
        transaction_id: The transaction ID to query
        limit: Maximum number of events to return
        offset: Number of events to skip

    Returns:
        List of AuditEvent objects
    """
    return AuditEvent.query.filter_by(
        transaction_id=transaction_id
    ).order_by(
        AuditEvent.created_at.desc()
    ).offset(offset).limit(limit).all()


def get_document_history(document_id, limit=50, offset=0):
    """
    Get audit history for a specific document.

    Args:
        document_id: The document ID to query
        limit: Maximum number of events to return
        offset: Number of events to skip

    Returns:
        List of AuditEvent objects
    """
    return AuditEvent.query.filter_by(
        document_id=document_id
    ).order_by(
        AuditEvent.created_at.desc()
    ).offset(offset).limit(limit).all()


def get_user_activity(user_id, limit=50, offset=0):
    """
    Get audit history for actions by a specific user.

    Args:
        user_id: The user ID to query
        limit: Maximum number of events to return
        offset: Number of events to skip

    Returns:
        List of AuditEvent objects
    """
    return AuditEvent.query.filter_by(
        actor_id=user_id
    ).order_by(
        AuditEvent.created_at.desc()
    ).offset(offset).limit(limit).all()


def format_event_for_display(event):
    """
    Format an audit event for display in the UI.

    Returns a dict with display-friendly data.
    """
    # Map event types to icons and colors
    event_display = {
        AuditEvent.TRANSACTION_CREATED: {'icon': 'fas fa-plus-circle', 'color': 'success', 'label': 'Transaction Created'},
        AuditEvent.TRANSACTION_UPDATED: {'icon': 'fas fa-edit', 'color': 'info', 'label': 'Transaction Updated'},
        AuditEvent.TRANSACTION_STATUS_CHANGED: {'icon': 'fas fa-exchange-alt', 'color': 'warning', 'label': 'Status Changed'},
        AuditEvent.TRANSACTION_DELETED: {'icon': 'fas fa-trash', 'color': 'danger', 'label': 'Transaction Deleted'},
        AuditEvent.PARTICIPANT_ADDED: {'icon': 'fas fa-user-plus', 'color': 'success', 'label': 'Participant Added'},
        AuditEvent.PARTICIPANT_REMOVED: {'icon': 'fas fa-user-minus', 'color': 'danger', 'label': 'Participant Removed'},
        AuditEvent.DOCUMENT_ADDED: {'icon': 'fas fa-file-medical', 'color': 'success', 'label': 'Document Added'},
        AuditEvent.DOCUMENT_REMOVED: {'icon': 'fas fa-file-excel', 'color': 'danger', 'label': 'Document Removed'},
        AuditEvent.DOCUMENT_FILLED: {'icon': 'fas fa-file-alt', 'color': 'info', 'label': 'Document Filled'},
        AuditEvent.DOCUMENT_GENERATED: {'icon': 'fas fa-file-pdf', 'color': 'primary', 'label': 'Preview Generated'},
        AuditEvent.DOCUMENT_PACKAGE_GENERATED: {'icon': 'fas fa-folder-plus', 'color': 'success', 'label': 'Package Generated'},
        AuditEvent.DOCUMENT_PACKAGE_SYNCED: {'icon': 'fas fa-sync-alt', 'color': 'info', 'label': 'Package Updated'},
        AuditEvent.DOCUMENT_SENT: {'icon': 'fas fa-paper-plane', 'color': 'primary', 'label': 'Sent for Signature'},
        AuditEvent.ENVELOPE_SENT: {'icon': 'fas fa-envelope', 'color': 'primary', 'label': 'Envelope Sent'},
        AuditEvent.DOCUMENT_RESENT: {'icon': 'fas fa-redo', 'color': 'warning', 'label': 'Resent'},
        AuditEvent.DOCUMENT_VOIDED: {'icon': 'fas fa-ban', 'color': 'danger', 'label': 'Document Voided'},
        AuditEvent.DOCUMENT_VIEWED: {'icon': 'fas fa-eye', 'color': 'info', 'label': 'Viewed'},
        AuditEvent.DOCUMENT_SIGNED: {'icon': 'fas fa-signature', 'color': 'success', 'label': 'Signed'},
        AuditEvent.DOCUMENT_SIGNED_PHYSICAL: {'icon': 'fas fa-pen-nib', 'color': 'success', 'label': 'Wet Signed'},
        AuditEvent.DOCUMENT_UPLOADED_EXTERNAL: {'icon': 'fas fa-file-import', 'color': 'purple', 'label': 'External Upload'},
        AuditEvent.DOCUMENT_SENT_ADHOC: {'icon': 'fas fa-paper-plane', 'color': 'info', 'label': 'Sent (Ad-hoc)'},
        AuditEvent.DOCUMENT_CONVERTED_HYBRID: {'icon': 'fas fa-code-branch', 'color': 'warning', 'label': 'Converted to Hybrid'},
        AuditEvent.DOCUMENT_DECLINED: {'icon': 'fas fa-times-circle', 'color': 'danger', 'label': 'Declined'},
        AuditEvent.WEBHOOK_RECEIVED: {'icon': 'fas fa-webhook', 'color': 'secondary', 'label': 'Webhook'},
        AuditEvent.INTAKE_SAVED: {'icon': 'fas fa-clipboard-check', 'color': 'info', 'label': 'Intake Saved'},
    }

    display = event_display.get(event.event_type, {
        'icon': 'fas fa-circle',
        'color': 'secondary',
        'label': event.event_type.replace('_', ' ').title()
    })

    return {
        'id': event.id,
        'event_type': event.event_type,
        'description': event.description,
        'event_data': event.event_data,
        'source': event.source,
        'created_at': event.created_at.isoformat() + 'Z' if event.created_at else None,  # Append Z to indicate UTC
        'actor_id': event.actor_id,
        'actor_name': f"{event.actor.first_name} {event.actor.last_name}" if event.actor else None,
        'document_id': event.document_id,
        'signature_id': event.signature_id,
        'icon': display['icon'],
        'color': display['color'],
        'label': display['label'],
        'ip_address': event.ip_address
    }
