# Email Marketing Integration Plan with SendGrid

This plan outlines the implementation of email marketing features in the CRM using SendGrid. The focus is on allowing agents to send template-based emails (both one-time and drip campaigns) to their contacts while maintaining simple but effective tracking.

## Core Features
- Use pre-made SendGrid templates (managed by admins)
- Support one-time email sends and drip campaigns
- Handle merge fields for personalization (client/agent details)
- Track email sends in CRM
- View email history per contact
- Sync templates from SendGrid

## Phase 1: Database Setup

### Tables Required

1. `marketing_templates`
   - `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
   - `sendgrid_template_id` (TEXT)
   - `name` (TEXT)
   - `description` (TEXT)
   - `merge_fields` (TEXT) - JSON array of available merge fields
   - `created_at` (DATETIME)
   - `updated_at` (DATETIME)

2. `email_sends`
   - `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
   - `contact_id` (INTEGER)
   - `user_id` (INTEGER) - the agent who sent it
   - `template_id` (INTEGER)
   - `sent_at` (DATETIME)
   - `status` (TEXT) - 'success' or 'failure'
   - `error_message` (TEXT)
   - `is_drip` (BOOLEAN)
   - `campaign_id` (INTEGER) - NULL for one-time sends

3. `email_campaigns`
   - `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
   - `user_id` (INTEGER)
   - `template_id` (INTEGER)
   - `name` (TEXT)
   - `start_date` (DATETIME)
   - `interval_days` (INTEGER)
   - `status` (TEXT) - 'active', 'paused', 'completed'
   - `created_at` (DATETIME)

4. `campaign_contacts`
   - `campaign_id` (INTEGER)
   - `contact_id` (INTEGER)
   - `status` (TEXT) - 'pending', 'sent', 'error'
   - PRIMARY KEY (campaign_id, contact_id)

## Phase 2: Template Sync
- Create SendGrid API integration
- Add scheduled task to sync templates from SendGrid
- Store template information locally
- Map available merge fields for each template

## Phase 3: One-Time Email Sends
- Create email send interface for agents
- Allow template selection
- Show merge field preview
- Implement SendGrid API integration for sending
- Record sends in `email_sends` table
- Add email history view in contact details

## Phase 4: Email Send Logging
- Create marketing section with filterable email logs
- Add email history tab to contact view
- Include basic status tracking (sent, failed)
- Add ability to resend failed emails

## Phase 5: Drip Campaigns
- Create campaign setup interface
- Implement contact selection for campaigns
- Add campaign management (pause, resume, cancel)
- Create scheduled task for processing drip sends
- Add campaign status view

## Technical Considerations

### SendGrid Integration
- Store API key securely in environment variables
- Implement rate limiting for API calls
- Add error handling and logging
- Create retry mechanism for failed API calls

### Merge Fields
Common merge fields to support:
- Client: name, email, address, phone
- Agent: name, email, phone, company
- Custom fields based on template

### Background Processing
- Use scheduled tasks for:
  - Template syncing (daily)
  - Drip campaign processing (hourly)
  - Failed email retries (hourly)

### Security
- Validate all contact lists before sending
- Implement sending limits per agent
- Log all email activities for audit

## Future Considerations
- Blacklist management
- Unsubscribe handling
- Email bounce processing
- Basic template performance metrics

Each phase can be implemented independently, allowing for gradual rollout and testing. The focus is on providing essential functionality while maintaining simplicity and reliability. 