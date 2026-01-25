"""
Gmail Integration Routes

Handles OAuth flow for Gmail connection and integration management.
"""

from flask import Blueprint, redirect, url_for, flash, request, session, jsonify
from flask_login import login_required, current_user
import secrets
import logging

from models import db, UserEmailIntegration
from services import gmail_service

logger = logging.getLogger(__name__)

gmail_bp = Blueprint('gmail', __name__, url_prefix='/integrations/gmail')


@gmail_bp.route('/connect')
@login_required
def connect():
    """
    Initiate Gmail OAuth flow.
    Redirects user to Google's consent screen.
    """
    try:
        # Generate CSRF state token
        state = secrets.token_urlsafe(32)
        session['gmail_oauth_state'] = state
        
        # Get authorization URL
        auth_url = gmail_service.get_oauth_url(state)
        
        logger.info(f"User {current_user.id} initiating Gmail OAuth")
        return redirect(auth_url)
        
    except ValueError as e:
        logger.error(f"Gmail OAuth configuration error: {e}")
        flash('Gmail integration is not configured. Please contact support.', 'error')
        return redirect(url_for('auth.view_user_profile'))
    except Exception as e:
        logger.exception(f"Error initiating Gmail OAuth: {e}")
        flash('Unable to connect to Gmail. Please try again.', 'error')
        return redirect(url_for('auth.view_user_profile'))


@gmail_bp.route('/callback')
@login_required
def callback():
    """
    Handle OAuth callback from Google.
    Exchange authorization code for tokens and store integration.
    """
    # Verify state token (CSRF protection)
    state = request.args.get('state')
    stored_state = session.pop('gmail_oauth_state', None)
    
    if not state or state != stored_state:
        logger.warning(f"Invalid OAuth state for user {current_user.id}")
        flash('Invalid authentication state. Please try again.', 'error')
        return redirect(url_for('auth.view_user_profile'))
    
    # Check for errors from Google
    error = request.args.get('error')
    if error:
        logger.warning(f"OAuth error for user {current_user.id}: {error}")
        if error == 'access_denied':
            flash('Gmail access was denied. You can try again anytime.', 'info')
        else:
            flash(f'Gmail connection failed: {error}', 'error')
        return redirect(url_for('auth.view_user_profile'))
    
    # Get authorization code
    code = request.args.get('code')
    if not code:
        flash('No authorization code received. Please try again.', 'error')
        return redirect(url_for('auth.view_user_profile'))
    
    try:
        # Exchange code for tokens
        token_data = gmail_service.exchange_code_for_tokens(code)
        
        # Check if integration already exists
        integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
        
        if integration:
            # Update existing integration
            integration.connected_email = token_data['email']
            integration.access_token_encrypted = gmail_service.encrypt_token(token_data['access_token'])
            integration.refresh_token_encrypted = gmail_service.encrypt_token(token_data['refresh_token'])
            integration.token_expires_at = token_data['expires_at']
            integration.sync_enabled = True
            integration.sync_status = 'pending'
            integration.sync_error = None
        else:
            # Create new integration
            integration = UserEmailIntegration(
                user_id=current_user.id,
                organization_id=current_user.organization_id,
                provider='gmail',
                connected_email=token_data['email'],
                access_token_encrypted=gmail_service.encrypt_token(token_data['access_token']),
                refresh_token_encrypted=gmail_service.encrypt_token(token_data['refresh_token']),
                token_expires_at=token_data['expires_at'],
                sync_enabled=True,
                sync_status='pending'
            )
            db.session.add(integration)
        
        # Check if user was enabling calendar sync (came from toggle flow)
        enable_calendar = session.pop('enable_calendar_after_reauth', False)
        if enable_calendar:
            integration.calendar_sync_enabled = True
        
        db.session.commit()
        logger.info(f"Gmail connected for user {current_user.id}: {token_data['email']}")
        
        # Show appropriate message
        if enable_calendar:
            flash(f'Gmail connected and Calendar sync enabled! Tasks will now sync to your Google Calendar.', 'success')
        else:
            flash(f'Gmail connected successfully! Syncing emails from {token_data["email"]}...', 'success')
        
        # Do initial sync
        try:
            result = gmail_service.fetch_emails_for_user(integration, initial=True)
            if result['emails_fetched'] > 0:
                flash(f'Synced {result["emails_fetched"]} emails matching {result["contacts_matched"]} contacts.', 'success')
            else:
                flash('No emails found matching your contacts. New emails will sync automatically.', 'info')
        except Exception as sync_error:
            db.session.rollback()  # Rollback any pending transaction
            logger.error(f"Initial sync failed for user {current_user.id}: {sync_error}")
            flash('Gmail connected but initial sync had issues. Emails will sync in background.', 'warning')
        
        return redirect(url_for('auth.view_user_profile'))
        
    except Exception as e:
        db.session.rollback()  # Rollback any pending transaction
        logger.exception(f"Error completing Gmail OAuth: {e}")
        flash('Failed to complete Gmail connection. Please try again.', 'error')
        return redirect(url_for('auth.view_user_profile'))


