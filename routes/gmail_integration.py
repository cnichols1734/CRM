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
