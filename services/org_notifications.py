"""
Organization notification emails.
Uses Flask-Mail (Gmail SMTP) for transactional emails.
"""
from flask import current_app, url_for
from flask_mail import Message
from models import User


def get_mail():
    """Get Flask-Mail instance from app extensions."""
    from flask import current_app
    return current_app.extensions.get('mail')


def send_org_approved_email(org, owner_email):
    """
    Send email to org owner when their organization is approved.
    """
    mail = get_mail()
    if not mail:
        current_app.logger.warning("Flask-Mail not configured, skipping approval email")
        return False
    
    try:
        login_url = url_for('auth.login', _external=True)
        
        msg = Message(
            subject=f"🎉 Your organization has been approved!",
            recipients=[owner_email],
        )
        
        msg.html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; margin-bottom: 30px;">
                <h1 style="color: #f97316; margin: 0;">Welcome to Origen TechnolOG!</h1>
            </div>
            
            <p style="font-size: 16px; color: #374151;">
                Great news! Your organization <strong>{org.name}</strong> has been approved and is now active.
            </p>
            
            <p style="font-size: 16px; color: #374151;">
                You can now log in and start using your CRM:
            </p>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{login_url}" 
                   style="background: linear-gradient(to right, #f97316, #f59e0b); 
                          color: white; 
                          padding: 14px 32px; 
                          text-decoration: none; 
                          border-radius: 8px;
                          font-weight: bold;
                          display: inline-block;">
                    Log In Now →
                </a>
            </div>
            
            <div style="background: #f0fdf4; border-radius: 8px; padding: 16px; margin: 20px 0;">
                <p style="margin: 0; color: #166534; font-size: 14px;">
                    <strong>Your Free Tier includes:</strong>
                </p>
                <ul style="margin: 10px 0; color: #166534; font-size: 14px;">
                    <li>Unlimited contacts</li>
                    <li>Task management</li>
                    <li>Contact groups & notes</li>
                    <li>Dashboard & reporting</li>
                </ul>
            </div>
            
            <p style="font-size: 14px; color: #6b7280; margin-top: 30px;">
                Questions? Reply to this email and we'll help you get started.
            </p>
            
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;">
            
            <p style="font-size: 12px; color: #9ca3af; text-align: center;">
                Origen TechnolOG CRM - Powering Real Estate Success
            </p>
        </div>
        """
        
        msg.body = f"""
Welcome to Origen TechnolOG!

Great news! Your organization "{org.name}" has been approved and is now active.

Log in here: {login_url}

Your Free Tier includes:
- Unlimited contacts
- Task management
- Contact groups & notes
- Dashboard & reporting

Questions? Reply to this email and we'll help you get started.

Origen TechnolOG CRM
        """
        
        mail.send(msg)
        current_app.logger.info(f"Sent approval email to {owner_email} for org {org.name}")
        return True
        
    except Exception as e:
        current_app.logger.error(f"Failed to send approval email: {e}")
        return False


def send_org_rejected_email(org, owner_email, reason=None):
    """
    Send email to org owner when their organization is rejected.
    """
    mail = get_mail()
    if not mail:
        current_app.logger.warning("Flask-Mail not configured, skipping rejection email")
        return False
    
    try:
        register_url = url_for('auth.register', _external=True)
        
        reason_text = reason if reason else "Your application did not meet our requirements at this time."
        
        msg = Message(
            subject=f"Update on your Origen TechnolOG application",
            recipients=[owner_email],
        )
        
        msg.html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #374151;">Application Update</h2>
            
            <p style="font-size: 16px; color: #374151;">
                Thank you for your interest in Origen TechnolOG. Unfortunately, we were unable to approve 
                your organization <strong>{org.name}</strong> at this time.
            </p>
            
            <div style="background: #fef3c7; border-radius: 8px; padding: 16px; margin: 20px 0;">
                <p style="margin: 0; color: #92400e; font-size: 14px;">
                    <strong>Reason:</strong> {reason_text}
                </p>
            </div>
            
            <p style="font-size: 16px; color: #374151;">
                If you believe this was an error or would like to provide additional information, 
                please reply to this email.
            </p>
            
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;">
            
            <p style="font-size: 12px; color: #9ca3af; text-align: center;">
                Origen TechnolOG CRM
            </p>
        </div>
        """
        
        msg.body = f"""
Application Update

Thank you for your interest in Origen TechnolOG. Unfortunately, we were unable to approve 
your organization "{org.name}" at this time.

Reason: {reason_text}

If you believe this was an error or would like to provide additional information, 
please reply to this email.

Origen TechnolOG CRM
        """
        
        mail.send(msg)
        current_app.logger.info(f"Sent rejection email to {owner_email} for org {org.name}")
        return True
        
    except Exception as e:
        current_app.logger.error(f"Failed to send rejection email: {e}")
        return False


def send_invite_email(org, inviter, invitee_email, invite_token):
    """
    Send invitation email to join an organization.
    """
    mail = get_mail()
    if not mail:
        current_app.logger.warning("Flask-Mail not configured, skipping invite email")
        return False
    
    try:
        invite_url = url_for('auth.accept_invite', token=invite_token, _external=True)
        
        msg = Message(
            subject=f"You've been invited to join {org.name} on Origen TechnolOG",
            recipients=[invitee_email],
        )
        
        msg.html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; margin-bottom: 30px;">
                <h1 style="color: #f97316; margin: 0;">You're Invited!</h1>
            </div>
            
            <p style="font-size: 16px; color: #374151;">
                <strong>{inviter.first_name} {inviter.last_name}</strong> has invited you to join 
                <strong>{org.name}</strong> on Origen TechnolOG CRM.
            </p>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{invite_url}" 
                   style="background: linear-gradient(to right, #f97316, #f59e0b); 
                          color: white; 
                          padding: 14px 32px; 
                          text-decoration: none; 
                          border-radius: 8px;
                          font-weight: bold;
                          display: inline-block;">
                    Accept Invitation →
                </a>
            </div>
            
            <p style="font-size: 14px; color: #6b7280;">
                This invitation expires in 72 hours. If you didn't expect this invitation, 
                you can safely ignore this email.
            </p>
            
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;">
            
            <p style="font-size: 12px; color: #9ca3af; text-align: center;">
                Origen TechnolOG CRM - Powering Real Estate Success
            </p>
        </div>
        """
        
        msg.body = f"""
You're Invited!

{inviter.first_name} {inviter.last_name} has invited you to join {org.name} on Origen TechnolOG CRM.

Accept your invitation here: {invite_url}

This invitation expires in 72 hours. If you didn't expect this invitation, 
you can safely ignore this email.

Origen TechnolOG CRM
        """
        
        mail.send(msg)
        current_app.logger.info(f"Sent invite email to {invitee_email} for org {org.name}")
        return True
        
    except Exception as e:
        current_app.logger.error(f"Failed to send invite email: {e}")
        return False
