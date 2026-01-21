"""
DocuSeal Client

Thin wrapper around the DocuSeal API for document submissions.
Handles authentication, request building, and error handling.

This is a simplified, focused client that only does what's needed
for the document generation system. For complex operations, use
the existing docuseal_service.py (which will eventually be deprecated).
"""

import logging
import os
from typing import Any, Dict, List, Optional

import requests

from .types import Submitter
from .exceptions import DocuSealAPIError

logger = logging.getLogger(__name__)

# Configuration from environment
DOCUSEAL_MODE = os.environ.get('DOCUSEAL_MODE', 'test').lower()
DOCUSEAL_API_KEY_TEST = os.environ.get('DOCUSEAL_API_KEY_TEST', '')
DOCUSEAL_API_KEY_PROD = os.environ.get('DOCUSEAL_API_KEY_PROD', '')

# Select API key based on mode
if DOCUSEAL_MODE == 'prod':
    DOCUSEAL_API_KEY = DOCUSEAL_API_KEY_PROD
else:
    DOCUSEAL_API_KEY = DOCUSEAL_API_KEY_TEST

DOCUSEAL_API_URL = 'https://api.docuseal.com'
DOCUSEAL_MOCK_MODE = not bool(DOCUSEAL_API_KEY)

# Request timeout
DEFAULT_TIMEOUT = 30


