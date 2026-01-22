"""
Gmail Integration Service

Handles OAuth flow, token management, and Gmail API interactions.
Provides email sync functionality for contact email history.

Usage:
    from services.gmail_service import (
        get_oauth_url,
        exchange_code_for_tokens,
        sync_emails_for_user
    )
"""

import os
import logging
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from email.utils import parseaddr

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from cryptography.fernet import Fernet
import bleach

from config import Config

logger = logging.getLogger(__name__)

# Gmail API scopes - read-only
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# OAuth redirect URIs
REDIRECT_URI_LOCAL = 'http://127.0.0.1:5011/integrations/gmail/callback'
REDIRECT_URI_PROD = 'https://www.origentechnolog.com/integrations/gmail/callback'

# HTML sanitization for email body display
ALLOWED_TAGS = ['p', 'br', 'b', 'i', 'strong', 'em', 'a', 'ul', 'ol', 'li', 'blockquote', 'div', 'span']
ALLOWED_ATTRS = {'a': ['href', 'title']}


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
        include_granted_scopes='true',
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
    
    # Get user's email address
    service = build('gmail', 'v1', credentials=credentials)
    profile = service.users().getProfile(userId='me').execute()
    email = profile.get('emailAddress')
    
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


def _parse_email_address(email_str: str) -> Tuple[str, str]:
    """Parse email string to extract name and address."""
    name, address = parseaddr(email_str)
    return name or '', address or email_str


def _extract_body(payload: dict) -> Tuple[str, str]:
    """
    Extract plain text and HTML body from email payload.
    
    Returns:
        Tuple of (body_text, body_html)
    """
    body_text = ''
    body_html = ''
    
    def extract_parts(parts):
        nonlocal body_text, body_html
        for part in parts:
            mime_type = part.get('mimeType', '')
            body = part.get('body', {})
            data = body.get('data', '')
            
            if mime_type == 'text/plain' and data:
                body_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            elif mime_type == 'text/html' and data:
                body_html = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            elif 'parts' in part:
                extract_parts(part['parts'])
    
    # Check if single part message
    body = payload.get('body', {})
    if body.get('data'):
        mime_type = payload.get('mimeType', '')
        data = body['data']
        decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
        if mime_type == 'text/html':
            body_html = decoded
        else:
            body_text = decoded
    
    # Check for multipart
    if 'parts' in payload:
        extract_parts(payload['parts'])
    
    return body_text, body_html


def sanitize_html(html_content: str) -> str:
    """Sanitize HTML content for safe display."""
    if not html_content:
        return ''
    return bleach.clean(html_content, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)


def fetch_emails_for_user(integration, initial: bool = False) -> Dict:
    """
    Fetch emails from Gmail for a user.
    
    Args:
        integration: UserEmailIntegration model instance
        initial: If True, fetch last 30 days. If False, use incremental sync.
    
    Returns:
        Dict with: emails_fetched (int), contacts_matched (int), errors (list)
    """
    from models import db, Contact, ContactEmail
    
    result = {
        'emails_fetched': 0,
        'contacts_matched': 0,
        'errors': []
    }
    
    try:
        service = _get_gmail_service(integration)
        
        # Build query
        if initial:
            # Initial sync: last 30 days
            after_date = (datetime.utcnow() - timedelta(days=Config.GMAIL_SYNC_DAYS)).strftime('%Y/%m/%d')
            query = f'after:{after_date}'
        else:
            # Incremental sync using history API
            if integration.last_history_id:
                try:
                    history = service.users().history().list(
                        userId='me',
                        startHistoryId=integration.last_history_id,
                        historyTypes=['messageAdded']
                    ).execute()
                    
                    message_ids = set()
                    for record in history.get('history', []):
                        for msg in record.get('messagesAdded', []):
                            message_ids.add(msg['message']['id'])
                    
                    if not message_ids:
                        logger.info(f"No new messages for user {integration.user_id}")
                        integration.last_sync_at = datetime.utcnow()
                        db.session.commit()
                        return result
                    
                    # Fetch these specific messages
                    for msg_id in message_ids:
                        _process_message(service, msg_id, integration, result)
                    
                    # Update history ID
                    integration.last_history_id = history.get('historyId')
                    integration.last_sync_at = datetime.utcnow()
                    integration.sync_status = 'active'
                    db.session.commit()
                    return result
                    
                except HttpError as e:
                    if e.resp.status == 404:
                        # History expired, do a full sync
                        logger.warning(f"History expired for user {integration.user_id}, doing full sync")
                        after_date = (datetime.utcnow() - timedelta(days=7)).strftime('%Y/%m/%d')
                        query = f'after:{after_date}'
                    else:
                        raise
            else:
                # No history ID, do initial sync
                after_date = (datetime.utcnow() - timedelta(days=Config.GMAIL_SYNC_DAYS)).strftime('%Y/%m/%d')
                query = f'after:{after_date}'
        
        # List messages matching query
        messages = []
        page_token = None
        
        while True:
            response = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=100,
                pageToken=page_token
            ).execute()
            
            messages.extend(response.get('messages', []))
            page_token = response.get('nextPageToken')
            
            # Limit initial sync to 50 emails for fast OAuth callback
            # Background job will sync the rest
            if not page_token or len(messages) >= 50:
                break
        
        logger.info(f"Found {len(messages)} messages for user {integration.user_id}")
        
        # Process each message
        for msg in messages:
            _process_message(service, msg['id'], integration, result)
        
        # Update sync status
        profile = service.users().getProfile(userId='me').execute()
        integration.last_history_id = profile.get('historyId')
        integration.last_sync_at = datetime.utcnow()
        integration.sync_status = 'active'
        db.session.commit()
        
    except Exception as e:
        logger.exception(f"Error fetching emails for user {integration.user_id}: {e}")
        result['errors'].append(str(e))
        try:
            db.session.rollback()  # Rollback any failed transaction first
            integration.sync_status = 'error'
            integration.sync_error = str(e)
            db.session.commit()
        except Exception:
            db.session.rollback()
    
    return result