@gmail_bp.route('/disconnect', methods=['POST'])
@login_required
def disconnect():
    """
    Disconnect Gmail integration.
    Disables sync but keeps previously synced emails.
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if integration:
        integration.sync_enabled = False
        integration.access_token_encrypted = None
        integration.refresh_token_encrypted = None
        integration.token_expires_at = None
        integration.sync_status = 'pending'
        db.session.commit()
        
        logger.info(f"Gmail disconnected for user {current_user.id}")
        flash('Gmail disconnected. Previously synced emails are still available.', 'info')
    else:
        flash('No Gmail integration found.', 'info')
    
    return redirect(url_for('auth.view_user_profile'))


@gmail_bp.route('/status')
@login_required
def status():
    """
    Get Gmail integration status (JSON endpoint for AJAX).
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration or not integration.sync_enabled:
        return jsonify({
            'connected': False
        })
    
    return jsonify({
        'connected': True,
        'email': integration.connected_email,
        'sync_status': integration.sync_status,
        'last_sync_at': integration.last_sync_at.isoformat() if integration.last_sync_at else None,
        'sync_error': integration.sync_error
    })


@gmail_bp.route('/resync', methods=['POST'])
@login_required
def resync():
    """
    Manually trigger email resync.
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration or not integration.sync_enabled:
        return jsonify({'success': False, 'error': 'Gmail not connected'}), 400
    
    try:
        result = gmail_service.fetch_emails_for_user(integration, initial=False)
        return jsonify({
            'success': True,
            'emails_fetched': result['emails_fetched'],
            'contacts_matched': result['contacts_matched'],
            'errors': result['errors']
        })
    except Exception as e:
        logger.exception(f"Resync failed for user {current_user.id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@gmail_bp.route('/calendar/toggle', methods=['POST'])
@login_required
def toggle_calendar_sync():
    """
    Enable or disable Google Calendar sync for tasks.
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration or not integration.sync_enabled:
        flash('Please connect Gmail first to enable calendar sync.', 'warning')
        return redirect(url_for('auth.view_user_profile'))
    
    try:
        # Check if user has calendar scope
        from services import calendar_service
        
        # Toggle the setting
        new_state = not integration.calendar_sync_enabled
        
        if new_state:
            # Enabling - check if we have calendar access
            has_calendar_scope = calendar_service.check_calendar_scope(integration)
            
            if not has_calendar_scope:
                # Need to re-authorize with calendar scope
                flash('Please reconnect your Google account to grant calendar permissions.', 'warning')
                # Set a flag so callback knows to enable calendar sync after re-auth
                session['enable_calendar_after_reauth'] = True
                # Trigger re-auth
                state = secrets.token_urlsafe(32)
                session['gmail_oauth_state'] = state
                auth_url = gmail_service.get_oauth_url(state)
                return redirect(auth_url)
        
        integration.calendar_sync_enabled = new_state
        db.session.commit()
        
        if new_state:
            flash('Google Calendar sync enabled. New tasks will appear in your calendar.', 'success')
        else:
            flash('Google Calendar sync disabled.', 'info')
        
        logger.info(f"User {current_user.id} {'enabled' if new_state else 'disabled'} calendar sync")
        
    except Exception as e:
        logger.exception(f"Failed to toggle calendar sync for user {current_user.id}: {e}")
        flash('Failed to update calendar sync settings. Please try again.', 'error')
    
    return redirect(url_for('auth.view_user_profile'))