class DocuSealClient:
    """
    Client for DocuSeal API operations.
    
    Provides methods for:
        - Creating submissions (send for signature)
        - Creating preview submissions (no email)
        - Getting template information
    """
    
    @classmethod
    def is_mock_mode(cls) -> bool:
        """Check if running in mock mode (no API key)."""
        return DOCUSEAL_MOCK_MODE
    
    @classmethod
    def _get_headers(cls) -> Dict[str, str]:
        """Get request headers with auth."""
        return {
            'X-Auth-Token': DOCUSEAL_API_KEY,
            'Content-Type': 'application/json'
        }
    
    @classmethod
    def get_template(cls, template_id: int) -> Dict[str, Any]:
        """
        Fetch template details from DocuSeal.
        
        Returns:
            Template data with fields, submitters, etc.
        """
        if DOCUSEAL_MOCK_MODE:
            return cls._mock_template(template_id)
        
        try:
            response = requests.get(
                f"{DOCUSEAL_API_URL}/templates/{template_id}",
                headers=cls._get_headers(),
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise DocuSealAPIError(
                f"Failed to fetch template {template_id}: {e}",
                status_code=getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
            )
    
    @classmethod
    def get_template_roles(cls, template_id: int) -> List[str]:
        """
        Get the submitter role names from a template.
        
        Returns:
            List of role names (e.g., ['Seller', 'Broker', 'Seller 2'])
        """
        template = cls.get_template(template_id)
        submitters = template.get('submitters', [])
        return [s.get('name', '') for s in submitters]
    
    @classmethod
    def create_submission(
        cls,
        template_id: int,
        submitters: List[Submitter],
        send_email: bool = True,
        message: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Create a submission to send document for signature.
        
        Args:
            template_id: DocuSeal template ID
            submitters: List of Submitter objects
            send_email: Whether to send email invitations
            message: Optional custom message with 'subject' and 'body'
            
        Returns:
            Submission data with ID and submitter details
        """
        if DOCUSEAL_MOCK_MODE:
            return cls._mock_submission(template_id, submitters)
        
        payload = {
            'template_id': template_id,
            'send_email': send_email,
            'submitters': [s.to_dict() for s in submitters]
        }
        
        if message:
            payload['message'] = message
        
        try:
            response = requests.post(
                f"{DOCUSEAL_API_URL}/submissions",
                headers=cls._get_headers(),
                json=payload,
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            
            result = response.json()
            
            # DocuSeal returns a list of submitter results
            if isinstance(result, list):
                return {
                    'id': result[0].get('submission_id') if result else None,
                    'submitters': result
                }
            
            return result
            
        except requests.exceptions.RequestException as e:
            error_body = None
            status_code = None
            
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                try:
                    error_body = e.response.text
                except Exception:
                    pass
            
            logger.error(f"DocuSeal submission failed: {e}")
            if error_body:
                logger.error(f"Response body: {error_body}")
            
            raise DocuSealAPIError(
                f"Failed to create submission: {e}",
                status_code=status_code,
                response_body=error_body
            )
    
    @classmethod
    def create_submission_from_pdf(
        cls,
        pdf_base64: str,
        document_name: str,
        fields: List[Dict[str, Any]],
        submitters: List[Submitter],
        send_email: bool = True,
        order: str = 'preserved'
    ) -> Dict[str, Any]:
        """
        Create a submission from an arbitrary PDF with custom field placements.
        
        This is used for ad-hoc document signing (external documents or hybrid flows)
        where we don't use a DocuSeal template but instead upload a PDF and specify
        where signature fields should be placed.
        
        Args:
            pdf_base64: Base64-encoded PDF content
            document_name: Name for the document
            fields: List of field definitions, each with:
                - name: Field name (e.g., 'signature_seller')
                - type: Field type ('signature', 'initials', 'date', 'text')
                - role: Which submitter role this field belongs to
                - areas: List of placement areas [{x, y, w, h, page}]
            submitters: List of Submitter objects
            send_email: Whether to send email invitations
            order: 'preserved' (sequential) or 'random' (all at once)
            
        Returns:
            Submission data with ID and submitter details
        """
        if DOCUSEAL_MOCK_MODE:
            return cls._mock_submission_from_pdf(document_name, submitters)
        
        # Build the payload for /submissions/pdf endpoint
        payload = {
            'name': document_name,
            'send_email': send_email,
            'order': order,
            'documents': [{
                'name': document_name,
                'file': pdf_base64,
                'fields': fields
            }],
            'submitters': [s.to_dict() for s in submitters]
        }
        
        try:
            response = requests.post(
                f"{DOCUSEAL_API_URL}/submissions/pdf",
                headers=cls._get_headers(),
                json=payload,
                timeout=60  # Longer timeout for PDF upload
            )
            response.raise_for_status()
            
            result = response.json()
            
            # DocuSeal returns a list of submitter results
            if isinstance(result, list):
                return {
                    'id': result[0].get('submission_id') if result else None,
                    'submitters': result
                }
            
            return result
            
        except requests.exceptions.RequestException as e:
            error_body = None
            status_code = None
            
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                try:
                    error_body = e.response.text
                except Exception:
                    pass
            
            logger.error(f"DocuSeal submission from PDF failed: {e}")
            if error_body:
                logger.error(f"Response body: {error_body}")
            
            raise DocuSealAPIError(
                f"Failed to create submission from PDF: {e}",
                status_code=status_code,
                response_body=error_body
            )
    
    @classmethod
    def create_template_from_pdf(
        cls,
        pdf_base64: str,
        document_name: str,
        fields: List[Dict[str, Any]],
        external_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a DocuSeal template from a PDF file with custom field placements.
        
        This creates a reusable template that can be merged with other templates.
        Used for external documents where we need to include them in a merged package.
        
        Args:
            pdf_base64: Base64-encoded PDF content
            document_name: Name for the document/template
            fields: List of field definitions, each with:
                - name: Field name (e.g., 'signature_seller')
                - type: Field type ('signature', 'initials', 'date', 'text')
                - role: Which submitter role this field belongs to
                - areas: List of placement areas [{x, y, w, h, page}]
            external_id: Optional external ID for tracking/updating
            
        Returns:
            Template data with ID that can be used for merging
        """
        if DOCUSEAL_MOCK_MODE:
            # Return mock template data
            return {
                'id': 99999,
                'name': document_name,
                'external_id': external_id,
                'submitters': [{'name': 'Seller', 'uuid': 'mock-uuid'}],
                'fields': fields
            }
        
        # Build the payload for /templates/pdf endpoint
        payload = {
            'name': document_name,
            'documents': [{
                'name': document_name,
                'file': pdf_base64,
                'fields': fields
            }]
        }
        
        if external_id:
            payload['external_id'] = external_id
        
        try:
            response = requests.post(
                f"{DOCUSEAL_API_URL}/templates/pdf",
                headers=cls._get_headers(),
                json=payload,
                timeout=60  # Longer timeout for PDF upload
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            error_body = None
            status_code = None
            
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                try:
                    error_body = e.response.text
                except Exception:
                    pass
            
            logger.error(f"DocuSeal create template from PDF failed: {e}")
            if error_body:
                logger.error(f"Response body: {error_body}")
            
            raise DocuSealAPIError(
                f"Failed to create template from PDF: {e}",
                status_code=status_code,
                response_body=error_body
            )
    
    @classmethod
    def create_preview_submission(
        cls,
        template_id: int,
        submitters: List[Submitter]
    ) -> Optional[Dict[str, Any]]:
        """
        Create a preview submission (no email sent).
        
        Returns the submission ID and first submitter's embed info for displaying the preview.
        
        Args:
            template_id: DocuSeal template ID
            submitters: List of Submitter objects
            
        Returns:
            Dict with 'id', 'slug' and 'embed_src' for embedding, or None in mock mode
        """
        if DOCUSEAL_MOCK_MODE:
            return None
        
        try:
            result = cls.create_submission(
                template_id=template_id,
                submitters=submitters,
                send_email=False
            )
            
            # Get the first submitter's embed info
            submitter_results = result.get('submitters', [])
            if submitter_results:
                first = submitter_results[0]
                return {
                    'id': result.get('id'),  # Include submission ID for PDF fetching
                    'slug': first.get('slug', ''),
                    'embed_src': first.get('embed_src', f"https://docuseal.com/s/{first.get('slug', '')}")
                }
            
            return None
            
        except DocuSealAPIError:
            raise
        except Exception as e:
            logger.error(f"Error creating preview submission: {e}")
            return None
    
    @classmethod
    def get_submission_documents(cls, submission_id: int, merge: bool = False) -> List[Dict[str, Any]]:
        """
        Get documents (PDFs) from a submission.
        
        Works for both completed and partially-filled submissions.
        Each document includes a downloadable URL.
        
        Args:
            submission_id: DocuSeal submission ID
            merge: If True, merges all documents into a single PDF
            
        Returns:
            List of document objects with 'name' and 'url' for download
        """
        if DOCUSEAL_MOCK_MODE:
            return [{
                'name': f'mock_document_{submission_id}.pdf',
                'url': f'https://docuseal.com/downloads/mock/{submission_id}.pdf'
            }]
        
        try:
            # Build URL with optional merge parameter
            url = f"{DOCUSEAL_API_URL}/submissions/{submission_id}/documents"
            if merge:
                url += "?merge=true"
            
            response = requests.get(
                url,
                headers=cls._get_headers(),
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            error_body = None
            status_code = None
            
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                try:
                    error_body = e.response.text
                except Exception:
                    pass
            
            logger.error(f"Failed to get submission documents: {e}")
            if error_body:
                logger.error(f"Response body: {error_body}")
            
            raise DocuSealAPIError(
                f"Failed to get submission documents: {e}",
                status_code=status_code,
                response_body=error_body
            )
    
    @classmethod
    def merge_templates(
        cls,
        template_ids: List[int],
        name: str,
        roles: Optional[List[str]] = None,
        external_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Merge multiple templates into one combined template.
        
        Args:
            template_ids: List of template IDs to merge
            name: Name for the merged template
            roles: Optional unified roles for the merged template
            external_id: Optional external ID for tracking
            
        Returns:
            Merged template data with new template ID
        """
        if DOCUSEAL_MOCK_MODE:
            return cls._mock_merged_template(template_ids, name)
        
        payload = {
            'template_ids': template_ids,
            'name': name
        }
        
        if roles:
            payload['submitters'] = [{'name': role} for role in roles]
        
        if external_id:
            payload['external_id'] = external_id
        
        try:
            response = requests.post(
                f"{DOCUSEAL_API_URL}/templates/merge",
                headers=cls._get_headers(),
                json=payload,
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            error_body = None
            status_code = None
            
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                try:
                    error_body = e.response.text
                except Exception:
                    pass
            
            logger.error(f"DocuSeal merge templates failed: {e}")
            if error_body:
                logger.error(f"Response body: {error_body}")
            
            raise DocuSealAPIError(
                f"Failed to merge templates: {e}",
                status_code=status_code,
                response_body=error_body
            )
    
    @classmethod
    def _mock_template(cls, template_id: int) -> Dict[str, Any]:
        """Return mock template data for testing."""
        return {
            'id': template_id,
            'name': f'Mock Template {template_id}',
            'submitters': [
                {'name': 'Seller', 'uuid': 'mock-seller-uuid'},
                {'name': 'Broker', 'uuid': 'mock-broker-uuid'}
            ],
            'fields': []
        }
    
    @classmethod
    def _mock_merged_template(cls, template_ids: List[int], name: str) -> Dict[str, Any]:
        """Return mock merged template data for testing."""
        return {
            'id': 99999,
            'name': name,
            'submitters': [
                {'name': 'Seller', 'uuid': 'mock-seller-uuid'},
                {'name': 'Broker', 'uuid': 'mock-broker-uuid'}
            ],
            'fields': [],
            'source_template_ids': template_ids
        }
    
    @classmethod
    def _mock_submission(
        cls,
        template_id: int,
        submitters: List[Submitter]
    ) -> Dict[str, Any]:
        """Return mock submission data for testing."""
        import uuid
        
        submitter_results = []
        for i, sub in enumerate(submitters):
            mock_slug = f"mock-{uuid.uuid4().hex[:8]}"
            submitter_results.append({
                'id': i + 1,
                'submission_id': 12345,
                'email': sub.email,
                'role': sub.role,
                'slug': mock_slug,
                'embed_src': f"https://docuseal.com/s/{mock_slug}",
                'status': 'pending'
            })
        
        return {
            'id': 12345,
            'submitters': submitter_results
        }
    
    @classmethod
    def _mock_submission_from_pdf(
        cls,
        document_name: str,
        submitters: List[Submitter]
    ) -> Dict[str, Any]:
        """Return mock submission data for PDF upload testing."""
        import uuid
        
        submitter_results = []
        for i, sub in enumerate(submitters):
            mock_slug = f"mock-pdf-{uuid.uuid4().hex[:8]}"
            submitter_results.append({
                'id': i + 1,
                'submission_id': 12346,
                'email': sub.email,
                'role': sub.role,
                'slug': mock_slug,
                'embed_src': f"https://docuseal.com/s/{mock_slug}",
                'status': 'pending'
            })
        
        return {
            'id': 12346,
            'submitters': submitter_results
        }

