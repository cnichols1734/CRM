from datetime import datetime
import sendgrid
import python_http_client
from sendgrid.helpers.mail import *
from flask import current_app
from models import db, SendGridTemplate
import urllib3
import ssl
import certifi
import os
import json

class SendGridService:
    def __init__(self, api_key=None):
        self.api_key = api_key or current_app.config.get('SENDGRID_API_KEY')
        if not self.api_key:
            raise ValueError("SendGrid API key not configured")
        
        # For development only - disable SSL warnings
        if current_app.config.get('FLASK_ENV') == 'development':
            urllib3.disable_warnings()
        
        # Initialize client
        self.client = sendgrid.SendGridAPIClient(api_key=self.api_key)
        
        # Set SSL verification using the system's certificates
        if os.path.exists('/etc/ssl/cert.pem'):
            self.client.client.useragent.session.verify = '/etc/ssl/cert.pem'
        else:
            self.client.client.useragent.session.verify = certifi.where()

    def sync_templates(self):
        """Sync templates from SendGrid to local database"""
        try:
            current_app.logger.info("Fetching templates from SendGrid")
            current_app.logger.info(f"Using API key ending in: ...{self.api_key[-4:]}")
            
            # Get all templates using the dynamic templates endpoint
            response = self.client.client.templates.get(query_params={
                'generations': 'dynamic'  # Only fetch dynamic templates
            })
            current_app.logger.info(f"Raw API Response Status Code: {response.status_code}")
            
            # Convert response to JSON data
            templates_data = response.body.decode('utf-8')
            current_app.logger.info(f"Raw API Response Body: {templates_data}")
            templates_data = json.loads(templates_data)
            
            if not isinstance(templates_data, dict) or 'templates' not in templates_data:
                current_app.logger.error(f"Unexpected API response: {templates_data}")
                return False, "Invalid API response format"
                
            templates = templates_data['templates']
            current_app.logger.info(f"Found {len(templates)} templates")

            # Update or create templates in database
            for template_data in templates:
                try:
                    template = SendGridTemplate.query.filter_by(
                        sendgrid_id=template_data['id']
                    ).first()

                    if not template:
                        current_app.logger.info(f"Creating new template: {template_data.get('name')}")
                        template = SendGridTemplate(
                            sendgrid_id=template_data['id']
                        )

                    # Get the active version details
                    versions = template_data.get('versions', [])
                    active_version = next(
                        (v for v in versions if v.get('active') == 1),
                        None
                    )

                    if active_version:
                        template.name = template_data.get('name')
                        template.subject = active_version.get('subject', '')
                        template.version = active_version.get('name')
                        template.active_version_id = active_version.get('id')
                        template.is_active = True  # Set as active when synced
                        if 'updated_at' in active_version:
                            template.last_modified = datetime.fromisoformat(
                                active_version['updated_at'].replace('Z', '+00:00')
                            )

                    db.session.add(template)
                    current_app.logger.info(f"Updated template: {template.name}")

                except Exception as template_error:
                    current_app.logger.error(f"Error processing template {template_data.get('id')}: {str(template_error)}")
                    continue

            db.session.commit()
            return True, f"Successfully synced {len(templates)} templates"

        except Exception as e:
            current_app.logger.error(f"Error in sync_templates: {str(e)}", exc_info=True)
            db.session.rollback()
            return False, str(e)

    def get_template_preview(self, template_id):
        """Get preview URL for a template"""
        try:
            template = SendGridTemplate.query.get_or_404(template_id)
            current_app.logger.info(f"Getting preview for template: {template.name}")
            
            # For Dynamic Templates, we need to get the template version first
            response = self.client.client.templates._(template.sendgrid_id).versions._(template.active_version_id).get()
            
            # Convert response to JSON data
            template_data = response.body.decode('utf-8')
            template_data = json.loads(template_data)
            current_app.logger.info(f"Template version data: {template_data}")
            
            # For Dynamic Templates, we'll use the HTML content directly
            html_content = template_data.get('html_content', '')
            
            if html_content:
                # Replace any template variables with sample data
                test_data = json.loads(template_data.get('test_data', '{}'))
                for key, value in test_data.items():
                    html_content = html_content.replace('{{' + key + '}}', value)
                
                # Create a data URL for the preview
                # Note: We don't store this in the database since it's too large for the varchar column
                # and is dynamically generated each time anyway
                import base64
                preview_url = f"data:text/html;base64,{base64.b64encode(html_content.encode('utf-8')).decode('utf-8')}"
                
                current_app.logger.info(f"Created preview URL for template: {template.name}")
                return preview_url
            else:
                current_app.logger.warning(f"No HTML content found for template: {template.name}")
                return None

        except Exception as e:
            current_app.logger.error(f"Error getting template preview: {str(e)}", exc_info=True)
            return None 