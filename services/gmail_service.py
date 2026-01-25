"""
Gmail Integration Service

Handles OAuth flow, token management, and Gmail API interactions.
Provides send-only email functionality (no inbox sync to avoid restricted scopes).

Usage:
    from services.gmail_service import (
        get_oauth_url,
        exchange_code_for_tokens,
        send_email
    )
"""

import logging
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from cryptography.fernet import Fernet
import bleach

from config import Config

logger = logging.getLogger(__name__)

# Gmail API scopes - send-only (no restricted scopes to avoid paid security assessment)
# OpenID email scope used to get user's email address during OAuth
GMAIL_SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',  # Get email from ID token
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/calendar.events'
]

# OAuth redirect URIs
REDIRECT_URI_LOCAL = 'http://127.0.0.1:5011/integrations/gmail/callback'
REDIRECT_URI_PROD = 'https://www.origentechnolog.com/integrations/gmail/callback'

# HTML sanitization for email body display and signature
ALLOWED_TAGS = ['p', 'br', 'b', 'i', 'strong', 'em', 'a', 'ul', 'ol', 'li', 'blockquote', 'div', 'span', 'img']
ALLOWED_ATTRS = {
    'a': ['href', 'title'],
    'img': ['src', 'alt', 'width', 'height']  # NO style - security risk
}


def _get_redirect_uri() -> str:
    """Get the appropriate redirect URI based on environment."""
    if Config.FLASK_ENV == 'production':
        return REDIRECT_URI_PROD
    return REDIRECT_URI_LOCAL


def _get_fernet() -> Fernet:
    """Get Fernet instance for token encryption."""
    key = Config.GMAIL_TOKEN_ENCRYPTION_KEY
    if not key:
        raise ValueError("GMAIL_TOKEN_ENCRYPTION_KEY not configured")
    return Fernet(key.encode())


