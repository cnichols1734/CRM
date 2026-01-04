# Real Estate CRM Documentation

## Project Overview

This project is a Customer Relationship Management (CRM) system tailored for the real estate industry. It's built using Flask (Python) for the backend, HTML templates, Tailwind CSS and JavaScript for the frontend, and Supabase PostgreSQL for the database. The CRM is designed to be multi-user, with distinct roles for administrators and agents.

### Key Features

*   **Contact Management:** Agents can create, view, edit, and delete contacts. Admins have the ability to view all contacts across the system.
*   **Task Management:** Agents can create tasks associated with contacts, and admins can view all tasks.
*   **User Roles:** The system differentiates between 'admin' and 'agent' roles, with different levels of access and functionality.
*   **Contact Grouping:** Contacts can be categorized into groups (e.g., "Buyer," "Seller," "Network").
*   **Data Import/Export:** Contacts can be imported from and exported to CSV files.
*   **Dashboard:** Provides an overview of key metrics, such as total contacts, average commission, and top contacts.
*   **User Authentication:** Secure user registration, login, and password reset functionality.
*   **Responsive Design:** The UI is designed to be responsive and work well on different screen sizes.
*   **User Management:** Admins can manage user roles, edit user profiles, and delete users.
*   **AI-Powered Features:** Multiple AI assistants powered by centralized GPT service with fallback chain:
    * **B.O.B. (Business Optimization Buddy) Chat Assistant:** Floating chat widget accessible from any page with context-aware responses
    * **2026 Lead Generation Action Plan:** AI-generated personalized marketing plans based on agent questionnaires
    * **Daily Todo Generator:** AI-powered daily task lists combining CRM data with fresh marketing ideas
*   **User Todo Lists:** Personal todo management for agents to track their own tasks and goals
*   **Company Updates:** Blog-style announcements and news feed with reactions, comments, and engagement tracking
*   **Marketing Integration:** SendGrid email template management for campaign automation
*   **Admin Tools:** Group management, user administration, and system configuration
*   **Contact Tracking:** Advanced contact date tracking (email, text, phone calls) and client objective management
*   **Feature Flags:** Runtime feature toggling without code deployments
*   **Transaction Management:** Complete real estate transaction workflow from listing to close, including:
    * **Transaction Creation:** Create transactions for seller, buyer, landlord, tenant, or referral deals
    * **Participant Management:** Track all parties (sellers, co-sellers, agents, title companies, lenders)
    * **Intake Questionnaires:** Guided property questionnaires that auto-generate document packages
    * **Document Management:** Dynamic document packages based on intake answers
    * **Specialized Document Forms:** Wizard-style forms for filling complex documents (e.g., Listing Agreement)
    * **E-Signature Integration:** DocuSeal integration for sending documents for signature (mock mode available)

### Planned Features

*   **Advanced Marketing Campaigns:** Full email campaign management with automation and analytics
*   **Calendar Integration:** Sync with external calendars for appointments and showings
*   **Mobile App:** Native mobile application for iOS and Android
*   **Advanced Analytics:** Detailed reporting and business intelligence dashboards

## Data Model

The database is structured using SQLAlchemy with Supabase PostgreSQL and includes the following models:

*   **User (models.py:17-50):**
    *   `id`: Integer, primary key.
    *   `username`: String, unique, required.
    *   `email`: String, unique, required.
    *   `password_hash`: String, stores hashed password.
    *   `first_name`: String, required.
    *   `last_name`: String, required.
    *   `role`: String, defaults to 'agent'.
    *   `created_at`: DateTime, defaults to current time.
    *   `last_login`: DateTime, tracks last login time.
    *   `phone`: String(20), optional (user profile field).
    *   `license_number`: String(16), optional (real estate license number).
    *   `licensed_supervisor`: String(120), optional (supervisor information).
    *   Methods for setting and checking passwords, and generating password reset tokens.
*   **ContactGroup (models.py:47-59):**
    *   `id`: Integer, primary key.
    *   `name`: String, unique, required.
    *   `category`: String, required.
    *   `sort_order`: Integer, required.
    *   `created_at`: DateTime, defaults to current time.
    *   `contacts`: Relationship to `Contact` model using association table `contact_groups`.
