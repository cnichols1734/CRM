#!/usr/bin/env python3
"""
Quick script to send HTML email templates via Gmail when SendGrid has issues.

NOTE: As of Jan 2026, the main EmailService in services/email_service.py
now has automatic Gmail fallback built-in. This standalone script is kept
for manual/emergency sending when you need to bypass the app entirely.

Usage:
    python3 send_gmail_html.py

You'll need a Gmail App Password (not your regular password):
1. Go to myaccount.google.com
2. Security → 2-Step Verification → App passwords
3. Generate new app password for "Mail"
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

def send_html_email(
    from_email: str,
    app_password: str,
    to_email: str,
    subject: str,
    html_template_path: str,
    template_vars: dict
):
    """
    Send an HTML email via Gmail SMTP.
    
    Args:
        from_email: Your Gmail address (e.g., info@origentechnolog.com)
        app_password: Gmail app password (NOT your regular password)
        to_email: Recipient email
        subject: Email subject line
        html_template_path: Path to HTML template file
        template_vars: Dict of variables to replace in template (e.g., {"org_name": "Acme Realty"})
    """
    # Read and process template
    with open(html_template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # Replace template variables
    for key, value in template_vars.items():
        placeholder = f"{{{{{key}}}}}"  # {{variable_name}}
        html_content = html_content.replace(placeholder, str(value))
    
    # Create message
    msg = MIMEMultipart('alternative')
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = subject
    
    # Attach HTML content
    html_part = MIMEText(html_content, 'html')
    msg.attach(html_part)
    
    # Send via Gmail SMTP
    try:
        print(f"Connecting to Gmail SMTP...")
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            print(f"Logging in as {from_email}...")
            server.login(from_email, app_password)
            print(f"Sending email to {to_email}...")
            server.send_message(msg)
            print(f"✓ Email sent successfully!")
    except Exception as e:
        print(f"✗ Error sending email: {e}")
        raise


if __name__ == "__main__":
    # ============================================================
    # CONFIGURE YOUR EMAIL HERE
    # ============================================================
    
    FROM_EMAIL = "info@origentechnolog.com"
    
    # Get your Gmail App Password:
    # 1. Go to myaccount.google.com
    # 2. Security → 2-Step Verification → App passwords
    # 3. Generate new app password for "Mail"
    APP_PASSWORD = input("Enter your Gmail App Password: ").strip()
    
    # Recipient details
    TO_EMAIL = "james@jameswoodrealty.com"
    ORG_NAME = "No Place Like Home Realty"
    
    # Email configuration
    SUBJECT = "Welcome to Origen TechnolOG! Your organization has been approved"
    TEMPLATE_PATH = "email_templates/2_org_approved.html"
    
    # Template variables
    template_vars = {
        "org_name": ORG_NAME,
        "login_url": "https://app.origentechnolog.com/login",
        "current_year": str(datetime.now().year)
    }
    
    # ============================================================
    # SEND EMAIL
    # ============================================================
    
    print("\n" + "="*60)
    print("GMAIL HTML EMAIL SENDER")
    print("="*60)
    print(f"From: {FROM_EMAIL}")
    print(f"To: {TO_EMAIL}")
    print(f"Subject: {SUBJECT}")
    print(f"Template: {TEMPLATE_PATH}")
    print(f"Variables: {template_vars}")
    print("="*60 + "\n")
    
    confirm = input("Send this email? (yes/no): ").strip().lower()
    if confirm == 'yes':
        send_html_email(
            from_email=FROM_EMAIL,
            app_password=APP_PASSWORD,
            to_email=TO_EMAIL,
            subject=SUBJECT,
            html_template_path=TEMPLATE_PATH,
            template_vars=template_vars
        )
    else:
        print("Cancelled.")
