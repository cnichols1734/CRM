from flask import Blueprint, render_template, jsonify, current_app, request
from flask_login import login_required, current_user
from functools import wraps
from models import db, SendGridTemplate
from services.sendgrid_service import SendGridService

# Create blueprint
marketing = Blueprint('marketing', __name__)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            current_app.logger.warning(f"Admin access denied for user {current_user.id if current_user.is_authenticated else 'anonymous'}")
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated_function

@marketing.route('/marketing/templates')
@login_required
@admin_required
def templates_list():
    """Display the SendGrid templates management page"""
    templates = SendGridTemplate.query.order_by(SendGridTemplate.name).all()
    # Ensure is_active is defined for all templates
    for template in templates:
        if template.is_active is None:
            template.is_active = True
            db.session.add(template)
    db.session.commit()
    return render_template('marketing/templates.html', templates=templates)

@marketing.route('/marketing/templates/<template_id>/toggle-status', methods=['POST'])
@login_required
@admin_required
def toggle_template_status(template_id):
    """Toggle the active status of a template"""
    try:
        template = SendGridTemplate.query.filter_by(sendgrid_id=template_id).first_or_404()
        template.is_active = not template.is_active
        db.session.commit()
        return jsonify({"message": "Status updated successfully", "is_active": template.is_active})
    except Exception as e:
        current_app.logger.error(f"Error toggling template status: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@marketing.route('/marketing/templates/refresh', methods=['POST'])
@login_required
@admin_required
def refresh_templates():
    """Endpoint to sync templates with SendGrid"""
    current_app.logger.info(f"Refresh templates request received from user {current_user.id}")
    current_app.logger.debug(f"Request headers: {dict(request.headers)}")
    
    try:
        current_app.logger.info("Starting template refresh")
        service = SendGridService()
        
        # Log the API key (last 4 characters only)
        api_key = service.api_key
        if api_key:
            current_app.logger.info(f"Using API key ending in: ...{api_key[-4:]}")
            current_app.logger.info("API key is configured")
        else:
            current_app.logger.error("No API key found")
            return jsonify({"error": "SendGrid API key not configured"}), 500
        
        success, message = service.sync_templates()
        
        if success:
            current_app.logger.info(f"Template refresh successful: {message}")
            return jsonify({"message": message}), 200
        else:
            current_app.logger.error(f"Template refresh failed: {message}")
            return jsonify({"error": message}), 500
            
    except Exception as e:
        current_app.logger.error(f"Error in refresh_templates: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@marketing.route('/marketing/templates/preview/<template_id>')
@login_required
@admin_required
def preview_template(template_id):
    """Preview a specific template"""
    try:
        # Find the template in our database by SendGrid ID
        template = SendGridTemplate.query.filter_by(sendgrid_id=template_id).first_or_404()
        
        service = SendGridService()
        preview_url = service.get_template_preview(template.id)
        
        if preview_url:
            return jsonify({"preview_url": preview_url})
        else:
            return jsonify({"error": "Could not get template preview"}), 404
    except Exception as e:
        current_app.logger.error(f"Error previewing template: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500 