@gmail_bp.route('/calendar/status')
@login_required
def calendar_status():
    """
    Get Google Calendar sync status (JSON endpoint).
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration or not integration.sync_enabled:
        return jsonify({
            'enabled': False,
            'available': False,
            'reason': 'gmail_not_connected'
        })
    
    # Check calendar scope
    from services import calendar_service
    has_scope = calendar_service.check_calendar_scope(integration)
    
    return jsonify({
        'enabled': integration.calendar_sync_enabled,
        'available': has_scope,
        'reason': None if has_scope else 'needs_reauth'
    })


@gmail_bp.route('/send', methods=['POST'])
@login_required
def send_email():
    """
    Send an email via Gmail API.
    
    Expected JSON body:
    {
        "to": ["email@example.com"],
        "cc": ["cc@example.com"],  # optional
        "bcc": ["bcc@example.com"],  # optional
        "subject": "Subject line",
        "body": "<p>HTML body content</p>",
        "contact_id": 123,  # optional - for logging to contact history
        "reply_to_message_id": "msg123",  # optional - for threading
        "thread_id": "thread123"  # optional - for threading
    }
    
    Files should be sent as multipart/form-data with field name "attachments"
    """
    from models import Contact
    from services.tenant_service import org_query
    
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration or not integration.sync_enabled:
        return jsonify({
            'success': False,
            'error': 'Gmail not connected. Please connect your Gmail account first.'
        }), 400
    
    # Check if we have send scope (users who connected before this feature need to reauth)
    # We'll attempt the send and handle the error if scope is missing
    
    # Get form data (supports both JSON and multipart)
    if request.content_type and 'multipart/form-data' in request.content_type:
        # Multipart form with attachments
        data = request.form.to_dict()
        # Parse JSON fields that may have been stringified
        import json
        to_emails = json.loads(data.get('to', '[]'))
        cc_emails = json.loads(data.get('cc', '[]')) if data.get('cc') else []
        bcc_emails = json.loads(data.get('bcc', '[]')) if data.get('bcc') else []
        subject = data.get('subject', '')
        body_html = data.get('body', '')
        contact_id = int(data.get('contact_id')) if data.get('contact_id') else None
        reply_to_message_id = data.get('reply_to_message_id')
        thread_id = data.get('thread_id')
        
        # Process attachments
        attachments = []
        files = request.files.getlist('attachments')
        for file in files:
            if file.filename:
                content = file.read()
                # Limit attachment size (10MB per file)
                if len(content) > 10 * 1024 * 1024:
                    return jsonify({
                        'success': False,
                        'error': f'Attachment "{file.filename}" exceeds 10MB limit'
                    }), 400
                
                attachments.append({
                    'filename': file.filename,
                    'content': content,
                    'mime_type': file.content_type or 'application/octet-stream'
                })
    else:
        # JSON request (no attachments)
        data = request.get_json() or {}
        to_emails = data.get('to', [])
        cc_emails = data.get('cc', [])
        bcc_emails = data.get('bcc', [])
        subject = data.get('subject', '')
        body_html = data.get('body', '')
        contact_id = data.get('contact_id')
        reply_to_message_id = data.get('reply_to_message_id')
        thread_id = data.get('thread_id')
        attachments = []
    
    # Validate required fields
    if not to_emails:
        return jsonify({
            'success': False,
            'error': 'At least one recipient is required'
        }), 400
    
    if not subject:
        return jsonify({
            'success': False,
            'error': 'Subject is required'
        }), 400
    
    if not body_html:
        return jsonify({
            'success': False,
            'error': 'Message body is required'
        }), 400
    
    # Validate contact if provided
    if contact_id:
        contact = org_query(Contact).filter_by(id=contact_id).first()
        if not contact:
            return jsonify({
                'success': False,
                'error': 'Contact not found'
            }), 404
    
    try:
        # Send the email
        result = gmail_service.send_email(
            integration=integration,
            to_emails=to_emails,
            subject=subject,
            body_html=body_html,
            cc_emails=cc_emails if cc_emails else None,
            bcc_emails=bcc_emails if bcc_emails else None,
            attachments=attachments if attachments else None,
            reply_to_message_id=reply_to_message_id,
            thread_id=thread_id
        )
        
        if not result['success']:
            # Check if it's an auth issue (missing send scope)
            error_str = str(result.get('error', '')).lower()
            auth_errors = ['403', 'insufficient', 'invalid_scope', 'invalid_grant', 'unauthorized']
            if any(err in error_str for err in auth_errors):
                return jsonify({
                    'success': False,
                    'error': 'Gmail send permission not granted. Please reconnect your Gmail account to enable email sending.',
                    'needs_reauth': True
                }), 403
            return jsonify(result), 500
        
        # Log to contact history if contact_id provided
        if contact_id:
            # Use the full body (with signature) from the result for logging
            logged_body = result.get('body_html', body_html)
            gmail_service.log_sent_email(
                integration=integration,
                contact_id=contact_id,
                message_id=result['message_id'],
                thread_id=result['thread_id'],
                subject=subject,
                to_emails=to_emails,
                cc_emails=cc_emails,
                body_html=logged_body,
                has_attachments=len(attachments) > 0
            )
        
        logger.info(f"User {current_user.id} sent email to {to_emails}")
        
        return jsonify({
            'success': True,
            'message_id': result['message_id'],
            'thread_id': result['thread_id']
        })
        
    except Exception as e:
        logger.exception(f"Error sending email for user {current_user.id}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@gmail_bp.route('/send/check')
@login_required
def check_send_capability():
    """
    Check if user can send emails via Gmail.
    Returns status and connected email address.
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration or not integration.sync_enabled:
        return jsonify({
            'can_send': False,
            'reason': 'not_connected',
            'message': 'Gmail not connected'
        })
    
    return jsonify({
        'can_send': True,
        'email': integration.connected_email,
        'reason': None,
        'message': None
    })


