"""
Centralized Email Service using SendGrid.
Replaces Flask-Mail for all transactional emails.
"""
import os
from datetime import datetime
from flask import current_app, url_for
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

# SendGrid Dynamic Template IDs
TEMPLATES = {
    'password_reset': 'd-15a6ff328e6248efaaa13d4dd395bee2',
    'org_approved': 'd-cb87898b6ef3402a80875550159613e7',
    'org_rejected': 'd-073e682064e545f7b75584030c58fdb2',
    'team_invite': 'd-6f0042c586f7493fa8a991da62f4ce52',
    'contact_form': 'd-5b21998dab034f4ba4da297d2da91f16',
}

# Default sender email (must be verified in SendGrid)
DEFAULT_SENDER = 'info@origentechnolog.com'


class EmailService:
    """Centralized email service using SendGrid dynamic templates."""
    
    def __init__(self, api_key=None):
        """Initialize with SendGrid API key."""
        self.api_key = api_key or os.getenv('SENDGRID_API_KEY')
        self._client = None
    
    @property
    def client(self):
        """Lazy-load SendGrid client."""
        if self._client is None:
            if not self.api_key:
                raise ValueError("SENDGRID_API_KEY not configured")
            self._client = SendGridAPIClient(self.api_key)
        return self._client
    
    def send(self, template_name: str, to_email: str, template_data: dict, 
             from_email: str = None, reply_to: str = None) -> bool:
        """
        Send an email using a SendGrid dynamic template.
        
        Args:
            template_name: Key from TEMPLATES dict (e.g., 'password_reset')
            to_email: Recipient email address
            template_data: Dict of variables for the template
            from_email: Override sender email (optional)
            reply_to: Reply-to email address (optional)
        
        Returns:
            True if sent successfully, False otherwise
        """
        template_id = TEMPLATES.get(template_name)
        if not template_id:
            current_app.logger.error(f"Unknown email template: {template_name}")
            return False
        
        # Add current_year to all templates
        template_data.setdefault('current_year', str(datetime.now().year))
        
        try:
            message = Mail(
                from_email=Email(from_email or DEFAULT_SENDER),
                to_emails=To(to_email)
            )
            message.template_id = template_id
            message.dynamic_template_data = template_data
            
            if reply_to:
                message.reply_to = Email(reply_to)
            
            response = self.client.send(message)
            
            if response.status_code in (200, 201, 202):
                current_app.logger.info(
                    f"Email sent: template={template_name}, to={to_email}, status={response.status_code}"
                )
                return True
            else:
                current_app.logger.error(
                    f"Email failed: template={template_name}, to={to_email}, status={response.status_code}"
                )
                return False
                
        except Exception as e:
            current_app.logger.error(f"Email error: template={template_name}, to={to_email}, error={str(e)}")
            return False
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    def send_password_reset(self, user, reset_url: str) -> bool:
        """
        Send password reset email.
        
        Args:
            user: User model instance
            reset_url: Full URL for password reset
        """
        return self.send(
            template_name='password_reset',
            to_email=user.email,
            template_data={
                'first_name': user.first_name or 'User',
                'reset_url': reset_url,
            }
        )
    
    def send_org_approved(self, org, owner_email: str, login_url: str) -> bool:
        """
        Send organization approved email.
        
        Args:
            org: Organization model instance
            owner_email: Email of the org owner
            login_url: Full URL for login page
        """
        return self.send(
            template_name='org_approved',
            to_email=owner_email,
            template_data={
                'org_name': org.name,
                'login_url': login_url,
            }
        )
    
    def send_org_rejected(self, org, owner_email: str, reason: str = None) -> bool:
        """
        Send organization rejected email.
        
        Args:
            org: Organization model instance
            owner_email: Email of the org owner
            reason: Reason for rejection (optional)
        """
        return self.send(
            template_name='org_rejected',
            to_email=owner_email,
            template_data={
                'org_name': org.name,
                'reason': reason or 'Your application did not meet our requirements at this time.',
            }
        )
    
    def send_team_invite(self, org, inviter, invitee_email: str, invite_url: str) -> bool:
        """
        Send team invitation email.
        
        Args:
            org: Organization model instance
            inviter: User model instance (who sent the invite)
            invitee_email: Email of the person being invited
            invite_url: Full URL to accept the invite
        """
        inviter_name = f"{inviter.first_name} {inviter.last_name}".strip()
        if not inviter_name:
            inviter_name = inviter.email
        
        return self.send(
            template_name='team_invite',
            to_email=invitee_email,
            template_data={
                'inviter_name': inviter_name,
                'org_name': org.name,
                'invite_url': invite_url,
            }
        )
    
    def send_contact_form(self, subject: str, user_email: str, message: str, 
                          internal_recipient: str = 'ogtechnolog@gmail.com') -> bool:
        """
        Send contact form submission to internal team.
        
        Args:
            subject: Form subject
            user_email: Email of person who submitted the form
            message: Form message content
            internal_recipient: Where to send the notification
        """
        return self.send(
            template_name='contact_form',
            to_email=internal_recipient,
            template_data={
                'subject': subject,
                'user_email': user_email,
                'message': message,
            },
            reply_to=user_email
        )


# Module-level instance for convenience
_email_service = None


def get_email_service() -> EmailService:
    """Get or create the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