*   **Contact (models.py:64-105):**
    *   `id`: Integer, primary key.
    *   `user_id`: Integer, foreign key referencing `User.id`, required.
    *   `first_name`: String, required.
    *   `last_name`: String, required.
    *   `email`: String, optional.
    *   `phone`: String, optional.
    *   `street_address`: String, optional.
    *   `city`: String, optional.
    *   `state`: String, optional.
    *   `zip_code`: String, optional.
    *   `notes`: Text, optional.
    *   `created_at`: DateTime, defaults to current time.
    *   `updated_at`: DateTime, defaults to current time, updated on each update.
    *   `potential_commission`: Numeric, defaults to 5000.00.
    *   Contact Date Tracking Fields:
        *   `last_email_date`: Date, optional.
        *   `last_text_date`: Date, optional.
        *   `last_phone_call_date`: Date, optional.
        *   `last_contact_date`: Date, optional (auto-calculated from above dates).
    *   Client Objective Fields:
        *   `current_objective`: Text, optional (buying, selling, etc.).
        *   `move_timeline`: Text, optional (timeline for move).
        *   `motivation`: Text, optional (why they're moving).
        *   `financial_status`: Text, optional (financial situation).
        *   `additional_notes`: Text, optional (extra context).
    *   `owner`: Relationship to `User` model.
    *   `groups`: Relationship to `ContactGroup` model using association table `contact_groups`.
    *   Methods: `update_last_contact_date()` (calculates last_contact_date from individual contact dates).
*   **Interaction (models.py:87-99):**
    *   `id`: Integer, primary key.
    *   `contact_id`: Integer, foreign key referencing `Contact.id`, required.
    *   `user_id`: Integer, foreign key referencing `User.id`, required.
    *   `type`: String, required.
    *   `notes`: Text, optional.
    *   `date`: DateTime, required.
    *   `follow_up_date`: DateTime, optional.
    *   `created_at`: DateTime, defaults to current time.
    *   `contact`: Relationship to `Contact` model.
    *   `user`: Relationship to `User` model.
*   **TaskType (models.py:100-107):**
    *   `id`: Integer, primary key.
    *   `name`: String, required (e.g., 'Call', 'Email', 'Meeting').
    *   `sort_order`: Integer, required.
    *   `subtypes`: Relationship to `TaskSubtype` model.
*   **TaskSubtype (models.py:108-113):**
    *   `id`: Integer, primary key.
    *   `task_type_id`: Integer, foreign key referencing `TaskType.id`, required.
    *   `name`: String, required (e.g., 'Check-in', 'Send Documents').
    *   `sort_order`: Integer, required.
*   **Task (models.py:136-168):**
    *   `id`: Integer, primary key.
    *   `contact_id`: Integer, foreign key referencing `Contact.id`, required.
    *   `assigned_to_id`: Integer, foreign key referencing `User.id`, required.
    *   `created_by_id`: Integer, foreign key referencing `User.id`, required.
    *   `type_id`: Integer, foreign key referencing `TaskType.id`, required.
    *   `subtype_id`: Integer, foreign key referencing `TaskSubtype.id`, required.
    *   `subject`: String, required.
    *   `description`: Text, optional.
    *   `priority`: String, defaults to 'medium' ('low', 'medium', 'high').
    *   `status`: String, defaults to 'pending' ('pending', 'completed', 'cancelled').
    *   `outcome`: Text, optional.
    *   `created_at`: DateTime, defaults to current time.
    *   `due_date`: DateTime, required.
    *   `completed_at`: DateTime, optional.
    *   `property_address`: String, optional.
    *   `scheduled_time`: DateTime, optional.
    *   `reminder_sent`: Boolean, defaults to False.
    *   `contact`: Relationship to `Contact` model.
    *   `assigned_to`: Relationship to `User` model.
    *   `created_by`: Relationship to `User` model.
    *   `task_type`: Relationship to `TaskType` model.
    *   `task_subtype`: Relationship to `TaskSubtype` model.
*   **DailyTodoList (models.py:170-184):**
    *   `id`: Integer, primary key.
    *   `user_id`: Integer, foreign key referencing `User.id`, required.
    *   `generated_at`: DateTime, defaults to current time.
    *   `todo_content`: JSON, required (AI-generated todo list structure).
    *   `created_at`: DateTime, defaults to current time.
    *   `updated_at`: DateTime, defaults to current time, updated on each update.
    *   `user`: Relationship to `User` model.
    *   Methods: `get_latest_for_user(user_id)`, `should_generate_new(user_id)`.
*   **UserTodo (models.py:196-210):**
    *   `id`: Integer, primary key.
    *   `user_id`: Integer, foreign key referencing `User.id`, required (cascade delete).
    *   `text`: String(500), required.
    *   `completed`: Boolean, defaults to False.
    *   `order`: Integer, required, defaults to 0.
    *   `created_at`: DateTime, defaults to current time.
    *   `updated_at`: DateTime, defaults to current time, updated on each update.
    *   `user`: Relationship to `User` model.
*   **ActionPlan (models.py:231-246):**
    *   `id`: Integer, primary key.
    *   `user_id`: Integer, foreign key referencing `User.id`, unique, required (cascade delete).
    *   `questionnaire_responses`: JSON, required (form answers as JSON).
    *   `ai_generated_plan`: Text, optional (AI-generated plan content).
    *   `created_at`: DateTime, defaults to current time.
    *   `updated_at`: DateTime, defaults to current time, updated on each update.
    *   `user`: Relationship to `User` model.
    *   Methods: `get_for_user(user_id)`.
*   **CompanyUpdate (models.py:254-290):**
    *   `id`: Integer, primary key.
    *   `title`: String(255), required.
    *   `content`: Text, required (HTML from Quill.js editor).
    *   `excerpt`: String(500), optional (auto-generated plain text preview).
    *   `cover_image_url`: String(500), optional (external URL).
    *   `author_id`: Integer, foreign key referencing `User.id`, optional (set null on delete).
    *   `created_at`: DateTime, defaults to current time.
    *   `updated_at`: DateTime, defaults to current time, updated on each update.
    *   `author`: Relationship to `User` model.
    *   `reactions`: Relationship to `CompanyUpdateReaction` model.
    *   `comments`: Relationship to `CompanyUpdateComment` model.
    *   `views`: Relationship to `CompanyUpdateView` model.
    *   Methods: `get_reaction_counts()`, `get_user_reactions(user_id)`.
*   **CompanyUpdateReaction (models.py:292-320):**
    *   `id`: Integer, primary key.
    *   `update_id`: Integer, foreign key referencing `CompanyUpdate.id`, required (cascade delete).
    *   `user_id`: Integer, foreign key referencing `User.id`, required (cascade delete).
    *   `reaction_type`: String(20), required (thumbs_up, heart, raised_hands, fire, clap).
    *   `created_at`: DateTime, defaults to current time.
    *   `REACTION_TYPES`: Available reaction types constant.
    *   `REACTION_EMOJIS`: Emoji mapping for reaction types.
    *   `update`: Relationship to `CompanyUpdate` model.
    *   `user`: Relationship to `User` model.
    *   Unique constraint: one reaction type per user per update.
*   **CompanyUpdateComment (models.py:322-336):**
    *   `id`: Integer, primary key.
    *   `update_id`: Integer, foreign key referencing `CompanyUpdate.id`, required (cascade delete).
    *   `user_id`: Integer, foreign key referencing `User.id`, required (cascade delete).
    *   `content`: Text, required.
    *   `created_at`: DateTime, defaults to current time.
    *   `update`: Relationship to `CompanyUpdate` model.
    *   `user`: Relationship to `User` model.
*   **CompanyUpdateView (models.py:339-354):**
    *   `id`: Integer, primary key.
    *   `update_id`: Integer, foreign key referencing `CompanyUpdate.id`, required (cascade delete).
    *   `user_id`: Integer, foreign key referencing `User.id`, required (cascade delete).
    *   `viewed_at`: DateTime, defaults to current time.
    *   `update`: Relationship to `CompanyUpdate` model.
    *   `user`: Relationship to `User` model.
    *   Unique constraint: one view record per user per update.
*   **SendGridTemplate (models.py:212-228):**
    *   `id`: Integer, primary key.
    *   `sendgrid_id`: String(100), unique, required.
    *   `name`: String(200), required.
    *   `subject`: String(200), optional.
    *   `version`: String(50), optional.
    *   `active_version_id`: String(100), optional.
    *   `preview_url`: String(500), optional.
    *   `is_active`: Boolean, defaults to True.
    *   `last_modified`: DateTime, optional.
    *   `created_at`: DateTime, defaults to current time.
    *   `updated_at`: DateTime, defaults to current time.

### Transaction Management Models

*   **TransactionType (models.py:362-380):**
    *   `id`: Integer, primary key.
    *   `name`: String(50), unique, required (e.g., 'seller', 'buyer', 'landlord', 'tenant', 'referral').
    *   `display_name`: String(100), required (e.g., 'Seller Representation').
    *   `description`: Text, optional.
    *   `is_active`: Boolean, defaults to True.
    *   `sort_order`: Integer, defaults to 0.
    *   `transactions`: Relationship to `Transaction` model.

*   **Transaction (models.py:383-447):**
    *   `id`: Integer, primary key.
    *   `created_by_id`: Integer, foreign key referencing `User.id`, required.
    *   `transaction_type_id`: Integer, foreign key referencing `TransactionType.id`, required.
    *   Property Details:
        *   `street_address`: String(200), required.
        *   `city`: String(100), optional.
        *   `state`: String(50), defaults to 'TX'.
        *   `zip_code`: String(20), optional.
        *   `county`: String(100), optional.
    *   `ownership_status`: String(50), optional (values: 'conventional', 'builder', 'reo', 'short_sale').
    *   `status`: String(50), defaults to 'draft' (values: 'draft', 'active', 'pending', 'under_contract', 'closed', 'cancelled').
    *   Key Dates:
        *   `expected_close_date`: Date, optional.
        *   `actual_close_date`: Date, optional.
    *   `intake_data`: JSON, defaults to {} (intake questionnaire answers).
    *   `extra_data`: JSON, defaults to {} (flexible additional fields).
    *   `created_at`: DateTime, defaults to current time.
    *   `updated_at`: DateTime, defaults to current time, updated on each update.
    *   Relationships: `created_by` (User), `participants` (TransactionParticipant), `documents` (TransactionDocument).
    *   Property: `full_address` (formatted full address string).

*   **TransactionParticipant (models.py:450-503):**
    *   `id`: Integer, primary key.
    *   `transaction_id`: Integer, foreign key referencing `Transaction.id`, required (cascade delete).
    *   `contact_id`: Integer, foreign key referencing `Contact.id`, optional.
    *   `user_id`: Integer, foreign key referencing `User.id`, optional.
    *   `role`: String(50), required (values: 'seller', 'co_seller', 'buyer', 'co_buyer', 'listing_agent', 'buyers_agent', 'title_company', 'lender', 'transaction_coordinator').
    *   For external parties:
        *   `name`: String(200), optional.
        *   `email`: String(200), optional.
        *   `phone`: String(20), optional.
        *   `company`: String(200), optional.
    *   `is_primary`: Boolean, defaults to True.
    *   `created_at`: DateTime, defaults to current time.
    *   Relationships: `contact` (Contact), `user` (User).
    *   Properties: `display_name` (gets name from contact, user, or name field), `display_email` (gets email from contact, user, or email field).

*   **TransactionDocument (models.py:506-544):**
    *   `id`: Integer, primary key.
    *   `transaction_id`: Integer, foreign key referencing `Transaction.id`, required (cascade delete).
    *   `template_slug`: String(100), required (e.g., 'listing-agreement').
    *   `template_name`: String(200), required (e.g., 'Listing Agreement').
    *   `status`: String(50), defaults to 'pending' (values: 'pending', 'draft', 'filled', 'generated', 'sent', 'partially_signed', 'signed', 'voided').
    *   `field_data`: JSON, defaults to {} (form data filled by agent).
    *   `included_reason`: String(500), optional (why document was included, for conditional docs).
    *   DocuSeal Integration:
        *   `docuseal_template_id`: String(100), optional.
        *   `docuseal_submission_id`: String(100), optional.
    *   Timestamps:
        *   `created_at`: DateTime, defaults to current time.
        *   `updated_at`: DateTime, defaults to current time, updated on each update.
        *   `sent_at`: DateTime, optional (when sent for signature).
        *   `signed_at`: DateTime, optional (when all signatures complete).
    *   Relationships: `signatures` (DocumentSignature).

*   **DocumentSignature (models.py:547-583):**
    *   `id`: Integer, primary key.
    *   `document_id`: Integer, foreign key referencing `TransactionDocument.id`, required (cascade delete).
    *   `participant_id`: Integer, foreign key referencing `TransactionParticipant.id`, optional.
    *   `signer_email`: String(200), required.
    *   `signer_name`: String(200), required.
    *   `signer_role`: String(50), required (e.g., 'seller', 'listing_agent').
    *   `status`: String(50), defaults to 'pending' (values: 'pending', 'sent', 'viewed', 'signed', 'declined').
    *   `sign_order`: Integer, defaults to 1 (for sequential signing).
    *   `docuseal_submitter_slug`: String(200), optional (for embedded signing).
    *   Timestamps:
        *   `sent_at`: DateTime, optional.
        *   `viewed_at`: DateTime, optional.
        *   `signed_at`: DateTime, optional.
    *   Relationships: `participant` (TransactionParticipant).

### Association Table

*   **contact\_groups (models.py:12-15):**
    *   `contact_id`: Integer, foreign key referencing `contact.id`, primary key.
    *   `group_id`: Integer, foreign key referencing `contact_group.id`, primary key.

## AI Features Architecture

The CRM integrates multiple AI-powered features using a centralized AI service architecture with intelligent model fallback chains for reliability and cost optimization.

### Centralized AI Service (`services/ai_service.py`)

**Model Hierarchy & Fallback Chain:**
1. **Primary Model:** GPT-5.1 (Responses API with reasoning capabilities)
2. **Fallback Model:** GPT-5-mini (Responses API with reasoning capabilities)
3. **Legacy Model:** GPT-4o (Chat Completions API)

**Key Features:**
- Automatic model failover on API errors, rate limits, or model unavailability
- Support for both single-response generation and multi-turn chat conversations
- Configurable reasoning effort levels ("low", "medium", "high")
- JSON mode support for structured outputs
- Comprehensive error logging and monitoring

**Usage:**
```python
from services.ai_service import generate_ai_response, generate_chat_response

# Single response generation
response = generate_ai_response(
    system_prompt="You are a helpful assistant...",
    user_prompt="Help me with...",
    temperature=0.7,
    json_mode=False
)

# Chat conversation
response = generate_chat_response(
    messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
    temperature=0.7
)
```

### AI Feature Components

#### 1. B.O.B. (Business Optimization Buddy) Chat Assistant

**Purpose:** Contextual AI assistant providing real estate guidance and CRM help
**Location:** `routes/ai_chat.py`, `static/js/ai_chat.js`, `static/css/ai_chat.css`

**Features:**
- Floating chat widget accessible from all pages
- Context-aware responses based on current page content
- Access to contact details and related tasks when viewing contact records
- Personalized responses using agent's name and role
- Real estate and HAR-focused guidance
- Markdown formatting for structured responses
- Message history persistence within sessions

**System Prompt Highlights:**
- 15+ years real estate experience in Houston
- HAR procedures and best practices expertise
- Market trends and property valuation knowledge
- Negotiation and client relationship skills

#### 2. 2026 Lead Generation Action Plan Generator

**Purpose:** AI-powered personalized marketing strategy creation
**Location:** `routes/action_plan.py`, `templates/action_plan.html`

**Process:**
1. Agent completes detailed questionnaire about their:
   - Natural communication tendencies
   - Time commitment availability
   - Preferred lead generation styles
   - Personal strengths and past successes
   - Target client types and motivations

2. AI analyzes responses and identifies 3 core lead generation pillars
3. Generates comprehensive plan with:
   - Monthly recurring action items with specific quantities
   - Weekly task schedules with measurable targets
   - Optional high-impact bonus activities
   - Next steps implementation guide

**System Prompt Restrictions:**
- Never recommends Facebook Live or live video streaming
- No surveys of past clients
- No referral reward programs
- No in-person seminars or workshops

#### 3. Daily Todo List Generator

**Purpose:** AI-powered daily task prioritization and fresh marketing ideas
**Location:** `routes/daily_todo.py`, `static/js/daily_todo.js`

**Data Sources:**
- Overdue, today's, and upcoming tasks from CRM
- Recent contacts needing follow-up
- High-value opportunities by commission potential
- Agent's personal information and location context

**Output Structure:**
- Personalized summary with urgent task highlights
- Prioritized task list with status indicators (OVERDUE, TODAY, UPCOMING)
- Suggested contact follow-ups with communication methods
- Top commission opportunities
- 3-5 fresh marketing ideas not based on existing CRM data

**Communication Style:**
- Direct and genuine, no unnecessary formality
- Uses agent's first name in summaries
- Professional yet conversational tone
- Local Houston context and seasonal awareness

## AI Service Architecture (`services/ai_service.py`)

The CRM implements a centralized AI service architecture that provides consistent, reliable AI interactions across all features. This service manages model selection, error handling, and fallback mechanisms to ensure high availability.

### Model Fallback Chain

**Primary Model:** GPT-5.1 (Responses API)
- Used for initial attempts on all AI features
- Supports reasoning effort levels ("low", "medium", "high")
- Most advanced and preferred model

**Fallback Model:** GPT-5-mini (Responses API)
- Automatic fallback when GPT-5.1 is unavailable
- Same API structure as primary model
- Cost-effective alternative with good performance

**Legacy Model:** GPT-4o (Chat Completions API)
- Final fallback for maximum reliability
- Uses different API structure (Chat Completions vs Responses)
- Supports both regular and JSON mode responses

### Error Handling & Reliability

**Automatic Fallback Triggers:**
- Model not found (404)
- Authentication errors (401/403)
- Rate limiting (429)
- Service unavailability (5xx errors)

**Logging & Monitoring:**
- Comprehensive logging for all AI interactions
- Masked API key logging for debugging
- Success/failure tracking per model
- Performance metrics and error analysis

### Service Functions

**`generate_ai_response()`**
- Primary function for single-response AI generation
- Supports configurable reasoning effort and JSON mode
- Automatic model fallback with detailed error logging
- Used by Action Plan and Daily Todo features

**`generate_chat_response()`**
- Specialized function for multi-turn conversations
- Maintains full message history context
- Used by B.O.B. chat assistant
- Supports both Responses API and Chat Completions API

### Integration Benefits

- **Consistency:** All AI features use the same underlying service
- **Reliability:** Multi-model fallback ensures service availability
- **Cost Optimization:** Intelligent model selection based on availability
- **Maintainability:** Single point of configuration for AI settings
- **Monitoring:** Centralized logging and error tracking

### Configuration

AI service configuration is managed through environment variables:
- `OPENAI_API_KEY`: Required API key for all models
- Model hierarchy is hardcoded for consistency across deployments

## SendGrid Service (`services/sendgrid_service.py`)

The CRM integrates with SendGrid for email marketing template management and future campaign automation. The service provides template synchronization, preview functionality, and status management.

### Key Features

**Template Synchronization:**
- Syncs dynamic email templates from SendGrid API to local database
- Maintains template metadata (name, subject, version, active version ID)
- Tracks last modification dates and preview URLs
- Handles both active and inactive templates

**Template Management:**
- Activate/deactivate templates for campaign use
- Preview template content through SendGrid's preview URLs
- Bulk synchronization with error handling
- Status tracking and database consistency

**Error Handling:**
- SSL certificate management for secure API calls
- Development mode SSL warning suppression
- Comprehensive logging for debugging
- Graceful handling of API failures

### Service Methods

**`sync_templates()`**
- Fetches all dynamic templates from SendGrid
- Updates local database with latest template information
- Creates new records for new templates
- Updates existing records with changed metadata
- Returns success/failure status with detailed messages

**`get_template_preview(template_id)`**
- Retrieves preview URLs for specific templates
- Used by admin interface for template preview functionality
- Handles template versioning and active version selection

### Configuration

SendGrid integration requires:
- `SENDGRID_API_KEY`: SendGrid API key with template access permissions
- SSL certificate verification (automatic in production)
- Development mode disables SSL warnings for local testing

### Database Integration

Templates are stored in `SendGridTemplate` model with:
- SendGrid template ID mapping
- Template metadata and versioning
- Active/inactive status flags
- Last synchronization timestamps

## Intake Service (`services/intake_service.py`)

The intake service handles loading intake questionnaire schemas and evaluating document rules to generate dynamic document packages based on property characteristics.

### Key Features

**Schema Loading:**
- Loads JSON schemas from `intake_schemas/` directory
- Schemas named by pattern: `{transaction_type}_{ownership_status}.json`
- Example: `seller_conventional.json` for conventional seller transactions

**Document Rule Evaluation:**
- Evaluates conditions against intake answers
- Supports `always`, `equals`, `in`, `not_equals` conditions
- Returns list of required documents with inclusion reasons

**Intake Validation:**
- Validates all required questions are answered
- Returns missing field IDs for error messaging

### Service Methods

**`get_intake_schema(transaction_type, ownership_status)`**
- Loads and returns the appropriate intake schema
- Returns None if schema file doesn't exist

**`evaluate_document_rules(schema, intake_data)`**
- Evaluates document_rules against intake answers
- Returns list of document dicts with slug, name, reason

**`validate_intake_data(schema, intake_data)`**
- Checks all required questions have answers
- Returns tuple: (is_valid, list_of_missing_field_ids)

**`get_question_labels(schema)`**
- Maps question IDs to display labels
- Useful for error messaging

### Schema Structure

```json
{
  "title": "Property Questionnaire",
  "sections": [
    {
      "title": "Property Details",
      "questions": [
        {"id": "has_hoa", "label": "Is there an HOA?", "type": "boolean", "required": true}
      ]
    }
  ],
  "document_rules": [
    {"slug": "listing-agreement", "name": "Listing Agreement", "always": true},
    {"slug": "hoa-addendum", "name": "HOA Addendum", "condition": {"field": "has_hoa", "equals": true}}
  ]
}
```

## DocuSeal Service (`services/docuseal_service.py`)

The DocuSeal service handles e-signature integration for sending documents for signature. Currently uses mock implementations for development, with real API integration ready to enable.

### Key Features

**Mock Mode:**
- Enabled by default (`DOCUSEAL_MOCK_MODE=True`)
- In-memory storage for submissions
- Simulates complete e-signature workflow
- Allows testing without DocuSeal account

**Submission Management:**
- Create and track signature requests
- Get submission status and details
- Retrieve signing URLs for embedded signing
- Download signed document URLs

**Template Mapping:**
- Maps internal template slugs to DocuSeal template IDs
- `TEMPLATE_MAP` dict for configuration

### Configuration

```bash
# Environment variables
DOCUSEAL_API_KEY=your_api_key          # Required for real mode
DOCUSEAL_API_URL=https://api.docuseal.com
DOCUSEAL_MOCK_MODE=True                 # Set False for real integration
```

### Service Methods

**`create_submission(template_slug, submitters, field_values, send_email, message)`**
- Creates new signature request
- Returns submission data with IDs and submitter details

**`get_submission(submission_id)`**
- Gets submission details by ID
- Returns status, submitters, documents

**`get_submission_status(submission_id)`**
- Returns current status: pending, viewed, started, completed, expired

**`get_signing_url(submission_id, submitter_slug)`**
- Gets embedded signing URL for specific submitter

**`get_signed_document_urls(submission_id)`**
- Gets download URLs for completed documents

**`process_webhook(payload)`**
- Processes DocuSeal webhook events
- Returns parsed event data

### Helper Functions

**`build_submitters_from_participants(participants, transaction)`**
- Converts transaction participants to DocuSeal submitters
- Maps roles: seller → Seller, co_seller → Co-Seller, listing_agent → Listing Agent

**`format_status_badge(status)`**
- Returns badge class and label for display

### Mock Testing

```python
from services.docuseal_service import _mock_simulate_signing

# Simulate signing completion for testing
_mock_simulate_signing('submission_id', 'completed')
```

Events supported: 'viewed', 'started', 'completed'

## Feature Flags System (`feature_flags.py`)

The CRM implements a feature flag system for runtime feature toggling without code deployments. This allows safe feature rollouts, A/B testing, and quick feature deactivation.

### Architecture

**Configuration File:** `feature_flags.py`
- Dictionary-based flag storage
- Simple boolean values (True/False)
- Centralized flag definitions
- Deployed with git (no environment variable management needed)

**Usage Functions:**
```python
from feature_flags import is_enabled

# Check if a feature is enabled
if is_enabled('SHOW_DASHBOARD_JOKE'):
    # Show dashboard joke functionality
    pass
```

### Current Feature Flags

**`SHOW_DASHBOARD_JOKE`**
- **Status:** False (disabled)
- **Purpose:** Controls display of joke of the day on dashboard
- **Implementation:** Fetches jokes from external APIs when enabled
- **Impact:** Adds humor element to dashboard but requires external API calls

**`TRANSACTIONS_ENABLED`**
- **Status:** True (enabled)
- **Purpose:** Controls access to the Transaction Management module
- **Implementation:** Uses `can_access_transactions(user)` helper that checks both flag status AND user role
- **Access Control:** Currently admin-only (user must have `role == 'admin'`)
- **Future:** Can be extended to allow specific agents via additional logic

### Benefits

- **Zero-downtime deployments:** Enable/disable features without redeploying
- **Gradual rollouts:** Test features with subset of users
- **Quick rollback:** Immediately disable problematic features
- **Environment consistency:** Same flags apply across all environments
- **No config complexity:** Simple Python dictionary, git-managed

### Adding New Flags

1. Add flag to `FEATURE_FLAGS` dictionary in `feature_flags.py`
2. Set initial value (typically False for new features)
3. Implement conditional logic in templates/code using `is_enabled()`
4. Test thoroughly before enabling in production
5. Commit and deploy with regular git workflow

## Routes and Functionality

The application is structured using Flask blueprints. Here's a breakdown of the routes and their functions:

### Main Blueprint (`routes/main.py`)

*   **`/` (index):**
    *   Displays a list of contacts.
    *   Supports filtering by 'all' (admin only) or 'my' contacts.
    *   Supports sorting by various fields (name, email, phone, address, notes, created\_at, owner, potential\_commission).
    *   Supports searching contacts by name, email, or phone.
    *   Renders `index.html` (templates/index.html).
*   **`/dashboard` (dashboard):**
    *   Displays a dashboard with key metrics.
    *   Shows total contacts, total commission, average commission, top contacts by commission, and group statistics.
    *   Displays upcoming tasks.
    *   Renders `dashboard.html` (templates/dashboard.html).

### Contacts Blueprint (`routes/contacts.py`)

*   **`/contact/<int:contact_id>` (view\_contact):**
    *   Displays details for a specific contact.
    *   Returns JSON data if it's an AJAX request.
    *   Renders `view_contact.html` (templates/view_contact.html).
*   **`/contacts/create` (create\_contact):**
    *   Handles the creation of new contacts.
    *   Renders `contact_form.html` (templates/contact_form.html).
*   **`/contacts/<int:contact_id>/edit` (edit\_contact):**
    *   Handles the editing of existing contacts via POST request.
    *   Returns JSON response indicating success or error.
*   **`/import-contacts` (import\_contacts):**
    *   Imports contacts from a CSV file.
*   **`/export-contacts` (export\_contacts):**
    *   Exports contacts to a CSV file.
*   **`/contacts/<int:contact_id>/delete` (delete\_contact):**
    *   Deletes a contact.

### Tasks Blueprint (`routes/tasks.py`)

*   **`/tasks` (tasks):**
    *   Displays a list of tasks.
    *   Supports filtering by 'all' (admin only) or 'my' tasks.
    *   Supports filtering by status (pending, completed, all).
    *   Renders `tasks.html` (templates/tasks.html).
*   **`/tasks/new` (create\_task):**
    *   Handles the creation of new tasks.
    *   Renders `create_task.html` (templates/create_task.html).
*   **`/tasks/<int:task_id>/edit` (edit\_task):**
    *   Handles the editing of existing tasks via POST request.
    *   Returns JSON response indicating success or error.
*   **`/tasks/<int:task_id>/delete` (delete\_task):**
    *   Deletes a task.
*   **`/tasks/types/<int:type_id>/subtypes` (get\_task\_subtypes):**
    *   Returns JSON data of subtypes for a given task type.
*   **`/tasks/<int:task_id>` (view\_task):**
    *   Displays details for a specific task.
    *   Returns JSON data if it's an AJAX request.
    *   Renders `view_task.html` (templates/view_task.html).
*   **`/tasks/<int:task_id>/quick-update` (quick\_update\_task):**
    *   Handles quick updates to task status and priority.

### Authentication Blueprint (`routes/auth.py`)

*   **`/register` (register):**
    *   Handles user registration.
    *   Renders `register.html` (templates/register.html).
*   **`/login` (login):**
    *   Handles user login.
    *   Renders `login.html` (templates/login.html).
*   **`/logout` (logout):**
    *   Handles user logout.
*   **`/profile` (view\_user\_profile):**
    *   Displays the current user's profile.
    *   Renders `user_profile.html` (templates/user_profile.html).
*   **`/profile/update` (update\_profile):**
    *   Handles updating the current user's profile information.
*   **`/reset_password` (reset\_request):**
    *   Handles password reset requests.
    *   Renders `reset_request.html` (templates/reset_request.html).
*   **`/reset_password/<token>` (reset\_password):**
    *   Handles password reset using a token.
    *   Renders `reset_password.html` (templates/reset_password.html).
*   **`/manage-users` (manage\_users):**
    *   Displays a list of all users (admin only).
    *   Renders `manage_users.html` (templates/manage_users.html).
*   **`/user/<int:user_id>/role` (update\_user\_role):**
    *   Handles updating a user's role (admin only).
*   **`/user/<int:user_id>/edit` (edit\_user):**
    *   Handles editing a user's profile (admin only).
*   **`/user/<int:user_id>/delete` (delete\_user):**
    *   Handles deleting a user (admin only).
*   **`/debug_users` (debug\_users):**
    *   Debug route to display all users.
*   **`/test_password/<username>/<password>` (test\_password):**
    *   Debug route to test password verification.

### AI Chat Blueprint (`routes/ai_chat.py`)

*   **`/api/ai-chat` (chat):**
    *   Handles AI chat interactions using centralized GPT service with fallback chain
    *   Requires authentication
    *   Processes contextual information:
        - Current page context and URL
        - Agent information (name, role)
        - Contact details when viewing contact records
        - Related tasks when viewing contacts
    *   Returns formatted AI responses with markdown support
    *   Comprehensive debug logging of context and responses
    *   Uses B.O.B. (Business Optimization Buddy) persona

### Action Plan Blueprint (`routes/action_plan.py`)

*   **`/action-plan` (action_plan):**
    *   Main questionnaire and plan display page
    *   Shows form for new users, displays existing plan for returning users
    *   Requires authentication
    *   Renders `action_plan.html`

*   **`/api/action-plan/submit` (submit_action_plan):**
    *   POST endpoint for questionnaire submission
    *   Processes form responses and generates AI action plan
    *   Uses centralized AI service with GPT-5.1 → GPT-5-mini → GPT-4o fallback
    *   Stores both questionnaire responses and generated plan in database
    *   Returns JSON with success status and generated plan content

*   **`/api/action-plan/retake` (retake_action_plan):**
    *   POST endpoint to clear existing plan for questionnaire retake
    *   Deletes current ActionPlan record for user
    *   Allows users to regenerate their plan with updated responses

*   **`/api/action-plan/get` (get_action_plan):**
    *   GET endpoint to retrieve user's current action plan
    *   Returns JSON with plan existence status, content, responses, and timestamps
    *   Used by frontend to display existing plans without regeneration

### Daily Todo Blueprint (`routes/daily_todo.py`)

*   **`/api/daily-todo/generate` (generate_todo):**
    *   POST endpoint to generate new daily todo list
    *   Optional `force` parameter to bypass 16-hour regeneration limit
    *   Gathers comprehensive CRM data:
        - Tasks by status (overdue, today, upcoming within 7 days)
        - Recent contacts (last 10 created)
        - Top commission opportunities (highest 5)
        - User information and current date context
    *   Uses centralized AI service with reasoning capabilities
    *   Stores generated todo list in DailyTodoList table
    *   Returns JSON with todo content and generation timestamp

*   **`/api/daily-todo/latest` (get_latest_todo):**
    *   GET endpoint to retrieve most recent todo list for user
    *   Returns 404 if no todo list exists
    *   Includes todo content and generation timestamp

### User Todo Blueprint (`routes/user_todo.py`)

*   **`/user_todo` (user_todo):**
    *   Personal todo list management page
    *   Allows users to create and manage their own todos
    *   Requires authentication
    *   Renders `user_todo.html`

*   **`/api/user_todos/get` (get_todos):**
    *   GET endpoint to retrieve user's active and completed todos
    *   Returns JSON with separate arrays for active and completed todos

*   **`/api/user_todos/save` (save_todos):**
    *   POST endpoint to save complete todo list state
    *   Accepts JSON with 'active' and 'completed' todo arrays
    *   Replaces all existing todos with new state (delete and recreate)
    *   Maintains order and completion status

### Company Updates Blueprint (`routes/company_updates.py`)

*   **`/updates` (list_updates):**
    *   Displays all company updates in reverse chronological order
    *   Shows engagement metrics (reaction counts, comment counts) for each update
    *   Requires authentication
    *   Renders `company_updates/list.html`

*   **`/updates/<int:update_id>` (view_update):**
    *   Displays individual company update with full content
    *   Tracks view analytics (one view per user per update)
    *   Shows navigation to previous/next updates
    *   Displays reactions, comments, and engagement data
    *   Admins see additional view analytics
    *   Renders `company_updates/view.html`

*   **`/updates/new` (create_update):**
    *   GET/POST route for creating new company updates
    *   Admin-only access
    *   Processes title, content (HTML from Quill.js), and optional cover image
    *   Auto-generates plain text excerpt from HTML content
    *   Renders `company_updates/form.html`

*   **`/updates/<int:update_id>/edit` (edit_update):**
    *   GET/POST route for editing existing updates
    *   Admin-only access
    *   Updates title, content, excerpt, and cover image
    *   Maintains update timestamp

*   **`/updates/<int:update_id>/delete` (delete_update):**
    *   POST route for deleting company updates
    *   Admin-only access
    *   Cascades to delete all reactions, comments, and views

*   **`/api/updates/latest` (get_latest_update):**
    *   API endpoint for dashboard teaser showing most recent update
    *   Returns JSON with title, excerpt, creation date, and author info

*   **`/api/updates/<int:update_id>/reactions` (toggle_reaction):**
    *   POST endpoint to add/remove emoji reactions
    *   Supports 5 reaction types: thumbs_up, heart, raised_hands, fire, clap
    *   Unique constraint: one reaction type per user per update
    *   Returns updated reaction counts and user reaction status

*   **`/api/updates/<int:update_id>/reactions` (get_reactions):**
    *   GET endpoint returning detailed reaction data
    *   Includes reaction counts and user lists for each reaction type

*   **`/api/updates/<int:update_id>/comments` (add_comment):**
    *   POST endpoint for adding comments to updates
    *   Validates content length (max 2000 characters)
    *   Returns comment data with user information

*   **`/api/updates/<int:update_id>/comments` (get_comments):**
    *   GET endpoint returning all comments for an update
    *   Includes comment metadata and deletion permissions

*   **`/api/updates/comments/<int:comment_id>` (delete_comment):**
    *   DELETE endpoint for removing comments
    *   Users can delete their own comments, admins can delete any

### Admin Blueprint (`routes/admin.py`)

*   **`/admin/groups` (manage_groups):**
    *   Admin-only page for managing contact groups
    *   Displays all groups organized by category
    *   Shows group counts and sort orders
    *   Renders `admin/groups.html`

*   **`/admin/groups/add` (add_group):**
    *   POST endpoint for creating new contact groups
    *   Auto-assigns sort order (highest in category + 1)
    *   Returns JSON with success status and group data

*   **`/admin/groups/<int:group_id>` (update_group):**
    *   PUT endpoint for updating group name, category, or sort order
    *   Accepts JSON with updated field values

*   **`/admin/groups/<int:group_id>` (delete_group):**
    *   DELETE endpoint for removing contact groups
    *   Note: May affect existing contacts with this group assignment

*   **`/admin/groups/reorder` (reorder_groups):**
    *   POST endpoint for drag-and-drop reordering of groups
    *   Updates sort_order for multiple groups simultaneously

### Marketing Blueprint (`routes/marketing.py`)

*   **`/marketing/templates` (templates_list):**
    *   Admin-only page for SendGrid template management
    *   Displays all synced templates with active status
    *   Shows template metadata (name, subject, version, etc.)
    *   Renders `marketing/templates.html`

*   **`/marketing/templates/<template_id>/toggle-status` (toggle_template_status):**
    *   POST endpoint to activate/deactivate templates
    *   Updates is_active flag in database

*   **`/marketing/templates/refresh` (refresh_templates):**
    *   POST endpoint to sync templates from SendGrid API
    *   Updates local database with latest template information
    *   Requires valid SendGrid API key configuration

*   **`/marketing/templates/preview/<template_id>` (preview_template):**
    *   GET endpoint to retrieve template preview URLs from SendGrid
    *   Returns JSON with preview_url for frontend display

### Transactions Blueprint (`routes/transactions.py`)

All routes protected by `@transactions_required` decorator which checks:
1. `TRANSACTIONS_ENABLED` feature flag is True
2. User is authenticated
3. User has 'admin' role

#### Transaction CRUD

*   **`/transactions/` (list_transactions):**
    *   Lists all transactions for the current user
    *   Supports filtering by status, type, and search query
    *   Search matches property address or participant names
    *   Renders `transactions/list.html`

*   **`/transactions/new` (new_transaction):**
    *   GET: Shows transaction creation form
    *   Renders `transactions/create.html`

*   **`/transactions/` (create_transaction - POST):**
    *   Creates new transaction with selected contacts as participants
    *   Automatically adds current user as listing/buyer's agent
    *   Redirects to transaction detail view

*   **`/transactions/<id>` (view_transaction):**
    *   Displays single transaction with participants and documents
    *   Shows document status and action buttons
    *   Renders `transactions/detail.html`

*   **`/transactions/<id>/edit` (edit_transaction):**
    *   GET: Shows edit form for transaction
    *   Renders `transactions/edit.html`

*   **`/transactions/<id>` (update_transaction - POST):**
    *   Updates transaction fields
    *   Handles date parsing for close dates

*   **`/transactions/<id>/delete` (delete_transaction - POST):**
    *   Deletes transaction and all related data (cascade)

*   **`/transactions/<id>/status` (update_status - POST):**
    *   AJAX endpoint to update transaction status
    *   Valid statuses: draft, active, pending, under_contract, closed, cancelled

#### Participant Management

*   **`/transactions/<id>/participants` (add_participant - POST):**
    *   Adds a participant to transaction
    *   Can link to existing contact or enter external party details
    *   Returns JSON with participant data

*   **`/transactions/<id>/participants/<participant_id>` (remove_participant - DELETE):**
    *   Removes participant from transaction
    *   Returns JSON success status

#### Intake Questionnaire

*   **`/transactions/<id>/intake` (intake_questionnaire - GET):**
    *   Shows the property intake questionnaire
    *   Schema loaded based on transaction type and ownership status
    *   Renders `transactions/intake.html`

*   **`/transactions/<id>/intake` (save_intake - POST):**
    *   Saves intake questionnaire answers to transaction.intake_data
    *   Supports both form submission and JSON

*   **`/transactions/<id>/intake/generate-package` (generate_document_package - POST):**
    *   Evaluates document rules against intake answers
    *   Creates TransactionDocument records for required documents
    *   Clears and regenerates if package already exists

#### Document Management

*   **`/transactions/<id>/documents` (add_document - POST):**
    *   Manually adds a document to the package
    *   Prevents duplicate documents

*   **`/transactions/<id>/documents/<doc_id>` (remove_document - DELETE):**
    *   Removes document from package

*   **`/transactions/<id>/documents/<doc_id>/form` (document_form - GET):**
    *   Displays form for filling document fields
    *   Routes to specialized templates based on document type:
        - `listing-agreement` → `listing_agreement_form.html` (wizard-style)
        - Other documents → `document_form.html` (generic)
    *   Prefills data from transaction, participants, and intake

*   **`/transactions/<id>/documents/<doc_id>/form` (save_document_form - POST):**
    *   Saves document form field data
    *   Updates document status to 'filled'

#### E-Signature (DocuSeal Integration)

*   **`/transactions/<id>/documents/<doc_id>/send` (send_for_signature - POST):**
    *   Sends document for e-signature via DocuSeal
    *   Builds submitters list from transaction participants
    *   Creates DocumentSignature records for each signer
    *   Updates document status to 'sent'

*   **`/transactions/<id>/documents/<doc_id>/status` (check_signature_status - GET):**
    *   Checks signature status from DocuSeal
    *   Returns signer status for each participant

*   **`/transactions/<id>/documents/<doc_id>/simulate-sign` (simulate_signature - POST):**
    *   Mock mode only: simulates signature completion for testing
    *   Updates document and signature statuses to 'signed'

*   **`/transactions/<id>/documents/<doc_id>/download` (download_signed_document - GET):**
    *   Gets download URLs for signed document
    *   Only available after document is fully signed

#### Webhook Endpoint

*   **`/transactions/webhook/docuseal` (docuseal_webhook - POST):**
    *   Receives webhooks from DocuSeal for signature events
    *   Event types: form.viewed, form.started, form.completed
    *   Updates document and signature statuses accordingly

#### API Endpoints

*   **`/transactions/api/contacts/search` (search_contacts - GET):**
    *   Contact picker search for transaction creation
    *   Returns JSON with matching contacts

## Forms

The application uses Flask-WTF for form handling:

*   **RegistrationForm (forms.py:5-14):** For user registration.
*   **LoginForm (forms.py:15-23):** For user login.
*   **ContactForm (forms.py:24-36):** For creating and editing contacts.
*   **RequestResetForm (forms.py:38-41):** For requesting a password reset.
*   **ResetPasswordForm (forms.py:43-47):** For resetting a password.

## Migrations

The codebase includes several migration scripts:

*   **`migrate_add_tasks.py`:** Populates initial task types and subtypes.
*   **`update_contact_groups.py`:** Migrates contact group relationships to a new association table.
*   **`adress_migration.py`:**  Appears to be a partial migration script related to address fields.
*   **`migrate_add_commsion.py`:** Adds a `potential_commission` field to the `Contact` model.
*   **`migrate_autoincrement.py`:** Migrates tables to use AUTOINCREMENT for primary keys.

## Initial Data

The `init_db.py` script initializes the database with:

*   Initial contact groups (Buyer, Seller, Rating, Network, Professional).
*   Initial task types (Call, Meeting, Email, Document) and their subtypes.
*   Initial transaction types (Seller, Buyer, Landlord, Tenant, Referral).

## Intake Schemas

The `intake_schemas/` directory contains JSON schema files that define property intake questionnaires for different transaction types:

### File Naming Convention
- `{transaction_type}_{ownership_status}.json` (e.g., `seller_conventional.json`)
- Loaded by `intake_service.get_intake_schema()`

### Current Schemas
- **`seller_conventional.json`:** Property questionnaire for conventional seller transactions

### Schema Structure
Each schema contains:
- `title`: Display title for the questionnaire
- `sections`: Array of question groups with questions
- `document_rules`: Conditional logic for document package generation

### Document Rules
Rules determine which documents to include based on intake answers:
- `always: true` - Always include this document
- `condition.field` + `condition.equals/in/not_equals` - Include if condition met

## Configuration

The `config.py` file contains application settings:

### Core Flask Settings
*   `SECRET_KEY`: For session management and security.
*   `FLASK_ENV`: Environment mode (development/production).
*   `SQLALCHEMY_DATABASE_URI`: Database connection string.
*   `SQLALCHEMY_TRACK_MODIFICATIONS`: Disable modification tracking.
*   `PERMANENT_SESSION_LIFETIME`: Session timeout (30 minutes).

### Mail Settings
*   `MAIL_SERVER`: SMTP server (default: smtp.gmail.com).
*   `MAIL_PORT`: SMTP port (default: 587).
*   `MAIL_USE_TLS`: TLS encryption (default: True).
*   `MAIL_USERNAME`: SMTP username.
*   `MAIL_PASSWORD`: SMTP password.
*   `MAIL_DEFAULT_SENDER`: Default sender tuple (name, email).
*   `MAIL_MAX_EMAILS`: Maximum emails per connection.
*   `MAIL_ASCII_ATTACHMENTS`: ASCII attachment handling.

### AI Integration Settings
*   `OPENAI_API_KEY`: OpenAI API key for GPT models (required for AI features).

### Email Marketing Settings
*   `SENDGRID_API_KEY`: SendGrid API key for template management.

### Environment-Specific Configuration

**Development Mode (`FLASK_ENV=development`):**
- Uses local Supabase PostgreSQL database
- SSL warnings disabled for local development

**Production Mode (`FLASK_ENV=production`):**
- Uses Supabase PostgreSQL via `DATABASE_URL` environment variable (contains Supabase connection string)
- Full SSL verification enabled
- Optimized for production deployment (PythonAnywhere)

## HTML Templates

The application uses Jinja2 templates for rendering HTML:

### Core Application Templates
*   **`base.html`:** Base template with common layout, navigation, and styles.
*   **`index.html`:** Displays the contact list with filtering and pagination.
*   **`dashboard.html`:** Main dashboard with metrics, recent tasks, and company updates.
*   **`login.html`:** User login form.
*   **`register.html`:** User registration form.
*   **`user_profile.html`:** User profile display and editing.
*   **`reset_request.html`:** Password reset request form.
*   **`reset_password.html`:** Password reset with token form.
*   **`manage_users.html`:** Admin interface for user management.

### Contact Management Templates
*   **`view_contact.html`:** Individual contact details and task history.
*   **`contact_form.html`:** Create/edit contact form.

### Task Management Templates
*   **`tasks.html`:** Task list with filtering and status management.
*   **`view_task.html`:** Individual task details.
*   **`create_task.html`:** Task creation form.

### AI Feature Templates
*   **`action_plan.html`:** 2026 Lead Generation Action Plan questionnaire and results.
*   **`admin_view_action_plan.html`:** Admin view of user action plans.

### User Todo Templates
*   **`user_todo.html`:** Personal todo list management interface.

### Company Updates Templates (`templates/company_updates/`)
*   **`list.html`:** Company updates feed with reactions and comments.
*   **`view.html`:** Individual update with full content and engagement.
*   **`form.html`:** Create/edit company updates (admin only, uses Quill.js editor).

### Marketing Templates (`templates/marketing/`)
*   **`templates.html`:** SendGrid template management interface.
*   **`marketing.html`:** Marketing campaign overview page.

### Transaction Templates (`templates/transactions/`)
*   **`list.html`:** Transaction list with filtering by status, type, and search.
*   **`create.html`:** Create new transaction form with contact selection.
*   **`detail.html`:** Transaction detail view with participants and document management.
*   **`edit.html`:** Edit transaction form.
*   **`intake.html`:** Property intake questionnaire form.
*   **`document_form.html`:** Generic document field fill form.
*   **`listing_agreement_form.html`:** Specialized wizard-style form for Listing Agreement with:
    - 14 organized sections (Parties, Property, Term, Pricing, etc.)
    - Conditional field visibility
    - Progress indicator
    - Inline helper text and tooltips
    - Floating action bar for Save/Continue
    - Prefilled data from transaction and participants

### Admin Templates (`templates/admin/`)
*   **`groups.html`:** Contact group management interface.

### Modal Templates (`templates/modals/`)
*   **`contact_modal.html`:** Quick contact editing modal.
*   **`daily_todo_modal.html`:** Daily todo list display modal.

## JavaScript Components

### Core Functionality (`static/js/base.js`)

*   **Mobile Search:** Responsive search functionality for mobile devices
*   **Modal Management:** Generic modal opening/closing utilities
*   **AJAX Utilities:** Common AJAX request handling and error management
*   **Form Handling:** Client-side form validation and submission
*   **UI Utilities:** Loading states, notifications, and responsive behaviors

### Contact Management (`inline in templates`)

*   **Contact Modals:** AJAX-powered contact editing and creation
*   **Contact Details:** Dynamic loading of contact information
*   **Contact Deletion:** Confirmation dialogs and AJAX deletion
*   **Contact Search:** Real-time filtering and pagination

### Task Management (`inline in templates`)

*   **Task Modals:** Task creation and editing interfaces
*   **Task Status Updates:** AJAX status and priority changes
*   **Task Subtypes:** Dynamic loading based on selected task type
*   **Task Filtering:** Status and assignment filtering

### AI Chat Widget (`static/js/ai_chat.js`)

*   **Features:**
    *   Floating chat icon with accessibility
    *   Expandable chat interface with smooth animations
    *   Real-time typing indicators
    *   Message history persistence within session
    *   Markdown formatting with syntax highlighting
    *   Error handling with automatic retry capability
    *   Responsive design for mobile and desktop
    *   Context-aware responses based on current page

*   **Key Functions:**
    *   `createChatIcon()`: Creates the floating chat button with positioning
    *   `createChatBox()`: Builds the complete chat interface
    *   `sendMessage()`: Handles message sending with context gathering
    *   `formatMessage()`: Processes markdown formatting and code blocks
    *   `showTypingIndicator()`: Shows AI thinking state with animations
    *   `addMessageToChat()`: Displays messages with proper styling
    *   `handleErrors()`: Graceful error handling and user feedback

### Action Plan (`static/js/action_plan.js`)

*   **Questionnaire Management:** Multi-step form handling with validation
*   **Progress Tracking:** Visual progress indicators and step navigation
*   **AI Generation:** Asynchronous plan generation with loading states
*   **Plan Display:** Formatted rendering of AI-generated action plans
*   **Retake Functionality:** Clear and restart questionnaire flow

### Daily Todo (`static/js/daily_todo.js`)

*   **Todo Generation:** AJAX requests for AI-powered todo creation
*   **Modal Display:** Formatted todo list presentation
*   **Regeneration Logic:** 16-hour cooldown management
*   **Markdown Rendering:** Task formatting with status indicators
*   **Responsive Layout:** Mobile-friendly todo display

### User Todo (`static/js/user_todo.js`)

*   **Drag & Drop:** Sortable todo list with visual feedback
*   **Real-time Sync:** Automatic saving of todo changes
*   **Status Management:** Active/completed todo separation
*   **Add/Edit/Delete:** Full CRUD operations with optimistic updates
*   **Persistence:** Local storage for offline capability

### Dashboard (`static/js/dashboard.js`)

*   **Metrics Display:** Dynamic loading of dashboard statistics
*   **Company Updates:** Latest update preview and navigation
*   **Task Previews:** Upcoming and overdue task summaries
*   **Real-time Updates:** Periodic refresh of dynamic content

### Admin Group Management (`static/js/manage_groups.js`)

*   **Drag & Drop Reordering:** Visual group reordering with persistence
*   **CRUD Operations:** Create, edit, delete groups with validation
*   **Category Management:** Group organization by category
*   **AJAX Updates:** Real-time updates without page refresh

### Agent Resources (`static/js/agent-resources.js`)

*   **Resource Loading:** Dynamic loading of agent resource content
*   **Search & Filter:** Resource discovery and filtering
*   **Bookmarking:** Save and organize useful resources
*   **Integration:** Links to external tools and services

## Styling

The application uses Tailwind CSS for utility-first styling with custom component classes and responsive design patterns.

### AI Chat Styles (`static/css/ai_chat.css`)

*   **Chat Interface Components:**
    *   Floating chat icon with hover animations and accessibility features
    *   Expandable chat container with smooth transitions
    *   Message bubbles with distinct styling for user/AI messages
    *   Input area with focus states and validation styling
    *   Typing indicator with animated dots
    *   Responsive mobile/desktop layouts

*   **Interactive Elements:**
    *   Hover and focus states for all interactive components
    *   Loading animations and state indicators
    *   Error state styling with clear visual feedback

*   **Theme Integration:**
    *   Origen Connect brand color scheme (#hex values)
    *   Consistent with main application UI components
    *   Dark/light mode support where applicable
    *   Accessible color contrast ratios
    *   Mobile-first responsive breakpoints

### Global Styling Architecture

**Tailwind CSS Framework:**
- Utility-first approach for rapid development
- Custom color palette matching brand guidelines
- Responsive grid and flexbox utilities
- Custom component classes for complex UI patterns

**Component-Specific Styling:**
- Modal overlays and dialog boxes
- Form inputs and validation states
- Button variants and interaction states
- Navigation and menu components
- Data tables and list layouts

**Responsive Design:**
- Mobile-first approach with progressive enhancement
- Tablet and desktop breakpoints
- Touch-friendly interactive elements
- Optimized layouts for different screen sizes

**Accessibility:**
- WCAG 2.1 AA compliance
- Keyboard navigation support
- Screen reader compatibility
- Focus management and visual indicators
- Reduced motion preferences support

## Summary

This CRM is a comprehensive real estate application with a clear separation of concerns. It provides a solid foundation for managing:

- **Contacts & Tasks:** Core CRM functionality for tracking clients and agent activities
- **AI-Powered Features:** B.O.B. chat assistant, action plan generator, and daily todo lists
- **Transaction Management:** Complete workflow from listing/purchase through close, including:
  - Transaction creation with participant management
  - Intake questionnaires for document package generation
  - Specialized wizard-style document forms (e.g., Listing Agreement)
  - E-signature integration via DocuSeal (mock mode available for testing)
- **Company Updates:** Internal communication and announcements
- **Marketing Integration:** SendGrid template management

The use of Flask, SQLAlchemy, Tailwind CSS, and JavaScript makes it a modern and maintainable project. The feature flag system allows safe rollout of new features, with Transaction Management currently enabled for admin users only.