@gmail_bp.route('/contact/<int:contact_id>/files')
@login_required
def get_contact_files_for_email(contact_id):
    """
    Get list of files attached to a contact for email attachment picker.
    Returns file metadata (not content) for display in the modal.
    """
    from models import Contact, ContactFile
    from services.tenant_service import org_query
    
    # Verify contact exists and user has access
    contact = org_query(Contact).filter_by(id=contact_id).first()
    if not contact:
        return jsonify({'success': False, 'error': 'Contact not found'}), 404
    
    # Get all files for this contact
    files = ContactFile.query.filter_by(contact_id=contact_id).order_by(ContactFile.created_at.desc()).all()
    
    file_list = []
    for f in files:
        file_list.append({
            'id': f.id,
            'filename': f.original_filename,
            'file_type': f.file_type,
            'file_size': f.file_size,
            'file_size_display': f.size_display,
            'extension': f.file_extension,
            'is_image': f.is_image,
            'created_at': f.created_at.isoformat() if f.created_at else None
        })
    
    return jsonify({
        'success': True,
        'files': file_list,
        'contact_name': f"{contact.first_name} {contact.last_name}"
    })


@gmail_bp.route('/contact-file/<int:file_id>/download')
@login_required
def download_contact_file_for_email(file_id):
    """
    Download a contact file's content for email attachment.
    Returns the file as bytes with appropriate headers.
    """
    from models import ContactFile
    from services.tenant_service import org_query
    from services import supabase_storage
    from flask import Response
    
    # Get the file record
    file_record = org_query(ContactFile).filter_by(id=file_id).first()
    if not file_record:
        return jsonify({'success': False, 'error': 'File not found'}), 404
    
    try:
        # Download from Supabase
        client = supabase_storage.get_supabase_client()
        file_data = client.storage.from_(supabase_storage.CONTACT_FILES_BUCKET).download(file_record.storage_path)
        
        # Return as downloadable response
        return Response(
            file_data,
            mimetype=file_record.file_type or 'application/octet-stream',
            headers={
                'Content-Disposition': f'attachment; filename="{file_record.original_filename}"',
                'X-File-Name': file_record.original_filename,
                'X-File-Type': file_record.file_type or 'application/octet-stream',
                'X-File-Size': str(file_record.file_size or len(file_data))
            }
        )
    except Exception as e:
        logger.error(f"Error downloading contact file {file_id}: {e}")
        return jsonify({'success': False, 'error': 'Failed to download file'}), 500
