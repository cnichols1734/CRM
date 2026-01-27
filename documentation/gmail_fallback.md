# Gmail Fallback for SendGrid Email Delivery

## Problem
SendGrid shared IP pools can get blacklisted (RBL), causing legitimate emails to bounce even though you're paying for the service. This happened with an org approval email that was blocked due to SendGrid's IP being on SpamCop's blacklist.

## Solution
Implemented automatic Gmail fallback in the `EmailService` class. When SendGrid fails to send an email, the system automatically retries using Gmail SMTP.

## How It Works

### Automatic Fallback Flow
1. **Try SendGrid first** - Attempt to send via SendGrid API with dynamic template
2. **Detect failure** - If SendGrid returns non-200 status or throws exception
3. **Load HTML template** - Read the local HTML template file from `email_templates/`
4. **Replace variables** - Substitute `{{variable_name}}` placeholders with actual data
5. **Send via Gmail** - Use Gmail SMTP (SSL on port 465) to deliver the email
6. **Log results** - Track which method succeeded for monitoring

### Code Location
- **Main implementation**: `services/email_service.py`
- **Standalone script**: `send_gmail_html.py` (for manual/emergency use)

### Configuration
Gmail fallback uses existing `.env` variables:
```bash
MAIL_USERNAME=ogtechnolog@gmail.com
MAIL_PASSWORD=flblrqlmyvfxfqkx  # Gmail App Password
```

### Supported Templates
All 6 email templates have Gmail fallback support:
1. ✓ Password Reset (`1_password_reset.html`)
2. ✓ Org Approved (`2_org_approved.html`)
3. ✓ Org Rejected (`3_org_rejected.html`)
4. ✓ Team Invitation (`4_team_invitation.html`)
5. ✓ Contact Form (`5_contact_form.html`)
6. ✓ Task Reminder (`6_task_reminder.html`)

## Usage

### In the App (Automatic)
No code changes needed! Existing email methods automatically use fallback:

```python
from services.email_service import get_email_service

email_service = get_email_service()

# This will automatically try Gmail if SendGrid fails
email_service.send_org_approved(
    org=organization,
    owner_email="user@example.com",
    login_url="https://app.origentechnolog.com/login"
)
```

### Manual Script (Emergency Use Only)
If you need to send an email completely outside the app:

```bash
python3 send_gmail_html.py
```

The script will:
- Prompt for Gmail App Password
- Show preview of email details
- Ask for confirmation
- Send directly via Gmail

## Monitoring

### Log Messages
Check application logs for these indicators:

**Success via SendGrid:**
```
✓ SendGrid email sent: template=org_approved, to=user@example.com, status=202
```

**Fallback triggered:**
```
SendGrid failed: template=org_approved, to=user@example.com, status=550
Attempting Gmail fallback for user@example.com
✓ Gmail fallback successful: template=org_approved, to=user@example.com
```

**Complete failure:**
```
SendGrid error: template=org_approved, to=user@example.com, error=...
Gmail fallback failed for user@example.com: ...
✗ All email methods failed: template=org_approved, to=user@example.com
```

## Benefits

1. **Zero downtime** - Emails still deliver when SendGrid has IP reputation issues
2. **No code changes** - Existing email-sending code works unchanged
3. **Transparent** - Logging shows which delivery method was used
4. **Cost effective** - Only uses Gmail when necessary (SendGrid preferred)
5. **Better UX** - Users get their emails even during SendGrid outages

## SendGrid IP Reputation Issues

### What Happened
```json
{
  "bounce_classification": "Reputation",
  "reason": "550 JunkMail rejected - s.wrqvtzvf.outbound-mail.sendgrid.net [149.72.126.143]:11742 is in an RBL on rbl.websitewelcome.com",
  "status": "550",
  "type": "blocked"
}
```

### Long-term Solutions to Consider
1. **Dedicated IP** - Upgrade SendGrid plan to get isolated IP ($90-150/month)
2. **Contact SendGrid Support** - Report the blocked IP for rotation
3. **Multiple providers** - Consider AWS SES or Mailgun as additional fallback
4. **Monitor deliverability** - Set up SendGrid webhook alerts for bounces

## Testing

To test the Gmail fallback locally:

1. Temporarily break SendGrid (e.g., use invalid API key)
2. Trigger an email send (e.g., org approval)
3. Check logs for Gmail fallback activation
4. Verify email delivered with proper HTML formatting
5. Restore SendGrid API key

## Notes

- Gmail App Password required (not regular password)
- Gmail has sending limits (~500 emails/day for free accounts)
- SendGrid is still the primary/preferred method
- HTML templates must be kept in sync between SendGrid and local files
- From address defaults to `info@origentechnolog.com` (verified in Gmail)

## Updated: January 27, 2026
