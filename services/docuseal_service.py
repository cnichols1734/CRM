# services/docuseal_service.py
"""
DocuSeal Integration Service

This service handles all DocuSeal API interactions for e-signatures.
Supports both test (sandbox) and production modes via environment variables.

Configuration:
- DOCUSEAL_API_KEY_TEST: API key for test/sandbox mode
- DOCUSEAL_API_KEY_PROD: API key for production
- DOCUSEAL_MODE: 'test' or 'prod' (defaults to 'test')

Field Mappings:
- Stored in YAML files at: docuseal_mappings/<template-slug>.yml
- Each template has its own mapping file
- See docuseal_mappings/listing-agreement.yml for format example
"""

import os
import json
import requests
import logging
import yaml
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to mapping files
MAPPINGS_DIR = Path(__file__).parent.parent / 'docuseal_mappings'

# Configuration - supports test and production modes
DOCUSEAL_MODE = os.environ.get('DOCUSEAL_MODE', 'test').lower()
DOCUSEAL_API_KEY_TEST = os.environ.get('DOCUSEAL_API_KEY_TEST', '')
DOCUSEAL_API_KEY_PROD = os.environ.get('DOCUSEAL_API_KEY_PROD', '')

# Select the appropriate API key based on mode
if DOCUSEAL_MODE == 'prod':
    DOCUSEAL_API_KEY = DOCUSEAL_API_KEY_PROD
    DOCUSEAL_API_URL = 'https://api.docuseal.com'
else:
    DOCUSEAL_API_KEY = DOCUSEAL_API_KEY_TEST
    DOCUSEAL_API_URL = 'https://api.docuseal.com'  # Same URL for test mode, different key

# Mock mode is now controlled by whether we have a valid API key
DOCUSEAL_MOCK_MODE = not bool(DOCUSEAL_API_KEY)

