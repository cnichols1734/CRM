"""
Contact Us routes - Handle contact form submissions
"""
from flask import Blueprint, request, jsonify, current_app
from flask_mail import Message

contact_bp = Blueprint('contact', __name__)


def get_mail():
    """Get Flask-Mail instance from app extensions."""
    return current_app.extensions.get('mail')


@contact_bp.route('/contact-us', methods=['POST'])
def contact_us():
    """Handle contact form submissions and send email to support."""
    mail = get_mail()
    if not mail:
        current_app.logger.warning("Flask-Mail not configured, cannot send contact form")
        return jsonify({
            'success': False,
            'message': 'Email service is not configured. Please try again later.'
        }), 503
    
    try:
        data = request.get_json()
        
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()
        user_email = data.get('email', '').strip()
        
        # Validation
        if not subject or not message or not user_email:
            return jsonify({'success': False, 'message': 'All fields are required'}), 400
        
        if len(subject) < 3:
            return jsonify({'success': False, 'message': 'Subject must be at least 3 characters'}), 400
        
        if len(message) < 10:
            return jsonify({'success': False, 'message': 'Message must be at least 10 characters'}), 400
        
        # Email validation (basic)
        if '@' not in user_email or '.' not in user_email:
            return jsonify({'success': False, 'message': 'Please provide a valid email address'}), 400
        
        # Send email to support (same pattern as org_notifications.py)
        # Don't set sender explicitly - let Flask-Mail use MAIL_DEFAULT_SENDER from config
        msg = Message(
            subject=f"💬 Contact Form: {subject}",
            recipients=['ogtechnolog@gmail.com'],
            reply_to=user_email
        )
        
        # HTML email body
        msg.html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); border-radius: 12px; padding: 30px; margin-bottom: 20px;">
                <h1 style="color: white; margin: 0; font-size: 24px; font-weight: bold;">
                    <i style="margin-right: 10px;">💬</i> New Contact Form Submission
                </h1>
            </div>
            
            <div style="background: #f8fafc; border-radius: 12px; padding: 25px; margin-bottom: 20px;">
                <div style="margin-bottom: 20px;">
                    <p style="margin: 0; color: #64748b; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Subject</p>
                    <p style="margin: 5px 0 0 0; color: #0f172a; font-size: 18px; font-weight: 600;">{subject}</p>
                </div>
                
                <div style="margin-bottom: 20px;">
                    <p style="margin: 0; color: #64748b; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">From</p>
                    <p style="margin: 5px 0 0 0; color: #0f172a; font-size: 16px;">
                        <a href="mailto:{user_email}" style="color: #3b82f6; text-decoration: none;">{user_email}</a>
                    </p>
                </div>
                
                <div>
                    <p style="margin: 0; color: #64748b; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Message</p>
                    <div style="margin: 10px 0 0 0; padding: 20px; background: white; border-radius: 8px; border-left: 4px solid #3b82f6;">
                        <p style="margin: 0; color: #334155; font-size: 15px; line-height: 1.6; white-space: pre-wrap;">{message}</p>
                    </div>
                </div>
            </div>
            
            <div style="text-align: center; padding: 20px;">
                <a href="mailto:{user_email}" 
                   style="display: inline-block; padding: 12px 30px; background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); color: white; text-decoration: none; border-radius: 8px; font-weight: 600; box-shadow: 0 4px 6px rgba(59, 130, 246, 0.3);">
                    Reply to {user_email}
                </a>
            </div>
            
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 30px 0;">
            
            <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">
                Origen TechnolOG CRM - Contact Form Submission
            </p>
        </div>
        """
        
        # Plain text fallback
        msg.body = f"""
New Contact Form Submission
============================

Subject: {subject}

From: {user_email}

Message:
{message}

---
Reply to: {user_email}

Origen TechnolOG CRM
        """
        
        mail.send(msg)
        
        current_app.logger.info(f"Contact form submitted by {user_email} - Subject: {subject}")
        
        return jsonify({
            'success': True,
            'message': 'Message sent successfully! We\'ll get back to you soon.'
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error processing contact form: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'An error occurred while sending your message. Please try again later.'
        }), 500
