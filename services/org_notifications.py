"""
Organization notification emails.
Uses SendGrid for transactional emails.
"""
from flask import current_app, url_for
from services.email_service import get_email_service


def send_org_approved_email(org, owner_email):
    """
    Send email to org owner when their organization is approved.
    """
    try:
        login_url = url_for('auth.login', _external=True)
        email_service = get_email_service()
        
        success = email_service.send_org_approved(org, owner_email, login_url)
        
        if success:
            current_app.logger.info(f"Sent approval email to {owner_email} for org {org.name}")
        return success
        
    except Exception as e:
        current_app.logger.error(f"Failed to send approval email: {e}")
        return False


def send_org_rejected_email(org, owner_email, reason=None):
    """
    Send email to org owner when their organization is rejected.
    """
    try:
        email_service = get_email_service()
        
        success = email_service.send_org_rejected(org, owner_email, reason)
        
        if success:
            current_app.logger.info(f"Sent rejection email to {owner_email} for org {org.name}")
        return success
        
    except Exception as e:
        current_app.logger.error(f"Failed to send rejection email: {e}")
        return False


def send_invite_email(org, inviter, invitee_email, invite_token):
    """
    Send invitation email to join an organization.
    """
    try:
        invite_url = url_for('auth.accept_invite', token=invite_token, _external=True)
        current_app.logger.info(f"Attempting to send invite email to {invitee_email}")
        current_app.logger.info(f"Invite URL: {invite_url}")
        
        email_service = get_email_service()
        current_app.logger.info(f"Email service initialized, API key present: {bool(email_service.api_key)}")
        
        success = email_service.send_team_invite(org, inviter, invitee_email, invite_url)
        
        if success:
            current_app.logger.info(f"Sent invite email to {invitee_email} for org {org.name}")
        else:
            current_app.logger.warning(f"send_team_invite returned False for {invitee_email}")
        return success
        
    except Exception as e:
        import traceback
        current_app.logger.error(f"Failed to send invite email: {e}")
        current_app.logger.error(f"Traceback: {traceback.format_exc()}")
        return False
