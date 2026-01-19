"""
Contact Us routes - Handle contact form submissions
"""
from flask import Blueprint, request, jsonify, current_app
from services.email_service import get_email_service

contact_bp = Blueprint('contact', __name__)


@contact_bp.route('/contact-us', methods=['POST'])
def contact_us():
    """Handle contact form submissions and send email to support."""
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
        
        # Send via SendGrid
        email_service = get_email_service()
        success = email_service.send_contact_form(
            subject=subject,
            user_email=user_email,
            message=message
        )
        
        if success:
            current_app.logger.info(f"Contact form submitted by {user_email} - Subject: {subject}")
            return jsonify({
                'success': True,
                'message': 'Message sent successfully! We\'ll get back to you soon.'
            }), 200
        else:
            return jsonify({
                'success': False,
                'message': 'An error occurred while sending your message. Please try again later.'
            }), 500
        
    except Exception as e:
        current_app.logger.error(f"Error processing contact form: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'An error occurred while sending your message. Please try again later.'
        }), 500
