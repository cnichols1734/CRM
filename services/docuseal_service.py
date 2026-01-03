# services/docuseal_service.py
"""
DocuSeal Integration Service

This service handles all DocuSeal API interactions for e-signatures.
Currently uses mock implementations - swap in real API calls when ready.

To enable real integration:
1. Sign up at https://console.docuseal.com/api
2. Add DOCUSEAL_API_KEY to .env
3. Set DOCUSEAL_MOCK_MODE=False in .env
4. Upload templates to DocuSeal and update TEMPLATE_MAP
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import uuid

# Configuration
DOCUSEAL_API_KEY = os.environ.get('DOCUSEAL_API_KEY', '')
DOCUSEAL_API_URL = os.environ.get('DOCUSEAL_API_URL', 'https://api.docuseal.com')
DOCUSEAL_MOCK_MODE = os.environ.get('DOCUSEAL_MOCK_MODE', 'True').lower() == 'true'

# Map our template slugs to DocuSeal template IDs
# Update these after uploading templates to DocuSeal
TEMPLATE_MAP = {
    'listing-agreement': None,  # DocuSeal template ID
    'iabs': None,
    'sellers-disclosure': None,
    'wire-fraud-warning': None,
    'lead-paint': None,
    'hoa-addendum': None,
    'flood-hazard': None,
    'water-district': None,
    't47-affidavit': None,
    'sellers-net': None,
    'referral-agreement': None,
}

# Mock storage for development (in-memory)
_mock_submissions = {}
_mock_events = []


class DocuSealError(Exception):
    """Custom exception for DocuSeal API errors."""
    pass


def get_template_id(slug: str) -> Optional[int]:
    """Get DocuSeal template ID for a document slug."""
    return TEMPLATE_MAP.get(slug)


def is_template_ready(slug: str) -> bool:
    """Check if a template has been uploaded to DocuSeal."""
    return TEMPLATE_MAP.get(slug) is not None


# =============================================================================
# SUBMISSION MANAGEMENT
# =============================================================================

def create_submission(
    template_slug: str,
    submitters: List[Dict[str, Any]],
    field_values: Dict[str, Any] = None,
    send_email: bool = True,
    message: Dict[str, str] = None
) -> Dict[str, Any]:
    """
    Create a new submission (send document for signature).
    
    Args:
        template_slug: Our internal template identifier
        submitters: List of signers with role, email, name
        field_values: Pre-filled field values
        send_email: Whether to send email invitations
        message: Custom email subject/body
    
    Returns:
        Submission data with ID and submitter details
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_create_submission(template_slug, submitters, field_values, send_email)
    
    # Real API implementation
    template_id = get_template_id(template_slug)
    if not template_id:
        raise DocuSealError(f"Template not configured for slug: {template_slug}")
    
    # TODO: Implement real DocuSeal API call
    # import requests
    # response = requests.post(
    #     f"{DOCUSEAL_API_URL}/submissions",
    #     headers={
    #         "X-Auth-Token": DOCUSEAL_API_KEY,
    #         "Content-Type": "application/json"
    #     },
    #     json={
    #         "template_id": template_id,
    #         "send_email": send_email,
    #         "submitters": submitters,
    #         "fields": field_values or {},
    #         "message": message
    #     }
    # )
    # return response.json()
    
    raise DocuSealError("Real API not implemented. Set DOCUSEAL_MOCK_MODE=True")


def get_submission(submission_id: str) -> Dict[str, Any]:
    """Get submission details by ID."""
    if DOCUSEAL_MOCK_MODE:
        return _mock_get_submission(submission_id)
    
    # TODO: Real API call
    raise DocuSealError("Real API not implemented")


def get_submission_status(submission_id: str) -> str:
    """
    Get the current status of a submission.
    
    Returns: 'pending', 'viewed', 'started', 'completed', 'expired'
    """
    submission = get_submission(submission_id)
    return submission.get('status', 'pending')