def _process_message(service, msg_id: str, integration, result: Dict):
    """Process a single email message."""
    from models import db, Contact, ContactEmail
    
    try:
        # Note: We don't skip based on gmail_message_id alone anymore
        # The same email can be linked to multiple contacts
        
        # Get full message
        msg = service.users().messages().get(
            userId='me',
            id=msg_id,
            format='full'
        ).execute()
        
        # Extract headers
        headers = {h['name'].lower(): h['value'] for h in msg['payload'].get('headers', [])}
        
        subject = headers.get('subject', '(No subject)')
        from_header = headers.get('from', '')
        to_header = headers.get('to', '')
        cc_header = headers.get('cc', '')
        date_header = headers.get('date', '')
        
        # Parse sender
        from_name, from_email = _parse_email_address(from_header)
        
        # Parse recipients
        to_emails = [_parse_email_address(e)[1] for e in to_header.split(',') if e.strip()]
        cc_emails = [_parse_email_address(e)[1] for e in cc_header.split(',') if e.strip()] if cc_header else []
        
        # Determine direction
        agent_email = integration.connected_email.lower()
        direction = 'outbound' if from_email.lower() == agent_email else 'inbound'
        
        # Collect all participant emails (excluding agent)
        participant_emails = set()
        if direction == 'outbound':
            participant_emails.update(e.lower() for e in to_emails if e.lower() != agent_email)
            participant_emails.update(e.lower() for e in cc_emails if e.lower() != agent_email)
        else:
            participant_emails.add(from_email.lower())
        
        # Match to contacts
        contacts = Contact.query.filter(
            Contact.organization_id == integration.organization_id,
            Contact.email.isnot(None)
        ).all()
        
        matched_contacts = [c for c in contacts if c.email and c.email.lower() in participant_emails]
        
        if not matched_contacts:
            # No matching contacts, skip this email
            return
        
        result['emails_fetched'] += 1
        
        # Extract body
        body_text, body_html = _extract_body(msg['payload'])
        
        # Check for attachments
        has_attachments = False
        if 'parts' in msg['payload']:
            for part in msg['payload']['parts']:
                if part.get('filename'):
                    has_attachments = True
                    break
        
        # Parse date
        sent_at = None
        if date_header:
            from email.utils import parsedate_to_datetime
            try:
                sent_at = parsedate_to_datetime(date_header)
                sent_at = sent_at.replace(tzinfo=None)  # Store as naive UTC
            except Exception:
                sent_at = datetime.utcnow()
        
        # Create ContactEmail for each matched contact
        for contact in matched_contacts:
            # Check if this specific contact-message combo exists
            existing = ContactEmail.query.filter_by(
                gmail_message_id=msg_id,
                contact_id=contact.id
            ).first()
            if existing:
                continue
            
            contact_email = ContactEmail(
                organization_id=integration.organization_id,
                user_id=integration.user_id,
                contact_id=contact.id,
                gmail_message_id=msg_id,
                gmail_thread_id=msg.get('threadId'),
                subject=subject[:500] if subject else None,
                snippet=msg.get('snippet', '')[:500],
                from_email=from_email,
                from_name=from_name,
                to_emails=to_emails,
                cc_emails=cc_emails if cc_emails else None,
                direction=direction,
                sent_at=sent_at,
                has_attachments=has_attachments,
                body_text=body_text,
                body_html=sanitize_html(body_html)
            )
            db.session.add(contact_email)
            result['contacts_matched'] += 1
        
        db.session.commit()
        
    except Exception as e:
        # Check if this is a 404 "not found" error (message was deleted)
        error_str = str(e)
        if '404' in error_str or 'not found' in error_str.lower():
            logger.debug(f"Message {msg_id} no longer exists (likely deleted) - skipping")
        else:
            logger.error(f"Error processing message {msg_id}: {e}")
            result['errors'].append(f"Message {msg_id}: {str(e)}")


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
