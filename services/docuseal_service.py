# services/docuseal_service.py
"""
DocuSeal Integration Service

This service handles all DocuSeal API interactions for e-signatures.
Supports both test (sandbox) and production modes via environment variables.

Configuration:
- DOCUSEAL_API_KEY_TEST: API key for test/sandbox mode
- DOCUSEAL_API_KEY_PROD: API key for production
- DOCUSEAL_MODE: 'test' or 'prod' (defaults to 'test')

Field Mappings:
- Stored in YAML files at: docuseal_mappings/<template-slug>.yml
- Each template has its own mapping file
- See docuseal_mappings/listing-agreement.yml for format example
"""

import os
import json
import re
import requests
import logging
import yaml
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to mapping files
MAPPINGS_DIR = Path(__file__).parent.parent / 'docuseal_mappings'

# Configuration - supports test and production modes
DOCUSEAL_MODE = os.environ.get('DOCUSEAL_MODE', 'test').lower()
DOCUSEAL_API_KEY_TEST = os.environ.get('DOCUSEAL_API_KEY_TEST', '')
DOCUSEAL_API_KEY_PROD = os.environ.get('DOCUSEAL_API_KEY_PROD', '')

# Select the appropriate API key based on mode
if DOCUSEAL_MODE == 'prod':
    DOCUSEAL_API_KEY = DOCUSEAL_API_KEY_PROD
    DOCUSEAL_API_URL = 'https://api.docuseal.com'
else:
    DOCUSEAL_API_KEY = DOCUSEAL_API_KEY_TEST
    DOCUSEAL_API_URL = 'https://api.docuseal.com'  # Same URL for test mode, different key

# Mock mode is now controlled by whether we have a valid API key
DOCUSEAL_MOCK_MODE = not bool(DOCUSEAL_API_KEY)

# Map our template slugs to DocuSeal template IDs
# NOTE: Template IDs may differ between test and production environments
TEMPLATE_MAP = {
    'listing-agreement': 2468023,  # Listing Agreement template
    'hoa-addendum': 2469165,  # HOA Addendum template (https://docuseal.com/d/b3S4Ryi2HCjoh4)
    'iabs': 2508644,  # TXR-2501 Information About Brokerage Services (preview-only)
    'sellers-disclosure': None,
    'wire-fraud-warning': None,
    'lead-paint': None,
    'flood-hazard': None,
    'water-district': None,
    't47-affidavit': None,
    'seller-net-proceeds': None,
    'referral-agreement': None,
}

# =============================================================================
# DOCUMENT FORMS REGISTRY
# =============================================================================
# Maps template slugs to their fill form HTML templates for the Document Mapping UI.
# Used by admin to map form fields to DocuSeal fields.
DOCUMENT_FORMS = {
    'listing-agreement': {
        'name': 'Listing Agreement',
        'form_template': 'transactions/listing_agreement_form.html',
        'template_id': 2468023,
        'description': 'TXR-1101 Exclusive Right to Sell'
    },
    'hoa-addendum': {
        'name': 'HOA Addendum',
        'form_template': 'transactions/hoa_addendum_form.html',
        'template_id': 2469165,
        'description': 'Addendum for Property Subject to Mandatory Membership in HOA'
    },
    'flood-hazard': {
        'name': 'Flood Hazard Information',
        'form_template': 'transactions/flood_hazard_form.html',
        'template_id': None,  # Set when DocuSeal template is created
        'description': 'Information About Special Flood Hazard Areas'
    },
    'seller-net-proceeds': {
        'name': "Seller's Estimated Net Proceeds",
        'form_template': 'transactions/seller_net_proceeds_form.html',
        'template_id': None,  # Set when DocuSeal template is created
        'description': 'TXR-1935 Seller\'s Estimated Net Proceeds'
    },
    'iabs': {
        'name': 'Information About Brokerage Services',
        'form_template': None,  # Preview-only document, no form UI
        'template_id': 2508644,
        'description': 'TXR-2501 Information About Brokerage Services (auto-populated from agent profile)'
    },
}

# Path to templates directory
TEMPLATES_DIR = Path(__file__).parent.parent / 'templates'


def has_yaml_mapping(template_slug: str) -> bool:
    """Check if a YAML mapping file exists for the given template slug."""
    mapping_file = MAPPINGS_DIR / f"{template_slug}.yml"
    return mapping_file.exists()


def get_yaml_template_id(template_slug: str) -> Optional[int]:
    """Get the DocuSeal template ID from an existing YAML mapping file."""
    mapping = load_field_mapping(template_slug)
    if mapping:
        return mapping.get('template_id')
    return None


def get_full_yaml_mapping(template_slug: str) -> Optional[Dict[str, Any]]:
    """Load the complete YAML mapping file for a template."""
    return load_field_mapping(template_slug)


# =============================================================================
# DOCUMENT MAPPING UI FUNCTIONS
# =============================================================================
# These functions support the admin Document Mapping UI for mapping form fields
# to DocuSeal template fields.


