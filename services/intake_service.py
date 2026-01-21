# services/intake_service.py
"""
Intake schema loading and document package generation service.
"""

import json
import os
from pathlib import Path

# Path to intake schemas
SCHEMAS_DIR = Path(__file__).parent.parent / 'intake_schemas'


def get_intake_schema(transaction_type: str, ownership_status: str = None) -> dict:
    """
    Load the intake schema for a given transaction type and ownership status.
    
    Args:
        transaction_type: e.g., 'seller', 'buyer'
        ownership_status: e.g., 'conventional', 'builder' (optional)
    
    Returns:
        The schema dict or None if not found
    """
    # Build the schema filename
    if ownership_status:
        filename = f"{transaction_type}_{ownership_status}.json"
    else:
        filename = f"{transaction_type}.json"
    
    schema_path = SCHEMAS_DIR / filename
    
    if not schema_path.exists():
        return None
    
    with open(schema_path, 'r') as f:
        return json.load(f)


def evaluate_document_rules(schema: dict, intake_data: dict) -> list:
    """
    Evaluate document rules against intake answers to determine required documents.
    
    Args:
        schema: The intake schema with document_rules
        intake_data: The user's answers
    
    Returns:
        List of document dicts with slug, name, reason
    """
    required_docs = []
    
    for rule in schema.get('document_rules', []):
        include = False
        reason = rule.get('reason', '')
        
        if rule.get('always'):
            include = True
        elif 'condition' in rule:
            condition = rule['condition']
            field_value = intake_data.get(condition['field'])
            
            if 'equals' in condition:
                include = field_value == condition['equals']
            elif 'in' in condition:
                include = field_value in condition['in']
            elif 'not_equals' in condition:
                include = field_value != condition['not_equals']
        
        if include:
            required_docs.append({
                'slug': rule['slug'],
                'name': rule['name'],
                'reason': reason,
                'always': rule.get('always', False),
                'is_placeholder': rule.get('is_placeholder', False)
            })
    
    return required_docs


def validate_intake_data(schema: dict, intake_data: dict) -> tuple:
    """
    Validate that all required questions have been answered.
    
    Args:
        schema: The intake schema
        intake_data: The user's answers
    
    Returns:
        Tuple of (is_valid, list of missing field ids)
    """
    missing = []
    
    for section in schema.get('sections', []):
        for question in section.get('questions', []):
            if question.get('required', False):
                field_id = question['id']
                value = intake_data.get(field_id)
                
                # Check if value is provided (not None and not empty string)
                if value is None or value == '':
                    missing.append(field_id)
    
    return (len(missing) == 0, missing)


def get_question_labels(schema: dict) -> dict:
    """
    Get a mapping of question IDs to their labels for display.
    """
    labels = {}
    for section in schema.get('sections', []):
        for question in section.get('questions', []):
            labels[question['id']] = question['label']
    return labels

