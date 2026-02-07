# Document Creation Guide for CRM

This guide explains how to add new documents to the CRM system. Documents are integrated with DocuSeal for e-signatures and can be either **PDF-preview** (auto-populated, no UI) or **form-driven** (custom form UI with field mapping).

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Document Types](#document-types)
3. [Key Files & Locations](#key-files--locations)
4. [Creating a PDF-Preview Document](#creating-a-pdf-preview-document)
5. [Creating a Form-Driven Document](#creating-a-form-driven-document)
6. [YAML Definition Reference](#yaml-definition-reference)
7. [Field Resolution System](#field-resolution-system)
8. [Role Configuration](#role-configuration)
9. [Document Registry](#document-registry)
10. [Context & Organization Data](#context--organization-data)
11. [Testing Checklist](#testing-checklist)
12. [Advanced Patterns](#advanced-patterns)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Document Flow                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. YAML Definition (documents/*.yml)                           │
│           ↓                                                      │
│  2. DocumentLoader (loads on app startup)                       │
│           ↓                                                      │
│  3. FieldResolver (resolves field values from context)          │
│           ↓                                                      │
│  4. RoleBuilder (builds DocuSeal submitters with fields)        │
│           ↓                                                      │
│  5. DocuSealClient (creates preview/submission)                 │
│           ↓                                                      │
│  6. E-signature flow (Seller, Agent, etc.)                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow Context

When a document is rendered/previewed, a **context** dict is built:

```python
context = {
    'user': current_user,           # The logged-in agent (User model)
    'transaction': transaction,      # The Transaction model
    'form': doc.field_data or {},   # Saved form field values (JSON)
    'organization': current_user.organization  # Org-level settings
}
```

Fields in the YAML use **source paths** to reference this context:
- `user.email` → Agent's email
- `user.full_name` → Agent's first + last name
- `transaction.property_address` → Property street address
- `transaction.primary_seller.display_name` → Primary seller's name
- `form.list_price` → Value saved from a form field
- `organization.broker_name` → Org's broker name (for documents like Wire Fraud Warning)
- `static:Origen Realty` → Hardcoded literal value

---

## Document Types

### 1. PDF-Preview Documents

**Use when:** The document only needs auto-populated fields (no user input required).

**Examples:** IABS, Lead-Based Paint Disclosure, Wire Fraud Warning

**Characteristics:**
- `type: pdf-preview` in YAML
- No form template needed
- Fields auto-populate from user/transaction/organization data
- Appears as an embedded PDF preview in "Fill All Documents" view
- Status is set to `'filled'` automatically when created

### 2. Form-Driven Documents

**Use when:** The document needs custom form fields for user input.

**Examples:** Listing Agreement, HOA Addendum, Seller's Net Proceeds

**Characteristics:**
- `type: form-driven` in YAML
- Requires a form partial template (`templates/transactions/partials/{slug}_fields.html`)
- Fields map from form inputs to DocuSeal fields
- User fills out form, then document is generated with their values
- Status progresses: `pending` → `filled` → `generated` → `sent` → `signed`

---

## Key Files & Locations

| File/Directory | Purpose |
|----------------|---------|
| `documents/*.yml` | YAML document definitions (main config) |
| `documents/schema/v1.0.json` | JSON schema for validation |
| `services/documents/loader.py` | Loads & validates YAML on startup |
| `services/documents/field_resolver.py` | Resolves source paths to values |
| `services/documents/role_builder.py` | Builds DocuSeal submitters |
| `services/documents/types.py` | Type definitions (DocumentDefinition, etc.) |
| `services/documents/docuseal_client.py` | DocuSeal API wrapper |
| `services/document_registry.py` | UI config for documents (colors, icons) |
| `routes/transactions/documents.py` | Fill form routes |
| `routes/transactions/signing.py` | Preview & send routes |
| `routes/transactions/intake.py` | Document package generation |
| `templates/transactions/partials/` | Form field templates (form-driven only) |
| `templates/transactions/fill_all_documents.html` | Combined fill view |
| `templates/transactions/preview_all_documents.html` | Preview before sending |

---

## Creating a PDF-Preview Document

### Step-by-Step Example: Wire Fraud Warning

#### Step 1: Get DocuSeal Template Info

**Fetch fields and roles from the DocuSeal API** (requires `DOCUSEAL_API_KEY_PROD` or `DOCUSEAL_API_KEY_TEST` in `.env`):

```bash
curl -s -H "X-Auth-Token: YOUR_API_KEY" "https://api.docuseal.com/templates/TEMPLATE_ID" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print('=== ROLES ===')
for s in data.get('submitters', []):
    print(f\"  {s['name']}\")
print('=== FIELDS (by role) ===')
role_map = {s['uuid']: s['name'] for s in data.get('submitters', [])}
for f in data.get('fields', []):
    role = role_map.get(f.get('submitter_uuid', ''), 'Unknown')
    print(f\"  {role}: {f['name']} (type={f['type']}, readonly={f.get('readonly')})\")
"
```

Copy field names **exactly** from the API response—including Unicode characters (e.g., non-breaking space `\u00A0`). Fields with empty names cannot be mapped (schema requires `docuseal_field` minLength 1).

#### Step 2: Create YAML Definition

Create `documents/wire-fraud-warning.yml`:

```yaml
schema_version: "1.0"
slug: wire-fraud-warning
name: "Wire Fraud Warning"
docuseal_template_id: 2661511
type: pdf-preview

display:
  color: "#F43F5E"  # Rose/red color (hex)
  icon: "fas fa-exclamation-triangle"  # FontAwesome icon
  sort_order: 7  # Display order in lists

roles:
  # Agent role - must sign (NOT auto_complete)
  - role_key: agent
    docuseal_role: "Broker's Agent"  # Exact DocuSeal role name
    email_source: user.email
    name_source: user.full_name
    # auto_complete: false (default) - agent must sign

  # Primary seller - signs the document
  - role_key: seller
    docuseal_role: Seller
    email_source: transaction.primary_seller.display_email
    name_source: transaction.primary_seller.display_name

  # Optional second seller
  - role_key: seller_2
    docuseal_role: "Seller 2"
    email_source: transaction.sellers[1].display_email
    name_source: transaction.sellers[1].display_name
    optional: true  # Skip if no second seller

fields:
  # Pre-fill broker name from organization settings
  - field_key: broker_printed_name
    docuseal_field: "Broker's Printed Name"  # Exact DocuSeal field name
    role_key: agent
    source: organization.broker_name
```

#### Step 3: Add to Preview Document Registry

In `services/document_registry.py`, add to `PREVIEW_DOCUMENT_REGISTRY`:

```python
'wire-fraud-warning': PreviewDocumentConfig(
    slug='wire-fraud-warning',
    name='Wire Fraud Warning',
    docuseal_template_id=2661511,
    color='rose',  # Tailwind color name
    icon='fa-exclamation-triangle',
    sort_order=102,
    description='Wire Fraud Warning for Sellers'
),
```

#### Step 4: Restart App

The `DocumentLoader.load_all()` runs on app startup. Restart Flask to load the new YAML.

#### Step 5: Test

1. Create a seller transaction
2. The document should appear in "Fill All Documents" as a PDF preview
3. Verify fields are populated correctly
4. Test preview and send flow

---

## Creating a Form-Driven Document

### Step-by-Step Example: Listing Agreement

#### Step 1: Get DocuSeal Template Info

- Template ID: `2468023`
- Roles: `System` (auto-complete), `Seller`, `Seller 2` (optional), `Broker` (agent signs)
- Fields: 142 fields—see `documents/listing-agreement.yml` for full mapping reference

#### Step 2: Create YAML Definition

Create `documents/listing-agreement.yml`:

```yaml
schema_version: "1.0"
slug: listing-agreement
name: "Listing Agreement"
docuseal_template_id: 2468023
type: form-driven

display:
  color: "#F97316"  # Orange
  icon: "fas fa-file-contract"
  sort_order: 1

form:
  template: listing_agreement_form.html  # Full form template (optional)
  partial: listing_agreement_fields.html  # Fields partial for fill-all view

roles:
  - role_key: seller
    docuseal_role: Seller
    email_source: transaction.primary_seller.display_email
    name_source: transaction.primary_seller.display_name

  - role_key: seller_2
    docuseal_role: "Seller 2"
    email_source: transaction.sellers[1].display_email
    name_source: transaction.sellers[1].display_name
    optional: true

  - role_key: agent
    docuseal_role: Agent
    email_source: user.email
    name_source: user.full_name
    auto_complete: true  # Agent fields are pre-filled, auto-sign

fields:
  # From form inputs
  - field_key: list_price
    docuseal_field: "List Price"
    role_key: seller
    source: form.list_price
    transform: currency  # Apply currency formatting

  - field_key: property_address
    docuseal_field: "Property Address"
    role_key: seller
    source: transaction.full_address

  # Combined field example (multiple sources)
  - field_key: city_state_zip
    docuseal_field: "City, State, Zip"
    role_key: seller
    sources:
      - transaction.city
      - transaction.state
      - transaction.zip_code
    template: "{0}, {1} {2}"  # Positional placeholders

  # Conditional field (only if doc_responsibility == 'seller')
  - field_key: seller_pays_checkbox
    docuseal_field: "Seller Pays"
    role_key: seller
    source: form.doc_responsibility
    transform: checkbox
    condition_field: form.doc_responsibility
    condition_equals: seller
```

#### Step 3: Create Form Partial Template

Create `templates/transactions/partials/listing_agreement_fields.html`:

```html
<!-- Listing Agreement Fields -->
<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    <div>
        <label class="form-label">List Price</label>
        <input type="text" 
               name="doc_{{ doc.id }}_field_list_price" 
               value="{{ doc.field_data.list_price or prefill_data.list_price or '' }}"
               class="form-input"
               placeholder="$0.00">
    </div>
    
    <div>
        <label class="form-label">Listing Period (Days)</label>
        <input type="number" 
               name="doc_{{ doc.id }}_field_listing_days" 
               value="{{ doc.field_data.listing_days or '180' }}"
               class="form-input">
    </div>
    
    <!-- More fields... -->
</div>
```

**Important naming convention:** Form fields must be named `doc_{{ doc.id }}_field_{field_key}` for the save handler to properly parse them.

#### Step 4: Add to Document Registry

In `services/document_registry.py`, add to `DOCUMENT_REGISTRY`:

```python
'listing-agreement': DocumentConfig(
    slug='listing-agreement',
    name='Listing Agreement',
    partial_template='transactions/partials/listing_agreement_fields.html',
    color='orange',
    icon='fa-file-contract',
    sort_order=1,
),
```

#### Step 5: Restart and Test

---

## YAML Definition Reference

### Required Fields

```yaml
schema_version: "1.0"        # Always "1.0" for now
slug: my-document            # Unique kebab-case identifier
name: "My Document"          # Display name
docuseal_template_id: 123456 # DocuSeal template ID
type: pdf-preview            # or "form-driven"

display:
  color: "#HEX"              # Hex color for UI
  icon: "fas fa-icon"        # FontAwesome icon class
  sort_order: 1              # Lower = appears first

roles: [...]                 # At least one role
fields: [...]                # Zero or more fields
```

### Role Definition

```yaml
- role_key: seller           # Internal identifier (snake_case)
  docuseal_role: "Seller"    # EXACT name in DocuSeal template
  email_source: transaction.primary_seller.display_email
  name_source: transaction.primary_seller.display_name
  optional: false            # Default false. If true, skip when no email
  auto_complete: false       # Default false. If true, auto-sign (no email sent)
```

### Field Definition

```yaml
# Simple field
- field_key: my_field
  docuseal_field: "My Field"  # EXACT name in DocuSeal template
  role_key: seller
  source: form.my_field       # Source path (see below)
  transform: null             # Optional: currency, date_short, checkbox, phone

# Static value
- field_key: broker_name
  docuseal_field: "Broker"
  role_key: agent
  source: static:Origen Realty  # Literal value

# Manual entry (user fills in DocuSeal)
- field_key: seller_initials
  docuseal_field: "Seller Initials"
  role_key: seller
  source: null                # null = manual entry during signing

# Combined field (multiple sources)
- field_key: full_address
  docuseal_field: "Address"
  role_key: seller
  sources:
    - transaction.street_address
    - transaction.city
    - transaction.state
    - transaction.zip_code
  template: "{0}, {1}, {2} {3}"

# Conditional field (use static:X for yes/no checkboxes)
- field_key: option_checkbox
  docuseal_field: "Option A"
  role_key: seller
  source: static:X
  condition_field: form.selected_option
  condition_equals: "yes"

# Conditional with form value (e.g., commission amount)
- field_key: commission_amount
  docuseal_field: "Commission %"
  role_key: system
  source: form.total_commission
  condition_field: form.offer_buyer_agent_comp
  condition_equals: "yes"
```

### Source Path Syntax

| Path | Resolves To |
|------|-------------|
| `user.email` | Current user's email |
| `user.full_name` | Computed: `{first_name} {last_name}` |
| `user.phone` | User's phone |
| `user.license_number` | User's license number |
| `user.licensed_supervisor` | Supervisor name |
| `transaction.property_address` | Street address |
| `transaction.full_address` | Computed: full formatted address |
| `transaction.city` | City |
| `transaction.state` | State |
| `transaction.county` | County |
| `transaction.primary_seller.display_email` | Primary seller's email |
| `transaction.primary_seller.display_name` | Primary seller's name |
| `transaction.sellers[0]` | First seller (same as primary) |
| `transaction.sellers[1]` | Second seller |
| `form.field_name` | Value from `TransactionDocument.field_data` |
| `organization.broker_name` | Org's broker name |
| `organization.broker_license_number` | Org's broker license |
| `organization.broker_address` | Org's broker address |
| `static:literal value` | Hardcoded string |
| `null` | Manual entry (user fills during signing) |

### Transforms

| Transform | Description |
|-----------|-------------|
| `currency` | Formats as currency ($1,234.56) |
| `date_short` | Formats as MM/DD/YYYY |
| `checkbox` | Converts to "X" if truthy |
| `phone` | Formats phone number |

---

## Field Resolution System

The `FieldResolver` class (`services/documents/field_resolver.py`) handles:

1. **Parsing source paths** - Dot notation and bracket notation
2. **Resolving values** - Gets values from context dict
3. **Applying transforms** - Currency, date, checkbox formatting
4. **Handling conditions** - Only includes field if condition matches
5. **Combined fields** - Merges multiple sources with template

Example resolution:

```python
# Source: "transaction.sellers[1].display_email"
# Parses to: ['transaction', 'sellers[1]', 'display_email']
# Resolves: context['transaction'].sellers[1].display_email
```

---

## Role Configuration

### When to use `auto_complete: true`

Use for roles where ALL fields are:
- Pre-filled with readonly values
- Don't require manual signature

Example: Broker role in IABS where all broker info is static.

### When to use `optional: true`

Use for roles that may not exist:
- Seller 2 (only if there's a co-seller)
- Co-Buyer

The system checks if `email_source` resolves to a value. If not, the role is skipped.

### Role Mapping to Participants

| Transaction Participant | Typical DocuSeal Role |
|------------------------|----------------------|
| Primary seller (`role='seller', is_primary=True`) | "Seller" |
| Co-seller (`role='seller', is_primary=False`) | "Seller 2" |
| Listing agent (`role='listing_agent'`) | "Agent" or "Broker" |
| Buyer's agent | "Buyer's Agent" |

### The "System" Role Pattern

Some DocuSeal templates use a **System** role for fields that are auto-populated and never require human signing. Map these with `auto_complete: true`:

```yaml
- role_key: system
  docuseal_role: "System"
  email_source: user.email
  name_source: user.full_name
  auto_complete: true
```

All System role fields should have `source` or `sources` (no `source: null` for readonly text fields). Manual fields (initials, signatures) stay on Seller/Broker roles.

---

## Document Registry

Two registries in `services/document_registry.py`:

### DOCUMENT_REGISTRY (Form-Driven)

```python
'slug': DocumentConfig(
    slug='slug',
    name='Display Name',
    partial_template='transactions/partials/slug_fields.html',
    color='orange',  # Tailwind color
    icon='fa-icon',
    sort_order=1,
)
```

### PREVIEW_DOCUMENT_REGISTRY (PDF-Preview)

```python
'slug': PreviewDocumentConfig(
    slug='slug',
    name='Display Name',
    docuseal_template_id=123456,
    color='indigo',
    icon='fa-icon',
    sort_order=100,
    description='Optional description'
)
```

---

## Context & Organization Data

### Adding Organization-Level Fields

If your document needs org-level data (like broker info):

1. **Add fields to Organization model** (`models.py`):
   ```python
   broker_name = db.Column(db.String(200))
   ```

2. **Create migration** (`migrations/versions/add_xyz.py`)

3. **Add admin UI** to update the fields (profile page or org settings)

4. **Use in YAML**:
   ```yaml
   source: organization.broker_name
   ```

### Ensuring Context Includes Organization

**CRITICAL:** All places that build the field resolution context must include organization:

```python
context = {
    'user': current_user,
    'transaction': transaction,
    'form': doc.field_data or {},
    'organization': current_user.organization  # DON'T FORGET THIS!
}
```

Locations to check (search for `'form': doc.field_data`):
- `routes/transactions/documents.py` - Fill form views
- `routes/transactions/signing.py` - Preview and send
- `routes/transactions/intake.py` - Package generation
- `routes/transactions/download.py` - PDF download

---

## Testing Checklist

### For PDF-Preview Documents

- [ ] YAML file created in `documents/` folder
- [ ] Added to `PREVIEW_DOCUMENT_REGISTRY`
- [ ] App restarted to load YAML
- [ ] Document appears in "Fill All Documents" as PDF preview
- [ ] Fields are populated correctly from context
- [ ] Single document preview shows correct values
- [ ] Preview All shows correct values
- [ ] Send flow works - correct roles receive signing request
- [ ] Optional roles (Seller 2) handled correctly

### For Form-Driven Documents

- [ ] YAML file created in `documents/` folder
- [ ] Form partial template created in `templates/transactions/partials/`
- [ ] Added to `DOCUMENT_REGISTRY`
- [ ] App restarted
- [ ] Document appears in "Fill All Documents" with form fields
- [ ] Prefill data populates form correctly
- [ ] Save works - values stored in `field_data`
- [ ] Preview shows filled values
- [ ] Send flow works

### Common Issues

1. **Document not loading**: Check YAML syntax, restart app
2. **Fields not populated**: Check source path, ensure context includes organization
3. **Role not appearing**: Check `optional` flag, verify email resolves
4. **Wrong field in DocuSeal**: Verify `docuseal_field` matches EXACTLY (copy from API—including Unicode like `\u00A0` for non-breaking space)
5. **Transform not working**: Check transform name is valid
6. **Conditional checkbox not showing**: Use `source: static:X` with `condition_field`/`condition_equals`, not `source: form.x` + transform

### DocuSeal Mapping Checklist (Complex Documents)

When mapping a document with many fields (e.g., Listing Agreement):

1. **Fetch template via API**—get exact field names and roles; note which are readonly vs manual
2. **Define all 4 role types** if template has them: System (auto-complete), Seller, Seller 2 (optional), Broker (agent signs)
3. **Map readonly fields** to form/transaction/user/organization sources; use `source: null` for initials/signatures/dates
4. **Use conditional logic** for mutually exclusive options (e.g., MLS yes/no, 5A vs 5B compensation)—`condition_field` + `condition_equals` + `source: static:X`
5. **Repeated fields** (e.g., "Listing Concerning" on each page)—map all instances to the same combined source
6. **Skip empty-name fields**—DocuSeal templates may have unnamed fields; schema cannot map them

---

## Quick Reference: Adding a New Document

### PDF-Preview (No UI)

1. Create `documents/{slug}.yml` with `type: pdf-preview`
2. Add to `PREVIEW_DOCUMENT_REGISTRY` in `services/document_registry.py`
3. Restart app
4. Test

### Form-Driven (With UI)

1. Create `documents/{slug}.yml` with `type: form-driven`
2. Create `templates/transactions/partials/{slug}_fields.html`
3. Add to `DOCUMENT_REGISTRY` in `services/document_registry.py`
4. Restart app
5. Test

---

## Advanced Patterns

### Multiple DocuSeal Fields from Same Form Input

Sometimes a DocuSeal template has multiple fields that should contain the same value (e.g., "Declarant" and "Seller Name" both need the seller's full name). Instead of adding two form inputs, map both DocuSeal fields to the same form field:

```yaml
fields:
  # User fills this in the form
  - field_key: declarant
    docuseal_field: "Declarant"
    role_key: broker
    source: form.declarant

  # Same value, different DocuSeal field
  - field_key: seller_name
    docuseal_field: "Seller Name"
    role_key: broker
    source: form.declarant  # Same source as above!
```

The agent enters the name once, but it populates both fields in DocuSeal.

### Pulling Data from Other Documents

When one document needs data from another (e.g., T-47.1 needs property description from Listing Agreement), add logic to `routes/transactions/helpers.py` in the `build_prefill_data()` function:

```python
def build_prefill_data(transaction, participants):
    data = { ... }
    
    # Pull data from another document's field_data
    listing_agreement = TransactionDocument.query.filter_by(
        transaction_id=transaction.id,
        template_slug='listing-agreement'
    ).first()
    
    if listing_agreement and listing_agreement.field_data:
        la_data = listing_agreement.field_data
        # Build concatenated value from LA fields
        data['t47_property_description'] = build_property_description(la_data)
    
    return data
```

This prefills the T-47.1 form with data from the Listing Agreement when the page loads.

### Cross-Document Field Sync in Fill All Forms

When documents are displayed together in "Fill All Forms", you may want fields in one document to auto-update fields in another as the agent types. This requires JavaScript in `templates/transactions/fill_all_documents.html`.

**Example: Listing Agreement → T-47.1 Property Description**

```javascript
function initializeCrossDocSync() {
    // Find source fields (Listing Agreement)
    const laFields = {
        lot: document.querySelector('[name*="_field_legal_lot"]'),
        block: document.querySelector('[name*="_field_legal_block"]'),
        subdivision: document.querySelector('[name*="_field_legal_subdivision"]'),
        city: document.querySelector('[name*="_field_property_city"]'),
        county: document.querySelector('[name*="_field_property_county"]'),
        address: document.querySelector('[name*="_field_property_address"]')
    };
    
    // Find target field (T-47.1)
    const t47PropDesc = document.querySelector('[name*="t47"][name*="_field_property_description"]');
    
    if (!t47PropDesc) return;
    
    // Mark as auto-synced (respects manual edits)
    t47PropDesc.dataset.autoSynced = 'true';
    
    function buildAndSync() {
        if (t47PropDesc.dataset.autoSynced === 'false') return;
        
        const parts = [];
        if (laFields.lot?.value) parts.push(`Lot ${laFields.lot.value}`);
        if (laFields.block?.value) parts.push(`Block ${laFields.block.value}`);
        // ... build full string
        t47PropDesc.value = parts.join(', ');
    }
    
    // Listen to source field changes
    Object.values(laFields).forEach(field => {
        if (field) {
            field.addEventListener('input', buildAndSync);
        }
    });
    
    // Stop syncing if user manually edits target
    t47PropDesc.addEventListener('keydown', function() {
        this.dataset.autoSynced = 'false';
    });
    
    // Initial sync
    buildAndSync();
}
```

**Key patterns:**
- Use `[name*="_field_fieldname"]` to find fields regardless of document ID
- Track `dataset.autoSynced` to respect manual edits
- Use `keydown` (not `input`) to detect manual typing vs programmatic updates
- Run initial sync on page load

### Intake Schema Triggers

Documents are added to a transaction based on the intake questionnaire answers. Rules are defined in `intake_schemas/seller_conventional.json`:

```json
{
  "slug": "t47-affidavit",
  "name": "T-47.1 Residential Real Property Affidavit",
  "condition": {"field": "has_survey", "in": ["yes", "not_sure"]},
  "reason": "Seller has survey or is unsure"
}
```

Condition types:
- `{"field": "x", "equals": true}` - Boolean match
- `{"field": "x", "equals": "value"}` - Exact string match
- `{"field": "x", "in": ["a", "b"]}` - Value in list
- `"always": true` - Always include

### Document Color Palette

Use these Tailwind color names for visual consistency:

| Color | Use For | Hex |
|-------|---------|-----|
| `orange` | Listing Agreement (primary) | #f97316 |
| `violet` | HOA Addendum | #8b5cf6 |
| `emerald` | Seller's Net Proceeds | #10b981 |
| `blue` | Lead Paint Disclosure | #3b82f6 |
| `rose` | Wire Fraud Warning | #f43f5e |
| `amber` | T-47 Affidavit | #f59e0b |
| `cyan` | Flood Hazard | #06b6d4 |
| `indigo` | IABS | #6366f1 |
| `teal` | Static/Uploaded docs | #14b8a6 |
| `purple` | External docs | #8b5cf6 |

---

## Example: Complete PDF-Preview Document

See `documents/wire-fraud-warning.yml` for a complete example of a PDF-preview document that:
- Uses organization data (`organization.broker_name`)
- Has multiple roles (Agent, Seller, Seller 2)
- Agent must sign (not auto-complete)
- Seller 2 is optional

See `documents/iabs.yml` for a more complex example with:
- Static values for broker info
- User profile data for agent/supervisor
- Auto-complete for broker and agent roles
- Multiple pre-filled fields

See `documents/t47-affidavit.yml` for a form-driven document with:
- Broker role with `auto_complete: true` (agent fills form, no manual signing)
- Multiple DocuSeal fields mapped to same form input (`form.declarant` → "Declarant" and "Seller Name")
- Cross-document data pulling (property description from Listing Agreement)
- Real-time field sync in Fill All Forms (JavaScript in `fill_all_documents.html`)

See `documents/listing-agreement.yml` for a complex multi-role document with:
- System role (`auto_complete: true`) for 79 readonly auto-populated fields
- Seller and Seller 2 (optional) with pre-filled contact info + manual initials/signatures
- Broker role (agent signs)—no auto_complete
- Conditional fields for MLS, financing, compensation sections
- Combined sources for address fields