# Map our template slugs to DocuSeal template IDs
# NOTE: Template IDs may differ between test and production environments
TEMPLATE_MAP = {
    'listing-agreement': 2468023,  # Listing Agreement template
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

# =============================================================================
# FIELD MAPPING LOADER
# =============================================================================
# Field mappings are stored in YAML files at: docuseal_mappings/<template-slug>.yml
# This keeps the service code clean and makes mappings easier to maintain.
# 
# Each YAML file contains:
#   - template_id: DocuSeal template ID
#   - field_mappings: Dict mapping our form fields to DocuSeal fields
#   - transforms: Rules for value transformation (radio mappings, currency, dates)
#   - agent_fields: Fields to populate from logged-in agent
#   - computed_fields: Fields that combine multiple form values
# =============================================================================

# Cache for loaded mappings
_mapping_cache: Dict[str, Dict[str, Any]] = {}


def load_field_mapping(template_slug: str) -> Optional[Dict[str, Any]]:
    """
    Load field mapping from YAML file for a template.
    
    Args:
        template_slug: Template identifier (e.g., 'listing-agreement')
        
    Returns:
        Dict with field_mappings, transforms, agent_fields, etc.
        Returns None if no mapping file exists.
    """
    # Check cache first
    if template_slug in _mapping_cache:
        return _mapping_cache[template_slug]
    
    # Load from YAML file
    mapping_file = MAPPINGS_DIR / f"{template_slug}.yml"
    
    if not mapping_file.exists():
        logger.warning(f"No field mapping file found: {mapping_file}")
        return None
    
    try:
        with open(mapping_file, 'r') as f:
            mapping = yaml.safe_load(f)
        
        # Cache it
        _mapping_cache[template_slug] = mapping
        logger.info(f"Loaded field mapping for {template_slug}")
        return mapping
        
    except Exception as e:
        logger.error(f"Error loading field mapping for {template_slug}: {e}")
        return None


def get_field_mappings(template_slug: str) -> Dict[str, str]:
    """Get just the field mappings dict from a template's YAML config."""
    mapping = load_field_mapping(template_slug)
    if mapping:
        return mapping.get('field_mappings', {})
    return {}


def get_mapping_file_path(template_slug: str) -> str:
    """Get the path to the mapping file for a template (for display in admin)."""
    return str(MAPPINGS_DIR / f"{template_slug}.yml")

def _format_date(date_str: str) -> str:
    """Convert date to MM/DD/YYYY format for DocuSeal."""
    if not date_str:
        return ''
    try:
        # Handle YYYY-MM-DD format from HTML date inputs
        if isinstance(date_str, str) and '-' in date_str:
            d = datetime.strptime(date_str, '%Y-%m-%d')
            return d.strftime('%m/%d/%Y')
        return date_str
    except:
        return date_str


def _format_currency(value: Any) -> str:
    """Strip currency formatting ($, commas) for DocuSeal number fields."""
    if not value:
        return ''
    return str(value).replace(',', '').replace('$', '').strip()


def _apply_transform(value: Any, field_name: str, transforms: Dict[str, Any]) -> str:
    """
    Apply transformation rules from YAML config to a field value.
    
    Handles:
    - radio_mappings: Map our values to DocuSeal values
    - currency_fields: Strip $ and commas
    - date_fields: Convert to MM/DD/YYYY
    """
    if not transforms:
        return str(value) if value else ''
    
    # Check if it's a currency field
    currency_fields = transforms.get('currency_fields', [])
    if field_name in currency_fields:
        return _format_currency(value)
    
    # Check if it's a date field
    date_fields = transforms.get('date_fields', [])
    if field_name in date_fields:
        return _format_date(value)
    
    # Check for radio mapping
    radio_mappings = transforms.get('radio_mappings', {})
    if field_name in radio_mappings:
        mapping = radio_mappings[field_name]
        str_value = str(value).lower() if value else ''
        return mapping.get(str_value, str(value) if value else '')
    
    # No transformation needed
    return str(value) if value else ''


def build_docuseal_fields(form_data: Dict[str, Any], template_slug: str, agent_data: Dict[str, Any] = None, submitter_role: str = None) -> List[Dict[str, Any]]:
    """
    Convert CRM form data to DocuSeal fields format.
    
    Loads field mappings from YAML file at: docuseal_mappings/<template_slug>.yml
    
    Args:
        form_data: Dict of form field names to values (from TransactionDocument.field_data)
        template_slug: The template being filled (e.g., 'listing-agreement')
        agent_data: Optional dict with agent info (name, email, license_number, phone)
        submitter_role: Optional role filter - 'Seller' returns only seller fields, 
                       'Broker' returns only broker/agent fields, None returns all
    
    Returns:
        List of dicts with 'name' and 'default_value' for DocuSeal API
    """
    fields = []
    
    # Load mapping from YAML file
    mapping = load_field_mapping(template_slug)
    if not mapping:
        logger.warning(f"No field mapping found for template: {template_slug}")
        return fields
    
    # For Broker submitter, return agent_fields + broker_form_fields
    if submitter_role == 'Broker':
        transforms = mapping.get('transforms', {})
        
        # 1. Add agent auto-fill fields
        agent_field_configs = mapping.get('agent_fields', [])
        if agent_data and agent_field_configs:
            for config in agent_field_configs:
                docuseal_name = config.get('docuseal_field')
                source = config.get('source', '')
                
                if docuseal_name and source:
                    parts = source.split('.')
                    if len(parts) == 2 and parts[0] == 'agent':
                        value = agent_data.get(parts[1], '')
                        if value:
                            fields.append({
                                'name': docuseal_name,
                                'default_value': str(value)
                            })
        elif agent_data and not agent_field_configs:
            # Fallback standard agent fields
            standard_agent_fields = [
                ('Broker printed name', agent_data.get('name', '')),
                ('Broker associate printed name', agent_data.get('name', '')),
                ('Broker license number', agent_data.get('license_number', '')),
                ('Broker associate license', agent_data.get('license_number', '')),
                ('Broker email/fax', agent_data.get('email', '')),
            ]
            for docuseal_name, value in standard_agent_fields:
                if value:
                    fields.append({
                        'name': docuseal_name,
                        'default_value': str(value)
                    })
        
        # 2. Add broker form fields (form data that goes to Broker submitter)
        broker_form_fields = mapping.get('broker_form_fields', {})
        for form_field, docuseal_field in broker_form_fields.items():
            if not docuseal_field:
                continue
            if form_field in form_data and form_data[form_field]:
                value = form_data[form_field]
                transformed_value = _apply_transform(value, form_field, transforms)
                if transformed_value:
                    fields.append({
                        'name': docuseal_field,
                        'default_value': transformed_value
                    })
        
        logger.info(f"Built {len(fields)} DocuSeal Broker fields")
        return fields
    
    # For Seller submitter or no filter, return form-based fields
    field_map = mapping.get('field_mappings', {})
    transforms = mapping.get('transforms', {})
    
    # Map form fields to DocuSeal fields
    for form_field, docuseal_field in field_map.items():
        if not docuseal_field:
            continue  # Skip fields mapped to null
            
        if form_field in form_data and form_data[form_field]:
            value = form_data[form_field]
            
            # Apply transformation if needed
            transformed_value = _apply_transform(value, form_field, transforms)
            
            if transformed_value:  # Only add non-empty values
                fields.append({
                    'name': docuseal_field,
                    'default_value': transformed_value
                })
    
    # Handle computed fields (fields that combine multiple form values)
    computed_fields = mapping.get('computed_fields', [])
    for computed in computed_fields:
        docuseal_name = computed.get('docuseal_field')
        template = computed.get('template', '')
        
        if docuseal_name and template:
            try:
                # Simple template substitution using format
                computed_value = template.format(**form_data)
                if computed_value and computed_value != template:  # Only add if substitution worked
                    fields.append({
                        'name': docuseal_name,
                        'default_value': computed_value
                    })
            except KeyError:
                pass  # Missing form field, skip this computed field
    
    # Add agent/broker fields if provided (only when not filtering for Seller)
    if submitter_role != 'Seller':
        agent_field_configs = mapping.get('agent_fields', [])
        if agent_data and agent_field_configs:
            for config in agent_field_configs:
                docuseal_name = config.get('docuseal_field')
                source = config.get('source', '')
                
                if docuseal_name and source:
                    # Parse source like "agent.name" or "agent.license_number"
                    parts = source.split('.')
                    if len(parts) == 2 and parts[0] == 'agent':
                        value = agent_data.get(parts[1], '')
                        if value:
                            fields.append({
                                'name': docuseal_name,
                                'default_value': str(value)
                            })
        
        # Fallback: Add standard agent fields if no config specified
        elif agent_data and not agent_field_configs:
            standard_agent_fields = [
                ('Broker printed name', agent_data.get('name', '')),
                ('Broker associate printed name', agent_data.get('name', '')),
                ('Broker license number', agent_data.get('license_number', '')),
                ('Broker associate license', agent_data.get('license_number', '')),
                ('Broker email/fax', agent_data.get('email', '')),
            ]
            for docuseal_name, value in standard_agent_fields:
                if value:
                    fields.append({
                        'name': docuseal_name,
                        'default_value': str(value)
                    })
    
    role_label = f" for {submitter_role}" if submitter_role else ""
    logger.info(f"Built {len(fields)} DocuSeal fields{role_label} from form data for {template_slug}")
    return fields

# Mock storage for development (in-memory)
_mock_submissions = {}
_mock_events = []


class DocuSealError(Exception):
    """Custom exception for DocuSeal API errors."""
    pass


def _get_headers() -> Dict[str, str]:
    """Get headers for DocuSeal API requests."""
    return {
        'X-Auth-Token': DOCUSEAL_API_KEY,
        'Content-Type': 'application/json'
    }


def get_template_id(slug: str) -> Optional[int]:
    """Get DocuSeal template ID for a document slug."""
    return TEMPLATE_MAP.get(slug)


def is_template_ready(slug: str) -> bool:
    """Check if a template has been uploaded to DocuSeal."""
    return TEMPLATE_MAP.get(slug) is not None


# =============================================================================
# TEMPLATE MANAGEMENT
# =============================================================================

def get_template(template_id: int) -> Dict[str, Any]:
    """
    Fetch template details from DocuSeal including all fields.
    
    Returns template info with:
    - fields: List of field definitions (name, type, required, submitter_uuid)
    - submitters: List of submitter roles (name, uuid)
    - documents: List of attached documents
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_get_template(template_id)
    
    try:
        response = requests.get(
            f"{DOCUSEAL_API_URL}/templates/{template_id}",
            headers=_get_headers()
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal get_template error: {e}")
        raise DocuSealError(f"Failed to fetch template: {e}")


def get_template_fields(template_id: int) -> List[Dict[str, Any]]:
    """
    Get just the fields from a template.
    
    Returns list of field dicts with:
    - name: Field name (used for pre-filling)
    - type: Field type (text, signature, date, checkbox, etc.)
    - required: Whether field is required
    - submitter_uuid: Which signer this field belongs to
    """
    template = get_template(template_id)
    return template.get('fields', [])


def get_template_submitters(template_id: int) -> List[Dict[str, Any]]:
    """
    Get the submitter roles defined in a template.
    
    Returns list of submitter dicts with:
    - name: Role name (e.g., "Seller", "Listing Agent")
    - uuid: Unique identifier for this role
    """
    template = get_template(template_id)
    return template.get('submitters', [])


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
        submitters: List of signers with role, email, name, and optional fields[]
        field_values: Pre-filled field values (applied to all submitters if not in submitter.fields)
        send_email: Whether to send email invitations
        message: Custom email subject/body
    
    Returns:
        Submission data with ID and submitter details
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_create_submission(template_slug, submitters, field_values, send_email)
    
    # Get template ID
    template_id = get_template_id(template_slug)
    if not template_id:
        raise DocuSealError(f"Template not configured for slug: {template_slug}")
    
    # Build the request payload
    payload = {
        "template_id": template_id,
        "send_email": send_email,
        "submitters": submitters
    }
    
    # Add custom message if provided
    if message:
        payload["message"] = message
    
    logger.info(f"Creating DocuSeal submission for template {template_id} ({template_slug})")
    logger.debug(f"Submitters: {[s.get('email') for s in submitters]}")
    
    try:
        response = requests.post(
            f"{DOCUSEAL_API_URL}/submissions",
            headers=_get_headers(),
            json=payload
        )
        response.raise_for_status()
        result = response.json()
        
        # DocuSeal returns a list of submitters, each with their own id and slug
        # We need to normalize this to a dict with 'id' and 'submitters' keys
        if isinstance(result, list):
            # Get submission_id from first submitter (all share the same submission)
            submission_id = result[0].get('submission_id') if result else None
            normalized_result = {
                'id': submission_id,
                'submitters': result
            }
            logger.info(f"DocuSeal submission created: {submission_id} with {len(result)} submitters")
            return normalized_result
        
        logger.info(f"DocuSeal submission created: {result.get('id')}")
        return result
        
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal create_submission error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        raise DocuSealError(f"Failed to create submission: {e}")


def get_submission(submission_id: str) -> Dict[str, Any]:
    """Get submission details by ID."""
    if DOCUSEAL_MOCK_MODE:
        return _mock_get_submission(submission_id)
    
    try:
        response = requests.get(
            f"{DOCUSEAL_API_URL}/submissions/{submission_id}",
            headers=_get_headers()
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal get_submission error: {e}")
        raise DocuSealError(f"Failed to get submission: {e}")


def get_submission_status(submission_id: str) -> str:
    """
    Get the current status of a submission.
    
    Returns: 'pending', 'viewed', 'started', 'completed', 'expired'
    """
    submission = get_submission(submission_id)
    return submission.get('status', 'pending')


def resend_signature_emails(submission_id: str, submitter_ids: List[int] = None, message: Dict[str, str] = None) -> Dict[str, Any]:
    """
    Resend signature request emails to submitters who haven't completed signing.
    
    Uses the DocuSeal Update Submitter API with send_email=true.
    
    Args:
        submission_id: The DocuSeal submission ID
        submitter_ids: Optional list of specific submitter IDs to resend to.
                      If None, resends to all pending submitters.
        message: Optional custom message with 'subject' and 'body' keys.
                Body can include {{submitter.link}} for the signing link.
    
    Returns:
        Dict with 'success', 'resent_count', and 'submitters' list
    """
    if DOCUSEAL_MOCK_MODE:
        return {
            'success': True,
            'resent_count': 1,
            'submitters': [{'id': 1, 'email': 'mock@example.com', 'status': 'sent'}],
            'message': 'Emails resent (mock mode)'
        }
    
    try:
        # Get current submission to find pending submitters
        submission = get_submission(submission_id)
        submitters = submission.get('submitters', [])
        
        resent = []
        for submitter in submitters:
            # Skip if submitter already completed
            if submitter.get('status') == 'completed':
                continue
            
            # Skip if we're targeting specific submitter IDs and this isn't one
            if submitter_ids and submitter.get('id') not in submitter_ids:
                continue
            
            submitter_id = submitter.get('id')
            if not submitter_id:
                continue
            
            # Build update payload
            update_payload = {
                'send_email': True
            }
            
            # Add custom message if provided
            if message:
                update_payload['message'] = {
                    'subject': message.get('subject', 'Reminder: Document Ready for Signature'),
                    'body': message.get('body', 'Please sign the document at your earliest convenience. Click here to sign: {{submitter.link}}')
                }
            
            # Call the Update Submitter API
            headers = {
                'X-Auth-Token': DOCUSEAL_API_KEY,
                'Content-Type': 'application/json'
            }
            
            response = requests.put(
                f"{DOCUSEAL_API_URL}/submitters/{submitter_id}",
                headers=headers,
                json=update_payload
            )
            response.raise_for_status()
            
            result = response.json()
            resent.append({
                'id': result.get('id'),
                'email': result.get('email'),
                'name': result.get('name'),
                'status': result.get('status'),
                'sent_at': result.get('sent_at')
            })
            
            logger.info(f"Resent signature email to submitter {submitter_id}: {result.get('email')}")
        
        return {
            'success': True,
            'resent_count': len(resent),
            'submitters': resent,
            'message': f'Successfully resent emails to {len(resent)} submitter(s)'
        }
        
    except requests.RequestException as e:
        logger.error(f"DocuSeal resend_signature_emails error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        raise DocuSealError(f"Failed to resend signature emails: {e}")


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
    
    try:
        submission = get_submission(submission_id)
        documents = []
        
        # Extract document URLs from submission
        for doc in submission.get('documents', []):
            documents.append({
                'name': doc.get('filename', 'document.pdf'),
                'url': doc.get('url', '')
            })
        
        return documents
    except Exception as e:
        logger.error(f"DocuSeal get_signed_document_urls error: {e}")
        raise DocuSealError(f"Failed to get signed documents: {e}")


# =============================================================================
# TEMPLATE LISTING
# =============================================================================

def list_templates(limit: int = 100) -> List[Dict[str, Any]]:
    """List all templates in DocuSeal account."""
    if DOCUSEAL_MOCK_MODE:
        return _mock_list_templates()
    
    try:
        response = requests.get(
            f"{DOCUSEAL_API_URL}/templates",
            headers=_get_headers(),
            params={'limit': limit}
        )
        response.raise_for_status()
        result = response.json()
        return result.get('data', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal list_templates error: {e}")
        raise DocuSealError(f"Failed to list templates: {e}")


def upload_template(file_path: str, name: str) -> Dict[str, Any]:
    """
    Upload a PDF as a new template.
    
    Note: After upload, you'll need to use DocuSeal's form builder
    to add signature fields, then update TEMPLATE_MAP with the ID.
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_upload_template(name)
    
    # Real implementation would use multipart form upload
    raise DocuSealError("Template upload should be done via DocuSeal web interface")


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


def _mock_get_template(template_id: int) -> Dict[str, Any]:
    """Mock implementation of get_template."""
    return {
        'id': template_id,
        'name': 'Listing Agreement',
        'fields': [
            {'uuid': 'f1', 'name': 'Seller Name', 'type': 'text', 'required': True},
            {'uuid': 'f2', 'name': 'Property Address', 'type': 'text', 'required': True},
            {'uuid': 'f3', 'name': 'Listing Price', 'type': 'text', 'required': True},
            {'uuid': 'f4', 'name': 'Seller Signature', 'type': 'signature', 'required': True},
        ],
        'submitters': [
            {'name': 'Seller', 'uuid': 's1'},
            {'name': 'Listing Agent', 'uuid': 's2'},
        ],
        'documents': []
    }


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
    
    # NOTE: Co-seller would need a separate DocuSeal template role
    # For now, only primary seller signs as "Seller"
    
    # Get listing agent - maps to "Broker" in DocuSeal
    for p in participants:
        if p.role == 'listing_agent':
            submitters.append({
                'role': 'Broker',  # DocuSeal uses "Broker" not "Listing Agent"
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