def get_signing_url(submission_id: str, submitter_slug: str) -> str:
    """Get the signing URL for a specific submitter."""
    if DOCUSEAL_MOCK_MODE:
        return f"https://docuseal.com/sign/mock/{submitter_slug}"
    
    # Real implementation would return the actual signing URL
    submission = get_submission(submission_id)
    for submitter in submission.get('submitters', []):
        if submitter.get('slug') == submitter_slug:
            return submitter.get('embed_src', '')
    
    return ''


def get_signed_document_urls(submission_id: str) -> List[Dict[str, str]]:
    """
    Get URLs to download signed documents.
    
    Returns:
        List of dicts with 'name' and 'url' for each document
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_get_signed_documents(submission_id)
    
    # TODO: Real API call
    raise DocuSealError("Real API not implemented")


# =============================================================================
# TEMPLATE MANAGEMENT
# =============================================================================

def list_templates() -> List[Dict[str, Any]]:
    """List all templates in DocuSeal account."""
    if DOCUSEAL_MOCK_MODE:
        return _mock_list_templates()
    
    # TODO: Real API call
    raise DocuSealError("Real API not implemented")


def upload_template(file_path: str, name: str) -> Dict[str, Any]:
    """
    Upload a PDF as a new template.
    
    Note: After upload, you'll need to use DocuSeal's form builder
    to add signature fields, then update TEMPLATE_MAP with the ID.
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_upload_template(name)
    
    # TODO: Real API call with multipart form data
    raise DocuSealError("Real API not implemented")


# =============================================================================
# WEBHOOK HANDLING
# =============================================================================

