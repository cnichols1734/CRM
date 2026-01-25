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
            integration.sync_status = 'active'
            integration.sync_error = None
            integration.oauth_scope_version = 2  # Mark as using new send-only scopes
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
                sync_status='active',
                oauth_scope_version=2  # New connections use send-only scopes
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
            flash(f'Gmail connected successfully! You can now send emails from {token_data["email"]}.', 'success')
        
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
        'needs_reauth': integration.needs_reauth,
        'has_signature': integration.has_signature
    })


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
    
    # Check if user needs to reconnect with new scopes
    if integration.needs_reauth:
        return jsonify({
            'can_send': False,
            'email': integration.connected_email,
            'reason': 'needs_reauth',
            'message': 'Please reconnect your Gmail account to continue sending emails.'
        })
    
    return jsonify({
        'can_send': True,
        'email': integration.connected_email,
        'has_signature': integration.has_signature,
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


# =============================================================================
# SIGNATURE MANAGEMENT ROUTES
# =============================================================================

@gmail_bp.route('/signature')
@login_required
def get_signature():
    """
    Get current email signature for the user.
    Returns signature HTML and image metadata.
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration:
        return jsonify({
            'success': True,
            'signature_html': '',
            'signature_images': []
        })
    
    return jsonify({
        'success': True,
        'signature_html': integration.signature_html or '',
        'signature_images': integration.get_signature_images_list()
    })


@gmail_bp.route('/signature', methods=['POST'])
@login_required
def save_signature():
    """
    Save email signature HTML.
    Images should be uploaded separately via /signature/image endpoint.
    
    Expected JSON body:
    {
        "signature_html": "<p>My signature with <img src='cid:img_123'></p>"
    }
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration:
        return jsonify({
            'success': False,
            'error': 'Gmail not connected. Please connect Gmail first.'
        }), 400
    
    data = request.get_json() or {}
    signature_html = data.get('signature_html', '')
    
    # Save the signature HTML
    integration.signature_html = signature_html
    db.session.commit()
    
    logger.info(f"User {current_user.id} saved email signature")
    
    return jsonify({
        'success': True,
        'message': 'Signature saved successfully'
    })


@gmail_bp.route('/signature/image', methods=['POST'])
@login_required
def upload_signature_image():
    """
    Upload and normalize a signature image.
    Resizes to max 600px width, compresses to max 250KB.
    Max 3 images per signature.
    
    Returns the content_id to use in signature HTML (cid:content_id).
    """
    import io
    import base64
    import uuid
    from PIL import Image
    
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration:
        return jsonify({
            'success': False,
            'error': 'Gmail not connected. Please connect Gmail first.'
        }), 400
    
    # Check max images limit
    current_images = integration.get_signature_images_list()
    if len(current_images) >= 3:
        return jsonify({
            'success': False,
            'error': 'Maximum 3 signature images allowed. Remove an existing image first.'
        }), 400
    
    # Get uploaded file
    if 'image' not in request.files:
        return jsonify({
            'success': False,
            'error': 'No image file provided'
        }), 400
    
    file = request.files['image']
    if not file.filename:
        return jsonify({
            'success': False,
            'error': 'No file selected'
        }), 400
    
    # Validate file type
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_extensions:
        return jsonify({
            'success': False,
            'error': f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'
        }), 400
    
    try:
        # Read and process image
        img = Image.open(file)
        
        # Convert RGBA to RGB if needed (for JPEG)
        if img.mode == 'RGBA':
            # Create white background
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize if wider than 600px
        max_width = 600
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
        
        # Compress to max 250KB
        max_size = 250 * 1024  # 250KB
        quality = 90
        output_format = 'JPEG' if ext in ('jpg', 'jpeg') else 'PNG'
        
        while quality > 10:
            buffer = io.BytesIO()
            img.save(buffer, format=output_format, quality=quality, optimize=True)
            if buffer.tell() <= max_size:
                break
            quality -= 10
        
        buffer.seek(0)
        img_bytes = buffer.read()
        
        # If still too large after compression, reduce dimensions
        if len(img_bytes) > max_size:
            ratio = 0.8
            while len(img_bytes) > max_size and img.width > 100:
                new_width = int(img.width * ratio)
                new_height = int(img.height * ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                img.save(buffer, format=output_format, quality=quality, optimize=True)
                buffer.seek(0)
                img_bytes = buffer.read()
        
        # Generate stable content_id
        content_id = f"sig_img_{uuid.uuid4().hex[:12]}"
        
        # Determine MIME type
        mime_type = 'image/jpeg' if output_format == 'JPEG' else 'image/png'
        
        # Create image metadata with base64 bytes
        image_data = {
            'content_id': content_id,
            'filename': f"{content_id}.{ext}",
            'mime_type': mime_type,
            'bytes_b64': base64.b64encode(img_bytes).decode('utf-8'),
            'width': img.width,
            'height': img.height,
            'size': len(img_bytes)
        }
        
        # Add to signature images
        current_images.append(image_data)
        integration.signature_images = current_images
        db.session.commit()
        
        logger.info(f"User {current_user.id} uploaded signature image {content_id}")
        
        return jsonify({
            'success': True,
            'content_id': content_id,
            'cid_url': f'cid:{content_id}',
            'width': img.width,
            'height': img.height,
            'size': len(img_bytes)
        })
        
    except Exception as e:
        logger.exception(f"Error processing signature image: {e}")
        return jsonify({
            'success': False,
            'error': f'Failed to process image: {str(e)}'
        }), 500


@gmail_bp.route('/signature/image/<content_id>', methods=['DELETE'])
@login_required
def delete_signature_image(content_id):
    """
    Delete a signature image by content_id.
    """
    integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    
    if not integration:
        return jsonify({
            'success': False,
            'error': 'Gmail not connected'
        }), 400
    
    current_images = integration.get_signature_images_list()
    
    # Find and remove the image
    updated_images = [img for img in current_images if img.get('content_id') != content_id]
    
    if len(updated_images) == len(current_images):
        return jsonify({
            'success': False,
            'error': 'Image not found'
        }), 404
    
    integration.signature_images = updated_images
    db.session.commit()
    
    logger.info(f"User {current_user.id} deleted signature image {content_id}")
    
    return jsonify({
        'success': True,
        'message': 'Image deleted'
    })
