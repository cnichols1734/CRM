"""
Centralized Email Service using SendGrid with Gmail fallback.
Replaces Flask-Mail for all transactional emails.
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
    'task_reminder': 'd-7152fec3bc1a4e9a92fc821ebb5b521d',
}

# Default sender email (must be verified in SendGrid)
DEFAULT_SENDER = 'info@origentechnolog.com'


class EmailService:
    """Centralized email service using SendGrid dynamic templates with Gmail fallback."""
    
    def __init__(self, api_key=None):
        """Initialize with SendGrid API key."""
        self.api_key = api_key or os.getenv('SENDGRID_API_KEY')
        self._client = None
        
        # Gmail fallback configuration
        self.gmail_username = os.getenv('MAIL_USERNAME')
        self.gmail_password = os.getenv('MAIL_PASSWORD')
        self.gmail_enabled = bool(self.gmail_username and self.gmail_password)
    
    @property
    def client(self):
        """Lazy-load SendGrid client."""
        if self._client is None:
            if not self.api_key:
                raise ValueError("SENDGRID_API_KEY not configured")
            self._client = SendGridAPIClient(self.api_key)
        return self._client
    
    def _send_via_gmail(self, to_email: str, subject: str, html_content: str, 
                        from_email: str = None, reply_to: str = None) -> bool:
        """
        Fallback method to send email via Gmail SMTP when SendGrid fails.
        
        Args:
            to_email: Recipient email address
            subject: Email subject line
            html_content: HTML email content
            from_email: Sender email (defaults to MAIL_USERNAME)
            reply_to: Reply-to email address (optional)
        
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.gmail_enabled:
            current_app.logger.warning("Gmail fallback not configured - missing MAIL_USERNAME or MAIL_PASSWORD")
            return False
        
        try:
            current_app.logger.info(f"Attempting Gmail fallback for {to_email}")
            
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = from_email or self.gmail_username
            msg['To'] = to_email
            msg['Subject'] = subject
            
            if reply_to:
                msg['Reply-To'] = reply_to
            
            # Attach HTML content
            html_part = MIMEText(html_content, 'html')
            msg.attach(html_part)
            
            # Send via Gmail SMTP
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.gmail_username, self.gmail_password)
                server.send_message(msg)
            
            current_app.logger.info(f"✓ Gmail fallback successful for {to_email}")
            return True
            
        except Exception as e:
            current_app.logger.error(f"Gmail fallback failed for {to_email}: {str(e)}")
            return False
    
    def _get_template_html(self, template_name: str, template_data: dict) -> tuple:
        """
        Load HTML template from file and replace variables.
        
        Args:
            template_name: Template name (e.g., 'org_approved')
            template_data: Dict of variables to replace
        
        Returns:
            Tuple of (subject, html_content) or (None, None) if template not found
        """
        # Map template names to file paths and subjects
        template_map = {
            'password_reset': {
                'file': 'email_templates/1_password_reset.html',
                'subject': 'Reset Your Password - Origen TechnolOG'
            },
            'org_approved': {
                'file': 'email_templates/2_org_approved.html',
                'subject': 'Welcome to Origen TechnolOG! Your organization has been approved'
            },
            'org_rejected': {
                'file': 'email_templates/3_org_rejected.html',
                'subject': 'Your organization application - Origen TechnolOG'
            },
            'team_invite': {
                'file': 'email_templates/4_team_invitation.html',
                'subject': "You've been invited to join {org_name} on Origen TechnolOG"
            },
            'contact_form': {
                'file': 'email_templates/5_contact_form.html',
                'subject': 'New Contact Form Submission - {subject}'
            },
            'task_reminder': {
                'file': 'email_templates/6_task_reminder.html',
                'subject': 'You have {total_task_count} task(s) that need your attention'
            },
        }
        
        template_info = template_map.get(template_name)
        if not template_info:
            current_app.logger.error(f"Unknown template for Gmail fallback: {template_name}")
            return None, None
        
        try:
            # Read template file
            template_path = os.path.join(current_app.root_path, template_info['file'])
            with open(template_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # Replace template variables
            for key, value in template_data.items():
                placeholder = f"{{{{{key}}}}}"  # {{variable_name}}
                html_content = html_content.replace(placeholder, str(value))
            
            # Process subject line with template data
            subject = template_info['subject']
            for key, value in template_data.items():
                placeholder = f"{{{key}}}"  # {variable_name}
                subject = subject.replace(placeholder, str(value))
            
            return subject, html_content
            
        except Exception as e:
            current_app.logger.error(f"Error loading template {template_name}: {str(e)}")
            return None, None
    
    def send(self, template_name: str, to_email: str, template_data: dict, 
             from_email: str = None, reply_to: str = None) -> bool:
        """
        Send an email using a SendGrid dynamic template with automatic Gmail fallback.
        
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
        
        # Try SendGrid first
        try:
            current_app.logger.info(f"Preparing email: template={template_name}, to={to_email}, template_id={template_id}")
            current_app.logger.info(f"Template data keys: {list(template_data.keys())}")
            
            message = Mail(
                from_email=Email(from_email or DEFAULT_SENDER),
                to_emails=To(to_email)
            )
            message.template_id = template_id
            message.dynamic_template_data = template_data
            
            if reply_to:
                message.reply_to = Email(reply_to)
            
            current_app.logger.info(f"Sending email via SendGrid...")
            response = self.client.send(message)
            
            if response.status_code in (200, 201, 202):
                current_app.logger.info(
                    f"✓ SendGrid email sent: template={template_name}, to={to_email}, status={response.status_code}"
                )
                return True
            else:
                current_app.logger.warning(
                    f"SendGrid failed: template={template_name}, to={to_email}, status={response.status_code}"
                )
                current_app.logger.warning(f"Response body: {response.body}")
                current_app.logger.warning(f"Response headers: {response.headers}")
                # Fall through to Gmail fallback
                
        except Exception as e:
            import traceback
            current_app.logger.warning(f"SendGrid error: template={template_name}, to={to_email}, error={str(e)}")
            current_app.logger.warning(f"Full traceback: {traceback.format_exc()}")
            # Fall through to Gmail fallback
        
        # If SendGrid failed, try Gmail fallback
        current_app.logger.info(f"Attempting Gmail fallback for {to_email}")
        subject, html_content = self._get_template_html(template_name, template_data)
        
        if subject and html_content:
            gmail_success = self._send_via_gmail(
                to_email=to_email,
                subject=subject,
                html_content=html_content,
                from_email=from_email,
                reply_to=reply_to
            )
            
            if gmail_success:
                current_app.logger.info(
                    f"✓ Gmail fallback successful: template={template_name}, to={to_email}"
                )
                return True
        
        # Both methods failed
        current_app.logger.error(
            f"✗ All email methods failed: template={template_name}, to={to_email}"
        )
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
                          internal_recipient: str = 'info@origentechnolog.com') -> bool:
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
    
    def send_task_reminder_digest(self, user, tasks_by_type: dict, base_url: str = None) -> bool:
        """
        Send batched task reminder email with all tasks organized by urgency.
        
        Args:
            user: User model instance (assigned_to)
            tasks_by_type: Dict with keys 'overdue', 'today', 'tomorrow', 'upcoming'
                           each containing list of Task objects
            base_url: Base URL for task links (optional, uses url_for if in app context)
        
        Returns:
            True if sent successfully, False otherwise
        """
        import pytz
        ct = pytz.timezone('America/Chicago')
        
        def format_task(task):
            """Format a task object for the email template."""
            # Handle timezone conversion for due_date
            if task.due_date:
                if task.due_date.tzinfo:
                    due_date_ct = task.due_date.astimezone(ct)
                else:
                    # Assume UTC if no timezone
                    import pytz
                    due_date_ct = pytz.utc.localize(task.due_date).astimezone(ct)
                due_date_str = due_date_ct.strftime('%b %d, %Y')
            else:
                due_date_str = 'No date'
            
            # Build task URL
            if base_url:
                task_url = f"{base_url}/tasks/{task.id}"
            else:
                try:
                    task_url = url_for('tasks.view_task', task_id=task.id, _external=True)
                except RuntimeError:
                    # Not in app context
                    task_url = f"#task-{task.id}"
            
            # Capitalize priority for display
            priority_display = task.priority.capitalize() if task.priority else None
            
            return {
                'subject': task.subject,
                'due_date': due_date_str,
                'priority': priority_display,
                'contact_name': f"{task.contact.first_name} {task.contact.last_name}" if task.contact else 'Unknown',
                'task_url': task_url
            }
        
        overdue_tasks = [format_task(t) for t in tasks_by_type.get('overdue', [])]
        today_tasks = [format_task(t) for t in tasks_by_type.get('today', [])]
        tomorrow_tasks = [format_task(t) for t in tasks_by_type.get('tomorrow', [])]
        upcoming_tasks = [format_task(t) for t in tasks_by_type.get('upcoming', [])]
        
        total_count = len(overdue_tasks) + len(today_tasks) + len(tomorrow_tasks) + len(upcoming_tasks)
        
        if total_count == 0:
            return False  # Nothing to send
        
        # Build view all URL
        if base_url:
            view_all_url = f"{base_url}/tasks"
        else:
            try:
                view_all_url = url_for('tasks.tasks', _external=True)
            except RuntimeError:
                view_all_url = "#"
        
        return self.send(
            template_name='task_reminder',
            to_email=user.email,
            template_data={
                'first_name': user.first_name or 'there',
                'total_task_count': total_count,
                'is_plural': total_count > 1,  # Pre-computed for SendGrid template
                'overdue_tasks': overdue_tasks,
                'today_tasks': today_tasks,
                'tomorrow_tasks': tomorrow_tasks,
                'upcoming_tasks': upcoming_tasks,
                'has_overdue': len(overdue_tasks) > 0,
                'has_today': len(today_tasks) > 0,
                'has_tomorrow': len(tomorrow_tasks) > 0,
                'has_upcoming': len(upcoming_tasks) > 0,
                'view_all_url': view_all_url
            }
        )


# Module-level instance for convenience
_email_service = None


def get_email_service() -> EmailService:
    """Get or create the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