def process_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process incoming webhook from DocuSeal.
    
    Event types:
    - form.viewed: Signer opened the form
    - form.started: Signer began filling
    - form.completed: All signers finished
    """
    event_type = payload.get('event_type')
    data = payload.get('data', {})
    
    result = {
        'event_type': event_type,
        'submission_id': data.get('submission_id'),
        'processed_at': datetime.utcnow().isoformat()
    }
    
    if event_type == 'form.completed':
        # Extract signed document URLs
        result['documents'] = data.get('documents', [])
        result['submitters'] = data.get('submitters', [])
    
    return result


# =============================================================================
# MOCK IMPLEMENTATIONS
# =============================================================================

def _mock_create_submission(
    template_slug: str,
    submitters: List[Dict[str, Any]],
    field_values: Dict[str, Any] = None,
    send_email: bool = True
) -> Dict[str, Any]:
    """Mock implementation of create_submission."""
    submission_id = str(uuid.uuid4())[:8]
    
    # Create mock submitters with slugs
    mock_submitters = []
    for i, sub in enumerate(submitters):
        submitter_slug = str(uuid.uuid4())[:12]
        mock_submitters.append({
            'id': 1000 + i,
            'slug': submitter_slug,
            'email': sub.get('email'),
            'name': sub.get('name', ''),
            'role': sub.get('role', 'Signer'),
            'status': 'pending',
            'sent_at': datetime.utcnow().isoformat() if send_email else None,
            'embed_src': f"https://docuseal.com/s/{submitter_slug}",
            'sign_url': f"https://docuseal.com/sign/{submitter_slug}"
        })
    
    submission = {
        'id': submission_id,
        'template_slug': template_slug,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat(),
        'expire_at': (datetime.utcnow() + timedelta(days=30)).isoformat(),
        'submitters': mock_submitters,
        'field_values': field_values or {},
        'documents': []
    }
    
    _mock_submissions[submission_id] = submission
    return submission


def _mock_get_submission(submission_id: str) -> Dict[str, Any]:
    """Mock implementation of get_submission."""
    if submission_id in _mock_submissions:
        return _mock_submissions[submission_id]
    
    # Return a default mock for any ID
    return {
        'id': submission_id,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat(),
        'submitters': []
    }


def _mock_get_signed_documents(submission_id: str) -> List[Dict[str, str]]:
    """Mock implementation of get_signed_document_urls."""
    submission = _mock_submissions.get(submission_id, {})
    
    # If status is completed, return mock document URLs
    if submission.get('status') == 'completed':
        return submission.get('documents', [])
    
    return []


def _mock_list_templates() -> List[Dict[str, Any]]:
    """Mock implementation of list_templates."""
    return [
        {'id': 1001, 'name': 'Listing Agreement', 'slug': 'listing-agreement'},
        {'id': 1002, 'name': 'IABS', 'slug': 'iabs'},
        {'id': 1003, 'name': "Seller's Disclosure", 'slug': 'sellers-disclosure'},
    ]


def _mock_upload_template(name: str) -> Dict[str, Any]:
    """Mock implementation of upload_template."""
    return {
        'id': 9999,
        'name': name,
        'created_at': datetime.utcnow().isoformat(),
        'status': 'draft'
    }


def _mock_simulate_signing(submission_id: str, event: str = 'completed') -> None:
    """
    Simulate signing events for testing.
    
    Usage in testing:
        from services.docuseal_service import _mock_simulate_signing
        _mock_simulate_signing('abc123', 'completed')
    """
    if submission_id not in _mock_submissions:
        return
    
    submission = _mock_submissions[submission_id]
    
    if event == 'viewed':
        for sub in submission['submitters']:
            sub['status'] = 'viewed'
            sub['viewed_at'] = datetime.utcnow().isoformat()
    
    elif event == 'started':
        for sub in submission['submitters']:
            sub['status'] = 'started'
    
    elif event == 'completed':
        submission['status'] = 'completed'
        submission['completed_at'] = datetime.utcnow().isoformat()
        for sub in submission['submitters']:
            sub['status'] = 'completed'
            sub['signed_at'] = datetime.utcnow().isoformat()
        
        # Add mock signed document URLs
        submission['documents'] = [
            {
                'name': f"{submission['template_slug']}_signed.pdf",
                'url': f"https://docuseal.com/downloads/mock/{submission_id}.pdf"
            }
        ]
    
    _mock_events.append({
        'event_type': f'form.{event}',
        'submission_id': submission_id,
        'timestamp': datetime.utcnow().isoformat()
    })


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def build_submitters_from_participants(participants, transaction) -> List[Dict[str, Any]]:
    """
    Build DocuSeal submitters list from transaction participants.
    
    Maps our roles to DocuSeal signer roles.
    """
    submitters = []
    
    # Get primary seller
    for p in participants:
        if p.role == 'seller' and p.is_primary:
            submitters.append({
                'role': 'Seller',
                'email': p.display_email,
                'name': p.display_name
            })
            break
    
    # Get co-seller if exists
    for p in participants:
        if p.role == 'co_seller':
            submitters.append({
                'role': 'Co-Seller',
                'email': p.display_email,
                'name': p.display_name
            })
            break
    
    # Get listing agent
    for p in participants:
        if p.role == 'listing_agent':
            submitters.append({
                'role': 'Listing Agent',
                'email': p.display_email,
                'name': p.display_name
            })
            break
    
    return submitters


def format_status_badge(status: str) -> Dict[str, str]:
    """Get badge class and label for a status."""
    status_map = {
        'pending': {'class': 'badge-ghost', 'label': 'Pending'},
        'sent': {'class': 'badge-info', 'label': 'Sent'},
        'viewed': {'class': 'badge-warning', 'label': 'Viewed'},
        'started': {'class': 'badge-warning', 'label': 'In Progress'},
        'completed': {'class': 'badge-success', 'label': 'Signed'},
        'expired': {'class': 'badge-error', 'label': 'Expired'},
        'filled': {'class': 'badge-secondary', 'label': 'Filled'},
        'generated': {'class': 'badge-primary', 'label': 'Generated'},
    }
    return status_map.get(status, {'class': 'badge-ghost', 'label': status.title()})