def parse_form_fields(template_slug: str) -> List[Dict[str, Any]]:
    """
    Parse an HTML form template to extract all field definitions.
    
    Returns list of dicts with:
    - name: field name (without 'field_' prefix)
    - label: human-readable label from the form
    - html_type: detected HTML input type (text, radio, checkbox, date, textarea, select)
    - section: section number if detected
    
    Args:
        template_slug: Template identifier (e.g., 'listing-agreement')
        
    Returns:
        List of field definitions in order they appear in the form
    """
    if template_slug not in DOCUMENT_FORMS:
        logger.warning(f"Unknown template slug: {template_slug}")
        return []
    
    form_template = DOCUMENT_FORMS[template_slug]['form_template']
    template_path = TEMPLATES_DIR / form_template
    
    if not template_path.exists():
        logger.warning(f"Form template not found: {template_path}")
        return []
    
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        logger.error(f"Error reading template {template_path}: {e}")
        return []
    
    fields = []
    seen_fields = set()
    current_section = None
    
    # Track section numbers from section headers
    section_pattern = re.compile(r'data-section="(\d+)"')
    
    # Find all input/select/textarea elements with name="field_*"
    # Pattern matches: name="field_xxx" with surrounding context for type detection
    field_pattern = re.compile(
        r'(?:<label[^>]*>([^<]*)</label>\s*)?'  # Optional preceding label
        r'<(input|select|textarea)[^>]*name=["\']field_([a-zA-Z0-9_]+)["\'][^>]*>',
        re.IGNORECASE | re.DOTALL
    )
    
    # Also find labels that come after the input (common pattern)
    # Pattern: <input name="field_xxx"> ... <label>Label Text</label>
    reverse_label_pattern = re.compile(
        r'<(input|select|textarea)[^>]*name=["\']field_([a-zA-Z0-9_]+)["\'][^>]*>'
        r'(?:[^<]*<[^>]*>)*?\s*'  # Skip some tags
        r'<(?:span|div)[^>]*class="[^"]*option-label[^"]*"[^>]*>([^<]+)</(?:span|div)>',
        re.IGNORECASE | re.DOTALL
    )
    
    # Find form-label class labels followed by inputs
    label_input_pattern = re.compile(
        r'<label[^>]*class="[^"]*form-label[^"]*"[^>]*>([^<]+)</label>'
        r'[\s\S]*?<(input|select|textarea)[^>]*name=["\']field_([a-zA-Z0-9_]+)["\']',
        re.IGNORECASE
    )
    
    # Build a map of field names to labels using the label_input pattern
    label_map = {}
    for match in label_input_pattern.finditer(html_content):
        label_text = match.group(1).strip()
        field_name = match.group(3)
        # Clean up label text
        label_text = re.sub(r'\s+', ' ', label_text)
        label_text = label_text.replace('*', '').strip()
        if field_name not in label_map:
            label_map[field_name] = label_text
    
    # Find all field_* inputs in order
    input_pattern = re.compile(
        r'<(input|select|textarea)([^>]*)name=["\']field_([a-zA-Z0-9_]+)["\']([^>]*)>',
        re.IGNORECASE
    )
    
    # Track sections
    lines = html_content.split('\n')
    line_to_section = {}
    current_section = None
    for i, line in enumerate(lines):
        section_match = section_pattern.search(line)
        if section_match:
            current_section = section_match.group(1)
        line_to_section[i] = current_section
    
    # Find all fields in order
    for match in input_pattern.finditer(html_content):
        tag_type = match.group(1).lower()
        attrs_before = match.group(2)
        field_name = match.group(3)
        attrs_after = match.group(4)
        attrs = attrs_before + attrs_after
        
        if field_name in seen_fields:
            continue
        seen_fields.add(field_name)
        
        # Determine HTML type
        if tag_type == 'textarea':
            html_type = 'textarea'
        elif tag_type == 'select':
            html_type = 'select'
        else:
            # Check input type attribute
            type_match = re.search(r'type=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
            if type_match:
                html_type = type_match.group(1).lower()
            else:
                html_type = 'text'
        
        # Get label from our map
        label = label_map.get(field_name, field_name.replace('_', ' ').title())
        
        # Find which section this field is in based on position
        pos = match.start()
        line_num = html_content[:pos].count('\n')
        section = line_to_section.get(line_num, None)
        
        fields.append({
            'name': field_name,
            'label': label,
            'html_type': html_type,
            'section': section
        })
    
    logger.info(f"Parsed {len(fields)} fields from {template_slug}")
    return fields


def get_existing_mappings(template_slug: str) -> Dict[str, Dict[str, str]]:
    """
    Load existing field mappings from YAML to pre-fill the mapping UI.
    
    Handles multiple mapping types:
    - field_mappings: Direct field-to-field mappings
    - broker_form_fields: Fields for broker submitter
    - computed_fields: Fields combined via templates
    - conditional_fields: Nested mappings based on conditions
    - option_transforms: Radio/checkbox option mappings
    
    Returns dict mapping field_name -> {'docuseal_field': '...', 'type': '...'}
    """
    mapping = load_field_mapping(template_slug)
    if not mapping:
        return {}
    
    result = {}
    
    # Get field_mappings
    field_mappings = mapping.get('field_mappings', {})
    transforms = mapping.get('transforms', {})
    
    # Determine types from transforms
    currency_fields = set(transforms.get('currency_fields', []))
    date_fields = set(transforms.get('date_fields', []))
    radio_mappings = transforms.get('radio_mappings', {})
    checkbox_to_x = set(transforms.get('checkbox_to_x', []))
    
    def get_field_type(field_name):
        """Determine field type from transforms."""
        if field_name in currency_fields:
            return 'currency'
        elif field_name in date_fields:
            return 'date'
        elif field_name in radio_mappings:
            return 'radio'
        elif field_name in checkbox_to_x:
            return 'checkbox'
        return 'text'
    
    # Process direct field_mappings
    for field_name, docuseal_field in field_mappings.items():
        if not docuseal_field:
            continue
        
        result[field_name] = {
            'docuseal_field': docuseal_field,
            'type': get_field_type(field_name)
        }
    
    # Process broker_form_fields
    broker_fields = mapping.get('broker_form_fields', {})
    for field_name, docuseal_field in broker_fields.items():
        if not docuseal_field or field_name in result:
            continue
        
        result[field_name] = {
            'docuseal_field': docuseal_field,
            'type': get_field_type(field_name)
        }
    
    # Process computed_fields - extract field names from template strings
    # e.g., template: "{hoa_name} - {hoa_phone}" -> hoa_name and hoa_phone are mapped
    computed_fields = mapping.get('computed_fields', [])
    for computed in computed_fields:
        if not isinstance(computed, dict):
            continue
        docuseal_field = computed.get('docuseal_field', '')
        template = computed.get('template', '')
        
        # Extract field names from template like "{field_name}"
        field_names = re.findall(r'\{([a-zA-Z0-9_]+)\}', template)
        for field_name in field_names:
            if field_name not in result:
                result[field_name] = {
                    'docuseal_field': f'(computed) {docuseal_field}',
                    'type': 'computed'
                }
    
    # Process conditional_fields - nested structure
    # e.g., doc_responsibility: { seller: { seller_delivery_days: "1. Days..." } }
    conditional_fields = mapping.get('conditional_fields', {})
    for condition_field, conditions in conditional_fields.items():
        if not isinstance(conditions, dict):
            continue
        
        # The condition_field itself has option-based mappings
        if condition_field not in result:
            result[condition_field] = {
                'docuseal_field': '(conditional)',
                'type': 'radio'
            }
        
        # Extract the nested field mappings
        for condition_value, field_map in conditions.items():
            if not isinstance(field_map, dict):
                continue
            for field_name, docuseal_field in field_map.items():
                if field_name not in result:
                    result[field_name] = {
                        'docuseal_field': f'(when {condition_field}={condition_value}) {docuseal_field}',
                        'type': get_field_type(field_name)
                    }
    
    # Process option_transforms - radio/checkbox transformations
    # e.g., doc_responsibility: { seller: "Option 1", buyer: "Option 2" }
    option_transforms = mapping.get('option_transforms', {})
    for field_name, options in option_transforms.items():
        if not isinstance(options, dict):
            continue
        if field_name not in result:
            # Get the first option value as an example
            first_option = next(iter(options.values()), '')
            result[field_name] = {
                'docuseal_field': f'(options) {first_option}...',
                'type': 'radio'
            }
    
    return result


def save_field_mappings(template_slug: str, mappings: List[Dict[str, Any]]) -> bool:
    """
    Save field mappings to YAML file.
    
    Args:
        template_slug: Template identifier
        mappings: List of dicts with 'name', 'docuseal_field', 'type'
        
    Returns:
        True if successful, False otherwise
    """
    if template_slug not in DOCUMENT_FORMS:
        logger.error(f"Unknown template slug: {template_slug}")
        return False
    
    doc_info = DOCUMENT_FORMS[template_slug]
    mapping_file = MAPPINGS_DIR / f"{template_slug}.yml"
    
    # Build the YAML structure
    field_mappings = {}
    currency_fields = []
    date_fields = []
    radio_mappings = {}
    checkbox_to_x = []
    
    for m in mappings:
        field_name = m.get('name', '')
        docuseal_field = m.get('docuseal_field', '').strip()
        field_type = m.get('type', 'text')
        html_type = m.get('html_type', '')  # HTML input type from form parsing
        
        if not field_name or not docuseal_field:
            continue
        
        field_mappings[field_name] = docuseal_field
        
        # Add to appropriate transform list based on type
        if field_type == 'currency':
            currency_fields.append(field_name)
        elif field_type == 'date':
            date_fields.append(field_name)
        elif field_type == 'radio':
            # Create a placeholder radio mapping
            radio_mappings[field_name] = {
                'yes': 'yes',
                'no': 'no'
            }
        elif field_type == 'checkbox' or html_type == 'checkbox':
            # Checkbox fields get converted to "X" for DocuSeal
            checkbox_to_x.append(field_name)
    
    # Build the YAML content
    yaml_content = f"""# =============================================================================
# {doc_info['name'].upper()} - DocuSeal Field Mapping
# =============================================================================
# This file maps CRM form fields to DocuSeal template fields.
#
# Template ID: {doc_info['template_id']}
# Generated by Document Mapping UI
# =============================================================================

template_id: {doc_info['template_id']}
template_name: "{doc_info['name']}"
template_slug: "{template_slug}"

# Submitter roles for this template
submitter_roles:
  - Seller
  - Broker

# =============================================================================
# FIELD MAPPINGS
# Format: crm_field: docuseal_field
# =============================================================================
field_mappings:
"""
    
    # Add field mappings
    for field_name, docuseal_field in field_mappings.items():
        yaml_content += f'  {field_name}: "{docuseal_field}"\n'
    
    # Add transforms section
    yaml_content += """
# =============================================================================
# VALUE TRANSFORMS
# =============================================================================
transforms:
"""
    
    # Radio mappings
    if radio_mappings:
        yaml_content += "  radio_mappings:\n"
        for field_name, mapping in radio_mappings.items():
            yaml_content += f"    {field_name}:\n"
            for k, v in mapping.items():
                yaml_content += f'      "{k}": "{v}"\n'
    else:
        yaml_content += "  radio_mappings: {}\n"
    
    # Currency fields
    yaml_content += "\n  currency_fields:\n"
    if currency_fields:
        for f in currency_fields:
            yaml_content += f"    - {f}\n"
    else:
        yaml_content += "    []\n"
    
    # Date fields
    yaml_content += "\n  date_fields:\n"
    if date_fields:
        for f in date_fields:
            yaml_content += f"    - {f}\n"
    else:
        yaml_content += "    []\n"
    
    # Checkbox to X fields (auto-detected from checkbox type fields)
    yaml_content += "\n  checkbox_to_x:\n"
    if checkbox_to_x:
        for f in checkbox_to_x:
            yaml_content += f"    - {f}\n"
    else:
        yaml_content += "    []\n"
    
    # Add placeholder sections
    yaml_content += """
# =============================================================================
# BROKER FORM FIELDS (fields that go to Broker submitter)
# =============================================================================
broker_form_fields: {}

# =============================================================================
# AGENT FIELDS (auto-populated from logged-in agent)
# =============================================================================
agent_fields: []

# =============================================================================
# COMPUTED FIELDS (combine multiple form values)
# =============================================================================
computed_fields: []
"""
    
    try:
        with open(mapping_file, 'w', encoding='utf-8') as f:
            f.write(yaml_content)
        
        # Clear the cache so changes are picked up
        if template_slug in _mapping_cache:
            del _mapping_cache[template_slug]
        
        logger.info(f"Saved field mappings to {mapping_file}")
        return True
        
    except Exception as e:
        logger.error(f"Error saving mappings to {mapping_file}: {e}")
        return False


def save_full_mapping(template_slug: str, mapping_data: Dict[str, Any]) -> bool:
    """
    Save complete mapping data including all mapping types to YAML file.
    
    This is the new save function that supports:
    - Direct field mappings (1:1)
    - Combined/computed fields (one DocuSeal field -> multiple CRM fields)
    - Conditional fields (mappings that depend on another field's value)
    - Option transforms (radio/checkbox value to DocuSeal field mapping)
    
    Args:
        template_slug: Template identifier
        mapping_data: Dict with template_id, field_mappings, computed_fields, 
                     conditional_fields, option_transforms, transforms
    
    Returns:
        True if successful, False otherwise
    """
    if template_slug not in DOCUMENT_FORMS:
        logger.error(f"Unknown template slug: {template_slug}")
        return False
    
    doc_info = DOCUMENT_FORMS[template_slug]
    mapping_file = MAPPINGS_DIR / f"{template_slug}.yml"
    
    template_id = mapping_data.get('template_id', doc_info.get('template_id', 0))
    
    # Get transforms from mapping data
    transforms = mapping_data.get('transforms', {
        'currency_fields': [],
        'date_fields': [],
        'radio_mappings': {},
        'checkbox_to_x': []
    })
    
    # Auto-detect checkbox fields from field_types if provided
    # field_types is a dict of {field_name: html_type} passed from the UI
    field_types = mapping_data.get('field_types', {})
    checkbox_to_x = list(transforms.get('checkbox_to_x', []))
    for field_name, html_type in field_types.items():
        if html_type == 'checkbox' and field_name not in checkbox_to_x:
            checkbox_to_x.append(field_name)
    transforms['checkbox_to_x'] = checkbox_to_x
    
    # Build YAML structure
    yaml_data = {
        'template_id': template_id,
        'template_name': doc_info['name'],
        'template_slug': template_slug,
        'submitter_roles': ['Seller', 'Seller 2'],
        'field_mappings': mapping_data.get('field_mappings', {}),
        'computed_fields': mapping_data.get('computed_fields', []),
        'conditional_fields': mapping_data.get('conditional_fields', {}),
        'option_transforms': mapping_data.get('option_transforms', {}),
        'transforms': transforms,
        'broker_form_fields': {},
        'agent_fields': []
    }
    
    # Build human-readable YAML with comments
    yaml_content = f"""# =============================================================================
# {doc_info['name'].upper()} - DocuSeal Field Mapping
# =============================================================================
# This file maps CRM form fields to DocuSeal template fields.
# Generated by the Document Mapping UI
#
# Template ID: {template_id}
# =============================================================================

template_id: {template_id}
template_name: "{doc_info['name']}"
template_slug: "{template_slug}"

# Submitter roles for this template
submitter_roles:
  - Seller
  - Seller 2

# =============================================================================
# DIRECT FIELD MAPPINGS
# Format: crm_field: "DocuSeal Field Name"
# =============================================================================
field_mappings:
"""
    
    # Add direct field mappings
    for crm_field, docu_field in yaml_data['field_mappings'].items():
        if docu_field:  # Only include non-empty mappings
            yaml_content += f'  {crm_field}: "{docu_field}"\n'
    
    # Add computed fields section
    yaml_content += """
# =============================================================================
# COMPUTED/COMBINED FIELDS
# One DocuSeal field populated from multiple CRM fields
# =============================================================================
computed_fields:
"""
    
    if yaml_data['computed_fields']:
        for cf in yaml_data['computed_fields']:
            yaml_content += f"  - docuseal_field: \"{cf.get('docuseal_field', '')}\"\n"
            yaml_content += f"    template: \"{cf.get('template', '')}\"\n"
    else:
        yaml_content += "  []\n"
    
    # Add conditional fields section
    yaml_content += """
# =============================================================================
# CONDITIONAL FIELDS
# Fields that are mapped based on another field's value
# =============================================================================
conditional_fields:
"""
    
    if yaml_data['conditional_fields']:
        for cond_field, conditions in yaml_data['conditional_fields'].items():
            yaml_content += f"  {cond_field}:\n"
            for cond_value, field_map in conditions.items():
                yaml_content += f"    {cond_value}:\n"
                for crm_field, docu_field in field_map.items():
                    yaml_content += f'      {crm_field}: "{docu_field}"\n'
    else:
        yaml_content += "  {}\n"
    
    # Add option transforms section
    yaml_content += """
# =============================================================================
# OPTION TRANSFORMS
# Radio/checkbox values mapped to DocuSeal checkbox fields
# =============================================================================
option_transforms:
"""
    
    if yaml_data['option_transforms']:
        for crm_field, options in yaml_data['option_transforms'].items():
            yaml_content += f"  {crm_field}:\n"
            for opt_value, docu_field in options.items():
                yaml_content += f'    {opt_value}: "{docu_field}"\n'
    else:
        yaml_content += "  {}\n"
    
    # Add transforms section
    yaml_content += """
# =============================================================================
# VALUE TRANSFORMS
# =============================================================================
transforms:
"""
    
    transforms = yaml_data.get('transforms', {})
    
    # Currency fields
    currency_fields = transforms.get('currency_fields', [])
    yaml_content += "  currency_fields:\n"
    if currency_fields:
        for f in currency_fields:
            yaml_content += f"    - {f}\n"
    else:
        yaml_content += "    []\n"
    
    # Date fields
    date_fields = transforms.get('date_fields', [])
    yaml_content += "  date_fields:\n"
    if date_fields:
        for f in date_fields:
            yaml_content += f"    - {f}\n"
    else:
        yaml_content += "    []\n"
    
    # Radio mappings
    radio_mappings = transforms.get('radio_mappings', {})
    yaml_content += "  radio_mappings:\n"
    if radio_mappings:
        for field_name, mapping in radio_mappings.items():
            yaml_content += f"    {field_name}:\n"
            for k, v in mapping.items():
                yaml_content += f'      "{k}": "{v}"\n'
    else:
        yaml_content += "    {}\n"
    
    # Checkbox to X
    checkbox_to_x = transforms.get('checkbox_to_x', [])
    yaml_content += "  checkbox_to_x:\n"
    if checkbox_to_x:
        for f in checkbox_to_x:
            yaml_content += f"    - {f}\n"
    else:
        yaml_content += "    []\n"
    
    # Add broker and agent fields
    yaml_content += """
# =============================================================================
# BROKER FORM FIELDS (fields that go to Broker submitter)
# =============================================================================
broker_form_fields: {}

# =============================================================================
# AGENT FIELDS (auto-populated from logged-in agent)
# =============================================================================
agent_fields: []
"""
    
    try:
        with open(mapping_file, 'w', encoding='utf-8') as f:
            f.write(yaml_content)
        
        # Clear the cache so changes are picked up
        if template_slug in _mapping_cache:
            del _mapping_cache[template_slug]
        
        logger.info(f"Saved full field mappings to {mapping_file}")
        return True
        
    except Exception as e:
        logger.error(f"Error saving mappings to {mapping_file}: {e}")
        return False


# =============================================================================
# FIELD MAPPING LOADER
# =============================================================================
# Field mappings are stored in YAML files at: docuseal_mappings/<template-slug>.yml
# This keeps the service code clean and makes mappings easier to maintain.
# 
# Each YAML file contains:
#   - template_id: DocuSeal template ID
#   - field_mappings: Dict mapping our form fields to DocuSeal fields
#   - transforms: Rules for value transformation (radio mappings, currency, dates)
#   - agent_fields: Fields to populate from logged-in agent
#   - computed_fields: Fields that combine multiple form values
# =============================================================================

# Cache for loaded mappings
_mapping_cache: Dict[str, Dict[str, Any]] = {}


def load_field_mapping(template_slug: str) -> Optional[Dict[str, Any]]:
    """
    Load field mapping from YAML file for a template.
    
    Args:
        template_slug: Template identifier (e.g., 'listing-agreement')
        
    Returns:
        Dict with field_mappings, transforms, agent_fields, etc.
        Returns None if no mapping file exists.
    """
    # Check cache first
    if template_slug in _mapping_cache:
        return _mapping_cache[template_slug]
    
    # Load from YAML file
    mapping_file = MAPPINGS_DIR / f"{template_slug}.yml"
    
    if not mapping_file.exists():
        logger.warning(f"No field mapping file found: {mapping_file}")
        return None
    
    try:
        with open(mapping_file, 'r') as f:
            mapping = yaml.safe_load(f)
        
        # Cache it
        _mapping_cache[template_slug] = mapping
        logger.info(f"Loaded field mapping for {template_slug}")
        return mapping
        
    except Exception as e:
        logger.error(f"Error loading field mapping for {template_slug}: {e}")
        return None


def get_field_mappings(template_slug: str) -> Dict[str, str]:
    """Get just the field mappings dict from a template's YAML config."""
    mapping = load_field_mapping(template_slug)
    if mapping:
        return mapping.get('field_mappings', {})
    return {}


def get_mapping_file_path(template_slug: str) -> str:
    """Get the path to the mapping file for a template (for display in admin)."""
    return str(MAPPINGS_DIR / f"{template_slug}.yml")


def get_template_submitter_roles(template_slug: str) -> List[str]:
    """
    Get the submitter roles for a template from YAML config.
    
    Returns list like ['Seller', 'Broker'] or ['Seller', 'Buyer'].
    Defaults to ['Seller', 'Broker'] if not specified in YAML.
    """
    mapping = load_field_mapping(template_slug)
    if mapping:
        roles = mapping.get('submitter_roles')
        if roles:
            return roles
    # Default to Seller + Broker for backwards compatibility
    return ['Seller', 'Broker']


def _format_date(date_str: str) -> str:
    """Convert date to MM/DD/YYYY format for DocuSeal."""
    if not date_str:
        return ''
    try:
        # Handle YYYY-MM-DD format from HTML date inputs
        if isinstance(date_str, str) and '-' in date_str:
            d = datetime.strptime(date_str, '%Y-%m-%d')
            return d.strftime('%m/%d/%Y')
        return date_str
    except:
        return date_str


def _format_currency(value: Any) -> str:
    """Strip currency formatting ($, commas) for DocuSeal number fields."""
    if not value:
        return ''
    return str(value).replace(',', '').replace('$', '').strip()


def _apply_transform(value: Any, field_name: str, transforms: Dict[str, Any]) -> str:
    """
    Apply transformation rules from YAML config to a field value.
    
    Handles:
    - checkbox_to_x: Convert truthy values to "X", falsy to "" (configured in YAML)
    - radio_mappings: Map our values to DocuSeal values
    - currency_fields: Strip $ and commas
    - date_fields: Convert to MM/DD/YYYY
    
    Note: Checkbox fields are automatically added to checkbox_to_x when the YAML
    is saved via the Document Mapping UI (see save_full_mapping function).
    """
    if not transforms:
        return str(value) if value else ''
    
    # Check if it's a checkbox_to_x field (convert true/yes/1 to "X")
    checkbox_to_x_fields = transforms.get('checkbox_to_x') or []
    if field_name in checkbox_to_x_fields:
        # Convert various truthy values to "X"
        if value in [True, 'true', 'True', 'yes', 'Yes', '1', 1, 'on', 'On', 'X', 'x']:
            return 'X'
        else:
            return ''
    
    # Check if it's a currency field
    currency_fields = transforms.get('currency_fields') or []
    if field_name in currency_fields:
        return _format_currency(value)
    
    # Check if it's a date field
    date_fields = transforms.get('date_fields') or []
    if field_name in date_fields:
        return _format_date(value)
    
    # Check for radio mapping
    radio_mappings = transforms.get('radio_mappings') or {}
    if field_name in radio_mappings:
        mapping = radio_mappings[field_name]
        str_value = str(value).lower() if value else ''
        return mapping.get(str_value, str(value) if value else '')
    
    # No transformation needed
    return str(value) if value else ''


def build_docuseal_fields(form_data: Dict[str, Any], template_slug: str, agent_data: Dict[str, Any] = None, submitter_role: str = None) -> List[Dict[str, Any]]:
    """
    Convert CRM form data to DocuSeal fields format.
    
    Loads field mappings from YAML file at: docuseal_mappings/<template_slug>.yml
    
    Args:
        form_data: Dict of form field names to values (from TransactionDocument.field_data)
        template_slug: The template being filled (e.g., 'listing-agreement')
        agent_data: Optional dict with agent info (name, email, license_number, phone)
        submitter_role: Optional role filter - 'Seller' returns only seller fields, 
                       'Broker' returns only broker/agent fields, None returns all
    
    Returns:
        List of dicts with 'name' and 'default_value' for DocuSeal API
    """
    fields = []
    
    # Load mapping from YAML file
    mapping = load_field_mapping(template_slug)
    if not mapping:
        logger.warning(f"No field mapping found for template: {template_slug}")
        return fields
    
    # For Broker submitter, return agent_fields + broker_form_fields
    if submitter_role == 'Broker':
        transforms = mapping.get('transforms') or {}
        
        # 1. Add agent auto-fill fields
        agent_field_configs = mapping.get('agent_fields') or []
        if agent_data and agent_field_configs:
            for config in agent_field_configs:
                docuseal_name = config.get('docuseal_field')
                source = config.get('source', '')
                
                if docuseal_name and source:
                    parts = source.split('.')
                    if len(parts) == 2 and parts[0] == 'agent':
                        value = agent_data.get(parts[1], '')
                        if value:
                            fields.append({
                                'name': docuseal_name,
                                'default_value': str(value)
                            })
        elif agent_data and not agent_field_configs:
            # Fallback standard agent fields
            standard_agent_fields = [
                ('Broker printed name', agent_data.get('name', '')),
                ('Broker associate printed name', agent_data.get('name', '')),
                ('Broker license number', agent_data.get('license_number', '')),
                ('Broker associate license', agent_data.get('license_number', '')),
                ('Broker email/fax', agent_data.get('email', '')),
            ]
            for docuseal_name, value in standard_agent_fields:
                if value:
                    fields.append({
                        'name': docuseal_name,
                        'default_value': str(value)
                    })
        
        # 2. Add broker form fields (form data that goes to Broker submitter)
        broker_form_fields = mapping.get('broker_form_fields') or {}
        for form_field, docuseal_field in broker_form_fields.items():
            if not docuseal_field:
                continue
            if form_field in form_data and form_data[form_field]:
                value = form_data[form_field]
                transformed_value = _apply_transform(value, form_field, transforms)
                if transformed_value:
                    fields.append({
                        'name': docuseal_field,
                        'default_value': transformed_value
                    })
        
        logger.info(f"Built {len(fields)} DocuSeal Broker fields")
        return fields
    
    # For Buyer submitter, return buyer_form_fields (used by HOA Addendum etc.)
    if submitter_role == 'Buyer':
        transforms = mapping.get('transforms') or {}
        
        # Add buyer form fields (form data that goes to Buyer submitter)
        buyer_form_fields = mapping.get('buyer_form_fields') or {}
        for form_field, docuseal_field in buyer_form_fields.items():
            if not docuseal_field:
                continue
            if form_field in form_data and form_data[form_field]:
                value = form_data[form_field]
                transformed_value = _apply_transform(value, form_field, transforms)
                if transformed_value:
                    fields.append({
                        'name': docuseal_field,
                        'default_value': transformed_value
                    })
        
        logger.info(f"Built {len(fields)} DocuSeal Buyer fields")
        return fields
    
    # For Seller 2, Seller 3, etc. - these typically only have signature fields
    # No pre-filled text fields needed, but check seller_2_fields in YAML if defined
    if submitter_role and submitter_role.startswith('Seller ') and submitter_role != 'Seller':
        transforms = mapping.get('transforms') or {}
        
        # Check for role-specific fields (e.g., seller_2_fields, seller_3_fields)
        role_key = submitter_role.lower().replace(' ', '_') + '_fields'  # "Seller 2" -> "seller_2_fields"
        role_fields = mapping.get(role_key) or {}
        
        for form_field, docuseal_field in role_fields.items():
            if not docuseal_field:
                continue
            if form_field in form_data and form_data[form_field]:
                value = form_data[form_field]
                transformed_value = _apply_transform(value, form_field, transforms)
                if transformed_value:
                    fields.append({
                        'name': docuseal_field,
                        'default_value': transformed_value
                    })
        
        logger.info(f"Built {len(fields)} DocuSeal {submitter_role} fields")
        return fields
    
    # For Seller submitter or no filter, return form-based fields
    field_map = mapping.get('field_mappings') or {}
    transforms = mapping.get('transforms') or {}
    
    # Map form fields to DocuSeal fields
    for form_field, docuseal_field in field_map.items():
        if not docuseal_field:
            continue  # Skip fields mapped to null
            
        if form_field in form_data and form_data[form_field]:
            value = form_data[form_field]
            
            # Apply transformation if needed
            transformed_value = _apply_transform(value, form_field, transforms)
            
            if transformed_value:  # Only add non-empty values
                fields.append({
                    'name': docuseal_field,
                    'default_value': transformed_value
                })
    
    # Handle computed fields (fields that combine multiple form values)
    computed_fields = mapping.get('computed_fields', [])
    for computed in computed_fields:
        docuseal_name = computed.get('docuseal_field')
        template = computed.get('template', '')
        
        if docuseal_name and template:
            try:
                # Simple template substitution using format
                computed_value = template.format(**form_data)
                if computed_value and computed_value != template:  # Only add if substitution worked
                    fields.append({
                        'name': docuseal_name,
                        'default_value': computed_value
                    })
            except KeyError:
                pass  # Missing form field, skip this computed field
    
    # Handle option_transforms (form values that map to DocuSeal checkbox fields with "X")
    # Used for radio groups where a single form value determines which DocuSeal field gets "X"
    option_transforms = mapping.get('option_transforms', {})
    for form_field, value_mapping in option_transforms.items():
        form_value = form_data.get(form_field)
        if form_value and isinstance(value_mapping, dict):
            # Get the DocuSeal field name that should receive "X" based on form value
            docuseal_field = value_mapping.get(form_value) or value_mapping.get(str(form_value).lower())
            if docuseal_field:
                fields.append({
                    'name': docuseal_field,
                    'default_value': 'X'
                })
    
    # Handle conditional_fields (fields only included when a condition is met)
    # Format: { condition_field: { condition_value: { form_field: docuseal_field } } }
    conditional_fields = mapping.get('conditional_fields', {})
    for condition_field, value_conditions in conditional_fields.items():
        condition_value = form_data.get(condition_field)
        if condition_value and isinstance(value_conditions, dict):
            # Get the field mappings for this condition value
            conditional_mappings = value_conditions.get(condition_value) or value_conditions.get(str(condition_value).lower())
            if conditional_mappings and isinstance(conditional_mappings, dict):
                for form_field, docuseal_field in conditional_mappings.items():
                    if form_field in form_data and form_data[form_field]:
                        value = form_data[form_field]
                        transformed_value = _apply_transform(value, form_field, transforms)
                        if transformed_value:
                            fields.append({
                                'name': docuseal_field,
                                'default_value': transformed_value
                            })
    
    # Add agent/broker fields if provided (only when not filtering for Seller)
    if submitter_role != 'Seller':
        agent_field_configs = mapping.get('agent_fields', [])
        if agent_data and agent_field_configs:
            for config in agent_field_configs:
                docuseal_name = config.get('docuseal_field')
                source = config.get('source', '')
                
                if docuseal_name and source:
                    # Parse source like "agent.name" or "agent.license_number"
                    parts = source.split('.')
                    if len(parts) == 2 and parts[0] == 'agent':
                        value = agent_data.get(parts[1], '')
                        if value:
                            fields.append({
                                'name': docuseal_name,
                                'default_value': str(value)
                            })
        
        # Fallback: Add standard agent fields if no config specified
        elif agent_data and not agent_field_configs:
            standard_agent_fields = [
                ('Broker printed name', agent_data.get('name', '')),
                ('Broker associate printed name', agent_data.get('name', '')),
                ('Broker license number', agent_data.get('license_number', '')),
                ('Broker associate license', agent_data.get('license_number', '')),
                ('Broker email/fax', agent_data.get('email', '')),
            ]
            for docuseal_name, value in standard_agent_fields:
                if value:
                    fields.append({
                        'name': docuseal_name,
                        'default_value': str(value)
                    })
    
    role_label = f" for {submitter_role}" if submitter_role else ""
    logger.info(f"Built {len(fields)} DocuSeal fields{role_label} from form data for {template_slug}")
    return fields


# =============================================================================
# PREVIEW-ONLY DOCUMENT FIELD BUILDERS
# =============================================================================
# These functions build fields for preview-only documents that auto-populate
# from user profile data rather than form input.

def build_iabs_fields(user) -> List[Dict[str, Any]]:
    """
    Build DocuSeal fields for the IABS document from user profile.
    
    The IABS (Information About Brokerage Services) document auto-populates
    agent and supervisor information from the logged-in user's profile.
    
    Args:
        user: The User model object (current_user)
        
    Returns:
        List of dicts with 'name' and 'default_value' for DocuSeal API
    """
    fields = []
    
    # Load the YAML mapping to get DocuSeal field names
    mapping = load_field_mapping('iabs')
    agent_fields_config = mapping.get('agent_fields', {}) if mapping else {}
    
    # Build agent name
    agent_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    
    # Map our internal field names to values from user profile
    field_values = {
        'supervisor_name': getattr(user, 'licensed_supervisor', '') or '',
        'supervisor_license': getattr(user, 'licensed_supervisor_license', '') or '',
        'supervisor_email': getattr(user, 'licensed_supervisor_email', '') or '',
        'supervisor_phone': getattr(user, 'licensed_supervisor_phone', '') or '',
        'agent_name': agent_name,
        'agent_license': getattr(user, 'license_number', '') or '',
        'agent_email': getattr(user, 'email', '') or '',
        'agent_phone': getattr(user, 'phone', '') or '',
    }
    
    # Build DocuSeal fields from the mapping
    for internal_name, docuseal_name in agent_fields_config.items():
        value = field_values.get(internal_name, '')
        if docuseal_name and value:
            fields.append({
                'name': docuseal_name,
                'default_value': str(value)
            })
    
    logger.info(f"Built {len(fields)} DocuSeal fields for IABS from user profile")
    return fields


def build_iabs_field_data(user) -> Dict[str, Any]:
    """
    Build field_data dict for IABS document storage.
    
    This is stored in TransactionDocument.field_data for reference
    and can be used for display purposes.
    
    Args:
        user: The User model object (current_user)
        
    Returns:
        Dict of field names to values
    """
    agent_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    
    return {
        'supervisor_name': getattr(user, 'licensed_supervisor', '') or '',
        'supervisor_license': getattr(user, 'licensed_supervisor_license', '') or '',
        'supervisor_email': getattr(user, 'licensed_supervisor_email', '') or '',
        'supervisor_phone': getattr(user, 'licensed_supervisor_phone', '') or '',
        'agent_name': agent_name,
        'agent_license': getattr(user, 'license_number', '') or '',
        'agent_email': getattr(user, 'email', '') or '',
        'agent_phone': getattr(user, 'phone', '') or '',
    }


# Mock storage for development (in-memory)
_mock_submissions = {}
_mock_events = []


class DocuSealError(Exception):
    """Custom exception for DocuSeal API errors."""
    pass


def _get_headers() -> Dict[str, str]:
    """Get headers for DocuSeal API requests."""
    return {
        'X-Auth-Token': DOCUSEAL_API_KEY,
        'Content-Type': 'application/json'
    }


def get_template_id(slug: str) -> Optional[int]:
    """
    Get DocuSeal template ID for a document slug.
    
    First checks TEMPLATE_MAP for hardcoded IDs, then falls back to
    checking the YAML mapping file for dynamically configured templates.
    """
    # First check the static TEMPLATE_MAP
    template_id = TEMPLATE_MAP.get(slug)
    if template_id:
        return template_id
    
    # Fall back to checking the YAML mapping file
    mapping = load_field_mapping(slug)
    if mapping:
        yaml_template_id = mapping.get('template_id')
        if yaml_template_id:
            return yaml_template_id
    
    return None


def is_template_ready(slug: str) -> bool:
    """Check if a template has been uploaded to DocuSeal."""
    return get_template_id(slug) is not None


# =============================================================================
# TEMPLATE MANAGEMENT
# =============================================================================

def get_template(template_id: int) -> Dict[str, Any]:
    """
    Fetch template details from DocuSeal including all fields.
    
    Returns template info with:
    - fields: List of field definitions (name, type, required, submitter_uuid)
    - submitters: List of submitter roles (name, uuid)
    - documents: List of attached documents
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_get_template(template_id)
    
    try:
        response = requests.get(
            f"{DOCUSEAL_API_URL}/templates/{template_id}",
            headers=_get_headers()
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal get_template error: {e}")
        raise DocuSealError(f"Failed to fetch template: {e}")


def get_template_fields(template_id: int) -> List[Dict[str, Any]]:
    """
    Get just the fields from a template.
    
    Returns list of field dicts with:
    - name: Field name (used for pre-filling)
    - type: Field type (text, signature, date, checkbox, etc.)
    - required: Whether field is required
    - submitter_uuid: Which signer this field belongs to
    """
    template = get_template(template_id)
    return template.get('fields', [])


def get_template_submitters(template_id: int) -> List[Dict[str, Any]]:
    """
    Get the submitter roles defined in a template.
    
    Returns list of submitter dicts with:
    - name: Role name (e.g., "Seller", "Listing Agent")
    - uuid: Unique identifier for this role
    """
    template = get_template(template_id)
    return template.get('submitters', [])


# =============================================================================
# SUBMISSION MANAGEMENT
# =============================================================================

def create_submission(
    template_slug: str,
    submitters: List[Dict[str, Any]],
    field_values: Dict[str, Any] = None,
    send_email: bool = True,
    message: Dict[str, str] = None,
    template_id: int = None
) -> Dict[str, Any]:
    """
    Create a new submission (send document for signature).
    
    Args:
        template_slug: Our internal template identifier (can be None if template_id provided)
        submitters: List of signers with role, email, name, and optional fields[]
        field_values: Pre-filled field values (applied to all submitters if not in submitter.fields)
        send_email: Whether to send email invitations
        message: Custom email subject/body
        template_id: Optional direct template ID (use for merged templates)
    
    Returns:
        Submission data with ID and submitter details
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_create_submission(template_slug or 'merged', submitters, field_values, send_email)
    
    # Get template ID - use provided ID or look up by slug
    if template_id is None:
        template_id = get_template_id(template_slug)
        if not template_id:
            raise DocuSealError(f"Template not configured for slug: {template_slug}")
    
    # Build the request payload
    payload = {
        "template_id": template_id,
        "send_email": send_email,
        "submitters": submitters
    }
    
    # Add custom message if provided
    if message:
        payload["message"] = message
    
    logger.info(f"Creating DocuSeal submission for template {template_id} ({template_slug})")
    logger.info(f"Submitters: {json.dumps(submitters, indent=2, default=str)}")
    logger.info(f"Full payload: {json.dumps(payload, indent=2, default=str)}")
    
    # DEBUG: Print to console for immediate visibility
    print(f"\n{'='*60}")
    print(f"DOCUSEAL SUBMISSION DEBUG")
    print(f"{'='*60}")
    print(f"Template ID: {template_id}, Slug: {template_slug}")
    print(f"Number of submitters: {len(submitters)}")
    for i, sub in enumerate(submitters):
        print(f"  Submitter {i+1}: role={sub.get('role')}, email={sub.get('email')}")
        print(f"    Fields: {len(sub.get('fields', []))} fields")
        for f in sub.get('fields', [])[:5]:  # Show first 5 fields
            print(f"      - {f.get('name')}: {f.get('default_value', '')[:30]}...")
    print(f"{'='*60}\n")
    
    try:
        response = requests.post(
            f"{DOCUSEAL_API_URL}/submissions",
            headers=_get_headers(),
            json=payload
        )
        response.raise_for_status()
        result = response.json()
        
        # DocuSeal returns a list of submitters, each with their own id and slug
        # We need to normalize this to a dict with 'id' and 'submitters' keys
        if isinstance(result, list):
            # Get submission_id from first submitter (all share the same submission)
            submission_id = result[0].get('submission_id') if result else None
            normalized_result = {
                'id': submission_id,
                'submitters': result
            }
            logger.info(f"DocuSeal submission created: {submission_id} with {len(result)} submitters")
            return normalized_result
        
        logger.info(f"DocuSeal submission created: {result.get('id')}")
        return result
        
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal create_submission error: {e}")
        # Print to console for immediate visibility
        print(f"\n{'='*60}")
        print(f"DOCUSEAL API ERROR")
        print(f"{'='*60}")
        print(f"Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Status Code: {e.response.status_code}")
            print(f"Response Body: {e.response.text}")
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
            logger.error(f"Request payload was: {json.dumps(payload, indent=2, default=str)}")
        print(f"{'='*60}\n")
        raise DocuSealError(f"Failed to create submission: {e}")


def get_submission(submission_id: str) -> Dict[str, Any]:
    """Get submission details by ID."""
    if DOCUSEAL_MOCK_MODE:
        return _mock_get_submission(submission_id)
    
    try:
        response = requests.get(
            f"{DOCUSEAL_API_URL}/submissions/{submission_id}",
            headers=_get_headers()
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal get_submission error: {e}")
        raise DocuSealError(f"Failed to get submission: {e}")


def get_submission_status(submission_id: str) -> str:
    """
    Get the current status of a submission.
    
    Returns: 'pending', 'viewed', 'started', 'completed', 'expired'
    """
    submission = get_submission(submission_id)
    return submission.get('status', 'pending')


def resend_signature_emails(submission_id: str, submitter_ids: List[int] = None, message: Dict[str, str] = None) -> Dict[str, Any]:
    """
    Resend signature request emails to submitters who haven't completed signing.
    
    Uses the DocuSeal Update Submitter API with send_email=true.
    
    Args:
        submission_id: The DocuSeal submission ID
        submitter_ids: Optional list of specific submitter IDs to resend to.
                      If None, resends to all pending submitters.
        message: Optional custom message with 'subject' and 'body' keys.
                Body can include {{submitter.link}} for the signing link.
    
    Returns:
        Dict with 'success', 'resent_count', and 'submitters' list
    """
    if DOCUSEAL_MOCK_MODE:
        return {
            'success': True,
            'resent_count': 1,
            'submitters': [{'id': 1, 'email': 'mock@example.com', 'status': 'sent'}],
            'message': 'Emails resent (mock mode)'
        }
    
    try:
        # Get current submission to find pending submitters
        submission = get_submission(submission_id)
        submitters = submission.get('submitters', [])
        
        resent = []
        for submitter in submitters:
            # Skip if submitter already completed
            if submitter.get('status') == 'completed':
                continue
            
            # Skip if we're targeting specific submitter IDs and this isn't one
            if submitter_ids and submitter.get('id') not in submitter_ids:
                continue
            
            submitter_id = submitter.get('id')
            if not submitter_id:
                continue
            
            # Build update payload
            update_payload = {
                'send_email': True
            }
            
            # Add custom message if provided
            if message:
                update_payload['message'] = {
                    'subject': message.get('subject', 'Reminder: Document Ready for Signature'),
                    'body': message.get('body', 'Please sign the document at your earliest convenience. Click here to sign: {{submitter.link}}')
                }
            
            # Call the Update Submitter API
            headers = {
                'X-Auth-Token': DOCUSEAL_API_KEY,
                'Content-Type': 'application/json'
            }
            
            response = requests.put(
                f"{DOCUSEAL_API_URL}/submitters/{submitter_id}",
                headers=headers,
                json=update_payload
            )
            response.raise_for_status()
            
            result = response.json()
            resent.append({
                'id': result.get('id'),
                'email': result.get('email'),
                'name': result.get('name'),
                'status': result.get('status'),
                'sent_at': result.get('sent_at')
            })
            
            logger.info(f"Resent signature email to submitter {submitter_id}: {result.get('email')}")
        
        return {
            'success': True,
            'resent_count': len(resent),
            'submitters': resent,
            'message': f'Successfully resent emails to {len(resent)} submitter(s)'
        }
        
    except requests.RequestException as e:
        logger.error(f"DocuSeal resend_signature_emails error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        raise DocuSealError(f"Failed to resend signature emails: {e}")


def get_signing_url(submission_id: str, submitter_slug: str) -> str:
    """Get the signing URL for a specific submitter."""
    if DOCUSEAL_MOCK_MODE:
        return f"https://docuseal.com/sign/mock/{submitter_slug}"
    
    # Real implementation would return the actual signing URL
    submission = get_submission(submission_id)
    for submitter in submission.get('submitters', []):
        if submitter.get('slug') == submitter_slug:
            return submitter.get('embed_src', '')
    
    return ''


def get_signed_document_urls(submission_id: str) -> List[Dict[str, str]]:
    """
    Get URLs to download signed documents.
    
    Returns:
        List of dicts with 'name' and 'url' for each document
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_get_signed_documents(submission_id)
    
    try:
        submission = get_submission(submission_id)
        documents = []
        
        # Extract document URLs from submission
        for doc in submission.get('documents', []):
            documents.append({
                'name': doc.get('filename', 'document.pdf'),
                'url': doc.get('url', '')
            })
        
        return documents
    except Exception as e:
        logger.error(f"DocuSeal get_signed_document_urls error: {e}")
        raise DocuSealError(f"Failed to get signed documents: {e}")


# =============================================================================
# TEMPLATE LISTING
# =============================================================================

def list_templates(limit: int = 100) -> List[Dict[str, Any]]:
    """List all templates in DocuSeal account."""
    if DOCUSEAL_MOCK_MODE:
        return _mock_list_templates()
    
    try:
        response = requests.get(
            f"{DOCUSEAL_API_URL}/templates",
            headers=_get_headers(),
            params={'limit': limit}
        )
        response.raise_for_status()
        result = response.json()
        return result.get('data', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal list_templates error: {e}")
        raise DocuSealError(f"Failed to list templates: {e}")


def upload_template(file_path: str, name: str) -> Dict[str, Any]:
    """
    Upload a PDF as a new template.
    
    Note: After upload, you'll need to use DocuSeal's form builder
    to add signature fields, then update TEMPLATE_MAP with the ID.
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_upload_template(name)
    
    # Real implementation would use multipart form upload
    raise DocuSealError("Template upload should be done via DocuSeal web interface")


# =============================================================================
# MULTI-DOCUMENT SUBMISSION
# =============================================================================
# PREVIEW SUBMISSION
# =============================================================================
# Creates a preview-only submission to show filled documents without sending.

def get_template_field_role_mapping(template_id: int) -> Dict[str, Any]:
    """
    Fetch a template and build a mapping of field names to their submitter roles.
    
    This is essential for correctly assigning pre-filled values to the right submitter
    when creating submissions.
    
    Returns:
        Dict with:
        - 'field_to_role': Dict mapping field_name -> role_name
        - 'roles': List of role names in the template
        - 'submitters': Original submitter data from template
        - 'all_field_names': List of all field names in template (for debugging)
    """
    try:
        template = get_template(template_id)
        submitters = template.get('submitters', [])
        fields = template.get('fields', [])
        
        # Build uuid -> role name mapping
        uuid_to_role = {s['uuid']: s['name'] for s in submitters}
        
        # Build field name -> role mapping
        field_to_role = {}
        all_field_names = []
        for field in fields:
            field_name = field.get('name')
            submitter_uuid = field.get('submitter_uuid')
            if field_name:
                all_field_names.append(field_name)
                if submitter_uuid:
                    role = uuid_to_role.get(submitter_uuid, 'Unknown')
                    field_to_role[field_name] = role
        
        roles = [s['name'] for s in submitters]
        
        logger.info(f"Template {template_id} has {len(roles)} roles: {roles}")
        logger.info(f"Template {template_id} has {len(all_field_names)} fields: {all_field_names}")
        
        return {
            'field_to_role': field_to_role,
            'roles': roles,
            'submitters': submitters,
            'all_field_names': all_field_names
        }
    except Exception as e:
        logger.error(f"Error getting template field role mapping: {e}")
        return {'field_to_role': {}, 'roles': [], 'submitters': [], 'all_field_names': []}


def create_preview_submission(
    template_id: int,
    field_data: Dict[str, Any],
    template_slug: str,
    prebuilt_fields: List[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Create a preview submission for showing filled documents.
    
    This function intelligently determines which submitter role each field belongs to
    by querying the template structure from DocuSeal.
    
    In real mode: Creates a draft submission in DocuSeal that can be previewed
    In mock mode: Returns None (template shows field summary instead)
    
    Args:
        template_id: The DocuSeal template ID
        field_data: Form data to populate the document
        template_slug: Our internal template identifier
        prebuilt_fields: Optional pre-built DocuSeal fields (for preview-only docs like IABS)
        
    Returns:
        Dict with 'slug' for embedding, or None if preview not available
    """
    if DOCUSEAL_MOCK_MODE:
        return None
    
    try:
        # Use prebuilt fields if provided, otherwise build from field_data
        if prebuilt_fields is not None:
            docuseal_fields = prebuilt_fields
        else:
            # Build the fields for DocuSeal format
            docuseal_fields = build_docuseal_fields(
                form_data=field_data,
                template_slug=template_slug
            )
        
        # Get template field-to-role mapping to correctly assign fields
        role_mapping = get_template_field_role_mapping(template_id)
        field_to_role = role_mapping.get('field_to_role', {})
        available_roles = role_mapping.get('roles', [])
        
        if not available_roles:
            logger.error(f"No roles found for template {template_id}")
            return None
        
        # Group fields by their owning role
        fields_by_role = {}
        unassigned_fields = []
        
        for field in docuseal_fields:
            field_name = field.get('name', '')
            role = field_to_role.get(field_name)
            
            if role:
                if role not in fields_by_role:
                    fields_by_role[role] = []
                fields_by_role[role].append(field)
            else:
                # Field not found in template - might be case mismatch or typo
                unassigned_fields.append(field_name)
        
        if unassigned_fields:
            logger.warning(f"Fields not found in template {template_id}: {unassigned_fields}")
            logger.warning(f"Available fields: {list(field_to_role.keys())}")
        
        if not fields_by_role:
            # Fallback: try the first available role with all fields
            logger.warning(f"No fields matched template roles. Using first role: {available_roles[0]}")
            fields_by_role[available_roles[0]] = docuseal_fields
        
        # Build submitters list with correct role assignments
        submitters = []
        for idx, (role, fields) in enumerate(fields_by_role.items()):
            submitters.append({
                'email': f'preview{idx}@preview.local',
                'role': role,
                'fields': fields
            })
        
        logger.info(f"Creating preview with {len(submitters)} submitter(s): {list(fields_by_role.keys())}")
        
        headers = {
            'X-Auth-Token': DOCUSEAL_API_KEY,
            'Content-Type': 'application/json'
        }
        
        payload = {
            'template_id': template_id,
            'send_email': False,  # Don't send any emails
            'submitters': submitters
        }
        
        response = requests.post(
            f'{DOCUSEAL_API_URL}/submissions',
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.ok:
            result = response.json()
            # Get the submitter slug for embedding - return the first one
            if isinstance(result, list) and len(result) > 0:
                return {'slug': result[0].get('slug')}
            return None
        else:
            logger.error(f"Failed to create preview submission: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Error creating preview submission: {e}")
        return None


# =============================================================================
# MULTI-DOCUMENT SUBMISSIONS
# =============================================================================
# These functions support sending multiple documents as a single envelope.
# DocuSeal allows templates to contain multiple PDFs, which signers complete
# in one signing session.

def create_multi_doc_submission(
    documents: List[Dict[str, Any]],
    submitters: List[Dict[str, Any]],
    transaction_id: int,
    send_email: bool = True,
    message: Dict[str, str] = None
) -> Dict[str, Any]:
    """
    Create a submission with multiple documents as one envelope.
    
    This creates a combined template from multiple existing templates,
    then creates a submission from that combined template.
    
    Args:
        documents: List of dicts with:
            - template_slug: Our internal template identifier (e.g., 'listing-agreement')
            - field_data: Pre-filled values for this document
            - template_name: Display name of the document
        submitters: List of signers with role, email, name
        transaction_id: Transaction ID for unique template naming
        send_email: Whether to send email invitations
        message: Custom email subject/body
    
    Returns:
        Dict with:
            - id: Submission ID
            - combined_template_id: The ID of the dynamically created combined template
            - submitters: List of submitters with their signing URLs
            - document_count: Number of documents in the envelope
    """
    if DOCUSEAL_MOCK_MODE:
        return _mock_create_multi_doc_submission(documents, submitters, transaction_id, send_email)
    
    try:
        # Step 1: Fetch each template to get document URLs and fields
        template_docs = []
        all_fields = []
        
        for doc in documents:
            template_slug = doc.get('template_slug')
            template_id = get_template_id(template_slug)
            
            if not template_id:
                logger.warning(f"Template not configured for slug: {template_slug}, skipping")
                continue
            
            # Fetch template details
            template = get_template(template_id)
            
            # Get the PDF document(s) from this template
            for template_doc in template.get('documents', []):
                template_docs.append({
                    'name': doc.get('template_name', template_slug),
                    'file': template_doc.get('url'),  # DocuSeal accepts URL directly
                })
            
            # Collect fields with their values
            field_data = doc.get('field_data', {})
            for field in template.get('fields', []):
                field_with_value = field.copy()
                # Add default value if we have it in field_data
                field_name = field.get('name')
                if field_name and field_name in field_data:
                    field_with_value['default_value'] = str(field_data[field_name])
                all_fields.append(field_with_value)
        
        if not template_docs:
            raise DocuSealError("No valid templates found for the provided documents")
        
        # Step 2: Create a combined template with all documents
        combined_template = _create_combined_template(
            name=f"Combined Package - Transaction {transaction_id}",
            documents=template_docs,
            external_id=f"tx-{transaction_id}-combined-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        )
        
        combined_template_id = combined_template.get('id')
        logger.info(f"Created combined template {combined_template_id} with {len(template_docs)} documents")
        
        # Step 3: Build submitters with pre-filled fields
        submitters_with_fields = []
        for sub in submitters:
            submitter = {
                'role': sub.get('role'),
                'email': sub.get('email'),
                'name': sub.get('name', ''),
            }
            
            # Add pre-filled fields for this submitter's role
            fields_for_role = []
            for doc in documents:
                template_slug = doc.get('template_slug')
                field_data = doc.get('field_data', {})
                agent_data = doc.get('agent_data')
                
                # Get fields for this submitter's role
                role_fields = build_docuseal_fields(
                    field_data,
                    template_slug,
                    agent_data,
                    submitter_role=sub.get('role')
                )
                fields_for_role.extend(role_fields)
            
            if fields_for_role:
                submitter['fields'] = fields_for_role
            
            submitters_with_fields.append(submitter)
        
        # Step 4: Create submission from combined template
        payload = {
            "template_id": combined_template_id,
            "send_email": send_email,
            "submitters": submitters_with_fields
        }
        
        if message:
            payload["message"] = message
        
        response = requests.post(
            f"{DOCUSEAL_API_URL}/submissions",
            headers=_get_headers(),
            json=payload
        )
        response.raise_for_status()
        result = response.json()
        
        # Normalize result
        if isinstance(result, list):
            submission_id = result[0].get('submission_id') if result else None
            normalized_result = {
                'id': submission_id,
                'combined_template_id': combined_template_id,
                'submitters': result,
                'document_count': len(template_docs)
            }
        else:
            normalized_result = result
            normalized_result['combined_template_id'] = combined_template_id
            normalized_result['document_count'] = len(template_docs)
        
        logger.info(f"Created multi-doc submission {normalized_result.get('id')} with {len(template_docs)} documents")
        return normalized_result
        
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal create_multi_doc_submission error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        raise DocuSealError(f"Failed to create multi-document submission: {e}")


def merge_templates(
    template_ids: List[int],
    name: str = None,
    roles: List[str] = None,
    external_id: str = None
) -> Dict[str, Any]:
    """
    Merge multiple existing templates into a single combined template.
    
    Uses DocuSeal's "Merge templates" API which combines templates
    with all their documents and fields into one.
    
    Args:
        template_ids: List of template IDs to merge
        name: Optional name for merged template (defaults to "Merged Template")
        roles: Optional list of unified role names for the merged template
               (e.g., ["Seller", "Broker"] to unify different role names)
        external_id: Optional external identifier for caching/deduplication
    
    Returns:
        Created merged template object with id, slug, fields, submitters, etc.
    """
    if DOCUSEAL_MOCK_MODE:
        return {
            'id': 999999,
            'name': name or 'Merged Template',
            'slug': 'mock-merged',
            'submitters': [{'name': r, 'uuid': f'mock-{r}'} for r in (roles or ['Seller', 'Broker'])]
        }
    
    payload = {
        "template_ids": template_ids,
        "folder_name": "CRM Combined Packages"
    }
    
    if name:
        payload["name"] = name
    
    if roles:
        payload["roles"] = roles
    
    if external_id:
        payload["external_id"] = external_id
    
    logger.info(f"Merging templates {template_ids} with roles {roles}")
    
    try:
        response = requests.post(
            f"{DOCUSEAL_API_URL}/templates/merge",
            headers=_get_headers(),
            json=payload
        )
        response.raise_for_status()
        result = response.json()
        logger.info(f"Merged template created: ID {result.get('id')}, submitters: {[s.get('name') for s in result.get('submitters', [])]}")
        return result
        
    except requests.exceptions.RequestException as e:
        logger.error(f"DocuSeal merge_templates error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        raise DocuSealError(f"Failed to merge templates: {e}")


def _mock_create_multi_doc_submission(
    documents: List[Dict[str, Any]],
    submitters: List[Dict[str, Any]],
    transaction_id: int,
    send_email: bool = True
) -> Dict[str, Any]:
    """Mock implementation of create_multi_doc_submission."""
    submission_id = f"multi-{str(uuid.uuid4())[:8]}"
    
    # Create mock submitters
    mock_submitters = []
    for i, sub in enumerate(submitters):
        submitter_slug = str(uuid.uuid4())[:12]
        mock_submitters.append({
            'id': 2000 + i,
            'slug': submitter_slug,
            'email': sub.get('email'),
            'name': sub.get('name', ''),
            'role': sub.get('role', 'Signer'),
            'status': 'pending',
            'sent_at': datetime.utcnow().isoformat() if send_email else None,
            'embed_src': f"https://docuseal.com/s/{submitter_slug}",
            'sign_url': f"https://docuseal.com/sign/{submitter_slug}"
        })
    
    # Build list of document names
    doc_names = [d.get('template_name', d.get('template_slug', 'Document')) for d in documents]
    
    submission = {
        'id': submission_id,
        'combined_template_id': 999999,
        'transaction_id': transaction_id,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat(),
        'expire_at': (datetime.utcnow() + timedelta(days=30)).isoformat(),
        'submitters': mock_submitters,
        'document_count': len(documents),
        'document_names': doc_names,
        'documents': []
    }
    
    _mock_submissions[submission_id] = submission
    logger.info(f"[MOCK] Created multi-doc submission {submission_id} with {len(documents)} documents")
    return submission


# =============================================================================
# WEBHOOK HANDLING
# =============================================================================

def process_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process incoming webhook from DocuSeal.
    
    Event types:
    - form.viewed: Signer opened the form
    - form.started: Signer began filling
    - form.completed: All signers finished
    """
    event_type = payload.get('event_type')
    data = payload.get('data', {})
    
    result = {
        'event_type': event_type,
        'submission_id': data.get('submission_id'),
        'processed_at': datetime.utcnow().isoformat()
    }
    
    if event_type == 'form.completed':
        # Extract signed document URLs
        result['documents'] = data.get('documents', [])
        result['submitters'] = data.get('submitters', [])
    
    return result


# =============================================================================
# MOCK IMPLEMENTATIONS
# =============================================================================

def _mock_create_submission(
    template_slug: str,
    submitters: List[Dict[str, Any]],
    field_values: Dict[str, Any] = None,
    send_email: bool = True
) -> Dict[str, Any]:
    """Mock implementation of create_submission."""
    submission_id = str(uuid.uuid4())[:8]
    
    # Create mock submitters with slugs
    mock_submitters = []
    for i, sub in enumerate(submitters):
        submitter_slug = str(uuid.uuid4())[:12]
        mock_submitters.append({
            'id': 1000 + i,
            'slug': submitter_slug,
            'email': sub.get('email'),
            'name': sub.get('name', ''),
            'role': sub.get('role', 'Signer'),
            'status': 'pending',
            'sent_at': datetime.utcnow().isoformat() if send_email else None,
            'embed_src': f"https://docuseal.com/s/{submitter_slug}",
            'sign_url': f"https://docuseal.com/sign/{submitter_slug}"
        })
    
    submission = {
        'id': submission_id,
        'template_slug': template_slug,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat(),
        'expire_at': (datetime.utcnow() + timedelta(days=30)).isoformat(),
        'submitters': mock_submitters,
        'field_values': field_values or {},
        'documents': []
    }
    
    _mock_submissions[submission_id] = submission
    return submission


def _mock_get_submission(submission_id: str) -> Dict[str, Any]:
    """Mock implementation of get_submission."""
    if submission_id in _mock_submissions:
        return _mock_submissions[submission_id]
    
    # Return a default mock for any ID
    return {
        'id': submission_id,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat(),
        'submitters': []
    }


def _mock_get_signed_documents(submission_id: str) -> List[Dict[str, str]]:
    """Mock implementation of get_signed_document_urls."""
    submission = _mock_submissions.get(submission_id, {})
    
    # If status is completed, return mock document URLs
    if submission.get('status') == 'completed':
        return submission.get('documents', [])
    
    return []


def _mock_list_templates() -> List[Dict[str, Any]]:
    """Mock implementation of list_templates."""
    return [
        {'id': 1001, 'name': 'Listing Agreement', 'slug': 'listing-agreement'},
        {'id': 1002, 'name': 'IABS', 'slug': 'iabs'},
        {'id': 1003, 'name': "Seller's Disclosure", 'slug': 'sellers-disclosure'},
    ]


def _mock_get_template(template_id: int) -> Dict[str, Any]:
    """Mock implementation of get_template."""
    return {
        'id': template_id,
        'name': 'Listing Agreement',
        'fields': [
            {'uuid': 'f1', 'name': 'Seller Name', 'type': 'text', 'required': True},
            {'uuid': 'f2', 'name': 'Property Address', 'type': 'text', 'required': True},
            {'uuid': 'f3', 'name': 'Listing Price', 'type': 'text', 'required': True},
            {'uuid': 'f4', 'name': 'Seller Signature', 'type': 'signature', 'required': True},
        ],
        'submitters': [
            {'name': 'Seller', 'uuid': 's1'},
            {'name': 'Listing Agent', 'uuid': 's2'},
        ],
        'documents': []
    }


def _mock_upload_template(name: str) -> Dict[str, Any]:
    """Mock implementation of upload_template."""
    return {
        'id': 9999,
        'name': name,
        'created_at': datetime.utcnow().isoformat(),
        'status': 'draft'
    }


def _mock_simulate_signing(submission_id: str, event: str = 'completed') -> None:
    """
    Simulate signing events for testing.
    
    Usage in testing:
        from services.docuseal_service import _mock_simulate_signing
        _mock_simulate_signing('abc123', 'completed')
    """
    if submission_id not in _mock_submissions:
        return
    
    submission = _mock_submissions[submission_id]
    
    if event == 'viewed':
        for sub in submission['submitters']:
            sub['status'] = 'viewed'
            sub['viewed_at'] = datetime.utcnow().isoformat()
    
    elif event == 'started':
        for sub in submission['submitters']:
            sub['status'] = 'started'
    
    elif event == 'completed':
        submission['status'] = 'completed'
        submission['completed_at'] = datetime.utcnow().isoformat()
        for sub in submission['submitters']:
            sub['status'] = 'completed'
            sub['signed_at'] = datetime.utcnow().isoformat()
        
        # Add mock signed document URLs
        submission['documents'] = [
            {
                'name': f"{submission['template_slug']}_signed.pdf",
                'url': f"https://docuseal.com/downloads/mock/{submission_id}.pdf"
            }
        ]
    
    _mock_events.append({
        'event_type': f'form.{event}',
        'submission_id': submission_id,
        'timestamp': datetime.utcnow().isoformat()
    })


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def build_submitters_from_participants(participants, transaction) -> List[Dict[str, Any]]:
    """
    Build DocuSeal submitters list from transaction participants.
    
    Maps our roles to DocuSeal signer roles:
    - Primary seller -> "Seller"
    - Additional sellers -> "Seller 2", "Seller 3", etc.
    - Listing agent -> "Broker"
    
    If a role is omitted, DocuSeal will skip fields assigned to that role.
    """
    submitters = []
    
    # Collect all sellers, primary first
    sellers = []
    primary_seller = None
    for p in participants:
        if p.role == 'seller':
            if p.is_primary:
                primary_seller = p
            else:
                sellers.append(p)
    
    # Add primary seller first as "Seller"
    if primary_seller:
        submitters.append({
            'role': 'Seller',
            'email': primary_seller.display_email,
            'name': primary_seller.display_name
        })
    
    # Add additional sellers as "Seller 2", "Seller 3", etc.
    for i, seller in enumerate(sellers, start=2):
        if seller.display_email:  # Only add if they have an email
            submitters.append({
                'role': f'Seller {i}',
                'email': seller.display_email,
                'name': seller.display_name
            })
    
    # Get listing agent - maps to "Broker" in DocuSeal
    for p in participants:
        if p.role == 'listing_agent':
            submitters.append({
                'role': 'Broker',  # DocuSeal uses "Broker" not "Listing Agent"
                'email': p.display_email,
                'name': p.display_name
            })
            break
    
    return submitters


def format_status_badge(status: str) -> Dict[str, str]:
    """Get badge class and label for a status."""
    status_map = {
        'pending': {'class': 'badge-ghost', 'label': 'Pending'},
        'sent': {'class': 'badge-info', 'label': 'Sent'},
        'viewed': {'class': 'badge-warning', 'label': 'Viewed'},
        'started': {'class': 'badge-warning', 'label': 'In Progress'},
        'completed': {'class': 'badge-success', 'label': 'Signed'},
        'expired': {'class': 'badge-error', 'label': 'Expired'},
        'filled': {'class': 'badge-secondary', 'label': 'Filled'},
        'generated': {'class': 'badge-primary', 'label': 'Generated'},
    }
    return status_map.get(status, {'class': 'badge-ghost', 'label': status.title()})