def encrypt_token(token: str) -> str:
    """Encrypt an OAuth token for storage."""
    f = _get_fernet()
    return f.encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt an OAuth token from storage."""
    f = _get_fernet()
    return f.decrypt(encrypted_token.encode()).decode()


def get_oauth_url(state: str) -> str:
    """
    Generate Google OAuth authorization URL.
    
    Args:
        state: CSRF state token (store in session for verification)
    
    Returns:
        Authorization URL to redirect user to
    """
    if not Config.GOOGLE_CLIENT_ID or not Config.GOOGLE_CLIENT_SECRET:
        raise ValueError("Google OAuth credentials not configured")
    
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": Config.GOOGLE_CLIENT_ID,
                "client_secret": Config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_get_redirect_uri()]
            }
        },
        scopes=GMAIL_SCOPES
    )
    flow.redirect_uri = _get_redirect_uri()
    
    auth_url, _ = flow.authorization_url(
        state=state,
        access_type='offline',  # Get refresh token
        include_granted_scopes='false',  # Don't include old restricted scopes
        prompt='consent'  # Always show consent to get refresh token
    )
    
    return auth_url


def exchange_code_for_tokens(code: str) -> Dict:
    """
    Exchange authorization code for access/refresh tokens.
    
    Args:
        code: Authorization code from OAuth callback
    
    Returns:
        Dict with keys: access_token, refresh_token, expires_at, email
    """
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
    
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": Config.GOOGLE_CLIENT_ID,
                "client_secret": Config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_get_redirect_uri()]
            }
        },
        scopes=GMAIL_SCOPES
    )
    flow.redirect_uri = _get_redirect_uri()
    
    # Exchange code for tokens
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
    # Get user's email from ID token (using OpenID scope instead of gmail.readonly)
    # The ID token is included when openid scope is requested
    email = None
    if credentials.id_token:
        try:
            # Verify and decode the ID token
            id_info = id_token.verify_oauth2_token(
                credentials.id_token,
                google_requests.Request(),
                Config.GOOGLE_CLIENT_ID
            )
            email = id_info.get('email')
            logger.info(f"Got email from ID token: {email}")
        except Exception as e:
            logger.warning(f"Failed to decode ID token: {e}")
    
    # Fallback: fetch from userinfo endpoint if ID token didn't work
    if not email:
        try:
            import requests
            userinfo_response = requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {credentials.token}'}
            )
            if userinfo_response.ok:
                userinfo = userinfo_response.json()
                email = userinfo.get('email')
                logger.info(f"Got email from userinfo endpoint: {email}")
        except Exception as e:
            logger.warning(f"Failed to get email from userinfo: {e}")
    
    if not email:
        raise ValueError("Could not retrieve user's email address from OAuth response")
    
    # Calculate expiration time
    expires_at = datetime.utcnow() + timedelta(seconds=credentials.expiry.timestamp() - datetime.now().timestamp()) if credentials.expiry else datetime.utcnow() + timedelta(hours=1)
    
    return {
        'access_token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'expires_at': expires_at,
        'email': email
    }


def refresh_access_token(integration) -> bool:
    """
    Refresh expired access token using refresh token.
    
    Args:
        integration: UserEmailIntegration model instance
    
    Returns:
        True if refresh successful, False otherwise
    """
    from models import db
    
    try:
        refresh_token = decrypt_token(integration.refresh_token_encrypted)
        
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=Config.GOOGLE_CLIENT_ID,
            client_secret=Config.GOOGLE_CLIENT_SECRET,
            scopes=GMAIL_SCOPES
        )
        
        # Force refresh
        from google.auth.transport.requests import Request
        credentials.refresh(Request())
        
        # Update integration with new token
        integration.access_token_encrypted = encrypt_token(credentials.token)
        integration.token_expires_at = datetime.utcnow() + timedelta(hours=1)
        integration.sync_status = 'active'
        integration.sync_error = None
        db.session.commit()
        
        logger.info(f"Refreshed access token for user {integration.user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to refresh token for user {integration.user_id}: {e}")
        integration.sync_status = 'error'
        integration.sync_error = str(e)
        db.session.commit()
        return False


def _get_gmail_service(integration):
    """
    Get authenticated Gmail API service.
    
    Args:
        integration: UserEmailIntegration model instance
    
    Returns:
        Gmail API service object
    """
    # Check if token needs refresh
    if integration.token_expires_at and integration.token_expires_at < datetime.utcnow():
        if not refresh_access_token(integration):
            raise Exception("Failed to refresh access token")
    
    access_token = decrypt_token(integration.access_token_encrypted)
    
    credentials = Credentials(
        token=access_token,
        refresh_token=decrypt_token(integration.refresh_token_encrypted),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=Config.GOOGLE_CLIENT_ID,
        client_secret=Config.GOOGLE_CLIENT_SECRET,
        scopes=GMAIL_SCOPES
    )
    
    return build('gmail', 'v1', credentials=credentials)


def sanitize_html(html_content: str) -> str:
    """Sanitize HTML content for safe display."""
    if not html_content:
        return ''
    return bleach.clean(html_content, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)


def get_email_threads_for_contact(contact_id: int, user_id: int) -> List[Dict]:
    """
    Get email threads for a contact, grouped by thread_id.
    
    Args:
        contact_id: Contact ID
        user_id: Current user ID (for filtering)
    
    Returns:
        List of thread dicts with messages
    """
    from models import ContactEmail
    
    # Get all emails for this contact from this user's sync
    emails = ContactEmail.query.filter_by(
        contact_id=contact_id,
        user_id=user_id
    ).order_by(ContactEmail.sent_at.asc()).all()
    
    # Group by thread
    threads_map = {}
    for email in emails:
        thread_id = email.gmail_thread_id or email.gmail_message_id
        if thread_id not in threads_map:
            threads_map[thread_id] = {
                'thread_id': thread_id,
                'subject': email.subject,
                'messages': [],
                'latest_at': email.sent_at
            }
        
        threads_map[thread_id]['messages'].append(email.to_dict())
        
        if email.sent_at and (not threads_map[thread_id]['latest_at'] or 
                              email.sent_at > threads_map[thread_id]['latest_at']):
            threads_map[thread_id]['latest_at'] = email.sent_at
    
    # Convert to list and sort by latest message
    threads = list(threads_map.values())
    threads.sort(key=lambda t: t['latest_at'] or datetime.min, reverse=True)
    
    # Add message count
    for thread in threads:
        thread['message_count'] = len(thread['messages'])
        thread['latest_at'] = thread['latest_at'].isoformat() if thread['latest_at'] else None
    
    return threads


def send_email(integration, to_emails: List[str], subject: str, body_html: str,
               cc_emails: List[str] = None, bcc_emails: List[str] = None,
               attachments: List[Dict] = None, reply_to_message_id: str = None,
               thread_id: str = None, include_signature: bool = True) -> Dict:
    """
    Send an email via Gmail API.
    
    Args:
        integration: UserEmailIntegration model instance
        to_emails: List of recipient email addresses
        subject: Email subject
        body_html: HTML body content
        cc_emails: Optional list of CC recipients
        bcc_emails: Optional list of BCC recipients
        attachments: Optional list of dicts with keys: filename, content (bytes), mime_type
        reply_to_message_id: Optional message ID to reply to (for threading)
        thread_id: Optional thread ID to add message to existing thread
        include_signature: Whether to append CRM signature (default True)
    
    Returns:
        Dict with: message_id, thread_id, success, error
    
    Raises:
        Exception if send fails
    """
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email.mime.image import MIMEImage
    from email import encoders
    import mimetypes
    
    # Check if user needs to reauth with new scopes
    if integration.oauth_scope_version is None or integration.oauth_scope_version < 2:
        return {
            'success': False,
            'message_id': None,
            'thread_id': None,
            'error': 'Your Gmail connection needs to be updated. Please reconnect your Gmail account.',
            'needs_reauth': True
        }
    
    try:
        service = _get_gmail_service(integration)
        
        # Build the full email body with optional CRM signature
        full_body_html = body_html
        signature_images_to_embed = []
        
        if include_signature and integration.signature_html:
            # Get signature HTML and prepare CID image references
            signature_html = integration.signature_html
            sig_images = integration.get_signature_images_list()
            
            # Find images referenced in the signature HTML (cid:content_id format)
            # and add width/height attributes for proper email rendering
            import re
            for img in sig_images:
                content_id = img.get('content_id')
                if content_id and f'cid:{content_id}' in signature_html:
                    # This image is referenced in the signature, prepare for embedding
                    signature_images_to_embed.append(img)
                    
                    # Add width constraint and display:block to the image tag
                    # Find the img tag with this cid and add/update width attribute
                    width = min(img.get('width', 200), 200)  # Max 200px
                    old_tag_pattern = rf'<img[^>]*src="cid:{re.escape(content_id)}"[^>]*>'
                    
                    def add_width_to_img(match):
                        tag = match.group(0)
                        # Remove existing width if present
                        tag = re.sub(r'\s+width="[^"]*"', '', tag)
                        # Add width before the closing >
                        tag = tag.replace('>', f' width="{width}" style="display:block;max-width:{width}px;">')
                        return tag
                    
                    signature_html = re.sub(old_tag_pattern, add_width_to_img, signature_html)
            
            # Add signature with separator
            full_body_html = f"{body_html}<br><br>--<br>{signature_html}"
        
        # Build proper MIME structure for CID image embedding:
        # multipart/mixed
        # ├── multipart/related
        # │   ├── text/html (body + signature with cid: references)
        # │   └── image/* (inline signature images with Content-ID)
        # └── attachment/* (regular file attachments)
        
        # Determine if we need the full multipart/mixed structure
        has_file_attachments = bool(attachments)
        has_inline_images = bool(signature_images_to_embed)
        
        if has_file_attachments or has_inline_images:
            # Need multipart/mixed as outer container
            message = MIMEMultipart('mixed')
        else:
            # Simple HTML email, no attachments
            message = MIMEMultipart('alternative')
        
        message['From'] = integration.connected_email
        message['To'] = ', '.join(to_emails)
        message['Subject'] = subject
        
        if cc_emails:
            message['Cc'] = ', '.join(cc_emails)
        if bcc_emails:
            message['Bcc'] = ', '.join(bcc_emails)
        
        # Add threading headers if replying
        if reply_to_message_id:
            message['In-Reply-To'] = reply_to_message_id
            message['References'] = reply_to_message_id
        
        # Build HTML content with inline images
        if has_inline_images:
            # Create multipart/related for HTML + inline images
            related_part = MIMEMultipart('related')
            
            # Add HTML body
            html_part = MIMEText(full_body_html, 'html', 'utf-8')
            related_part.attach(html_part)
            
            # Add inline signature images
            for img in signature_images_to_embed:
                content_id = img.get('content_id')
                bytes_b64 = img.get('bytes_b64')
                mime_type = img.get('mime_type', 'image/png')
                
                if content_id and bytes_b64:
                    try:
                        img_bytes = base64.b64decode(bytes_b64)
                        maintype, subtype = mime_type.split('/', 1)
                        img_part = MIMEImage(img_bytes, _subtype=subtype)
                        img_part.add_header('Content-ID', f'<{content_id}>')
                        img_part.add_header('Content-Disposition', 'inline', filename=img.get('filename', 'image.png'))
                        related_part.attach(img_part)
                    except Exception as e:
                        logger.warning(f"Failed to attach signature image {content_id}: {e}")
            
            message.attach(related_part)
        else:
            # No inline images, just attach HTML directly
            html_part = MIMEText(full_body_html, 'html', 'utf-8')
            message.attach(html_part)
        
        # Attach file attachments (after the related part)
        if attachments:
            for attachment in attachments:
                filename = attachment.get('filename', 'attachment')
                content = attachment.get('content')  # bytes
                mime_type = attachment.get('mime_type', 'application/octet-stream')
                
                if content:
                    maintype, subtype = mime_type.split('/', 1) if '/' in mime_type else ('application', 'octet-stream')
                    part = MIMEBase(maintype, subtype)
                    part.set_payload(content)
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', 'attachment', filename=filename)
                    message.attach(part)
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        
        # Build request body
        body = {'raw': raw_message}
        if thread_id:
            body['threadId'] = thread_id
        
        # Send message
        sent_message = service.users().messages().send(
            userId='me',
            body=body
        ).execute()
        
        logger.info(f"Email sent successfully. Message ID: {sent_message.get('id')}")
        
        return {
            'success': True,
            'message_id': sent_message.get('id'),
            'thread_id': sent_message.get('threadId'),
            'body_html': full_body_html,  # Return full body with signature for logging
            'error': None
        }
        
    except HttpError as e:
        error_msg = f"Gmail API error: {e.resp.status} - {e.reason}"
        logger.error(f"Failed to send email: {error_msg}")
        return {
            'success': False,
            'message_id': None,
            'thread_id': None,
            'error': error_msg
        }
        
    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Failed to send email: {error_msg}")
        return {
            'success': False,
            'message_id': None,
            'thread_id': None,
            'error': error_msg
        }


def _strip_html_tags(html: str) -> str:
    """Strip HTML tags and return plain text for snippets."""
    import re
    if not html:
        return ''
    # Add space before block-level elements to prevent text concatenation
    text = re.sub(r'<(p|div|br|li|h[1-6]|tr|td)[^>]*>', ' ', html, flags=re.IGNORECASE)
    # Add space after closing block elements
    text = re.sub(r'</(p|div|li|h[1-6]|tr|td|ul|ol)>', ' ', text, flags=re.IGNORECASE)
    # Replace <br> and <br/> with space
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
    # Remove remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode common HTML entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    # Collapse multiple whitespace into single space
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def log_sent_email(integration, contact_id: int, message_id: str, thread_id: str,
                   subject: str, to_emails: List[str], cc_emails: List[str],
                   body_html: str, has_attachments: bool) -> None:
    """
    Log a sent email to the ContactEmail table.
    
    Args:
        integration: UserEmailIntegration model instance
        contact_id: ID of the contact this email is associated with
        message_id: Gmail message ID
        thread_id: Gmail thread ID
        subject: Email subject
        to_emails: List of recipient emails
        cc_emails: List of CC recipients
        body_html: HTML body (will be sanitized)
        has_attachments: Whether email had attachments
    """
    from models import db, ContactEmail
    
    try:
        # Create plain text snippet from HTML
        plain_text = _strip_html_tags(body_html)
        
        contact_email = ContactEmail(
            organization_id=integration.organization_id,
            user_id=integration.user_id,
            contact_id=contact_id,
            gmail_message_id=message_id,
            gmail_thread_id=thread_id,
            subject=subject[:500] if subject else None,
            snippet=plain_text[:500] if plain_text else '',
            from_email=integration.connected_email,
            from_name='',  # Could pull from user profile
            to_emails=to_emails,
            cc_emails=cc_emails if cc_emails else None,
            direction='outbound',
            sent_at=datetime.utcnow(),
            has_attachments=has_attachments,
            body_text=plain_text,
            body_html=sanitize_html(body_html)
        )
        db.session.add(contact_email)
        db.session.commit()
        
        logger.info(f"Logged sent email {message_id} for contact {contact_id}")
        
    except Exception as e:
        logger.exception(f"Failed to log sent email: {e}")
        db.session.rollback()
