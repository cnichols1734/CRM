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
    # Prefer the most specific schema, then fall back to the transaction type.
    if ownership_status:
        schema_path = SCHEMAS_DIR / f"{transaction_type}_{ownership_status}.json"
        if schema_path.exists():
            with open(schema_path, 'r') as f:
                return json.load(f)

    schema_path = SCHEMAS_DIR / f"{transaction_type}.json"
    if schema_path.exists():
        with open(schema_path, 'r') as f:
            return json.load(f)

    return None


def _condition_matches(condition: dict, intake_data: dict) -> bool:
    """Evaluate a document rule condition against intake answers."""
    if not condition:
        return False

    if 'all' in condition:
        return all(_condition_matches(item, intake_data) for item in condition['all'])

    if 'any' in condition:
        return any(_condition_matches(item, intake_data) for item in condition['any'])

    field = condition.get('field')
    if not field:
        return False

    field_value = intake_data.get(field)

    if 'equals' in condition:
        return field_value == condition['equals']
    if 'in' in condition:
        return field_value in condition['in']
    if 'not_equals' in condition:
        return field_value != condition['not_equals']

    return False


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
            include = _condition_matches(rule['condition'], intake_data)
        
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


def compute_document_diff(schema: dict, intake_data: dict, existing_docs: dict) -> dict:
    """
    Evaluate document rules and compute add/remove/keep diff against existing docs.

    Manually added placeholder docs (slugs starting with 'custom-') are excluded
    from the diff so they survive questionnaire re-sync.

    Args:
        schema: The intake schema with document_rules
        intake_data: The user's questionnaire answers
        existing_docs: Dict of {template_slug: TransactionDocument} for the transaction

    Returns:
        Dict with keys:
            required_docs_by_slug, to_add, to_remove, to_keep,
            blocked_removals, safe_removals
    """
    required_docs = evaluate_document_rules(schema, intake_data)
    required_docs_by_slug = {doc['slug']: doc for doc in required_docs}
    required_slugs = set(required_docs_by_slug.keys())

    # Exclude manually-added custom placeholders from diffing
    managed_slugs = {slug for slug in existing_docs if not slug.startswith('custom-')}

    to_keep = managed_slugs & required_slugs
    to_remove = managed_slugs - required_slugs
    to_add = required_slugs - managed_slugs

    blocked_removals = []
    safe_removals = []
    for slug in to_remove:
        doc = existing_docs[slug]
        if doc.status in ('sent', 'signed'):
            blocked_removals.append({
                'slug': slug,
                'name': doc.template_name,
                'status': doc.status,
            })
        else:
            safe_removals.append({
                'slug': slug,
                'name': doc.template_name,
                'status': doc.status,
            })

    return {
        'required_docs': required_docs,
        'required_docs_by_slug': required_docs_by_slug,
        'to_add': to_add,
        'to_remove': to_remove,
        'to_keep': to_keep,
        'blocked_removals': blocked_removals,
        'safe_removals': safe_removals,
    }


def post_upload_processing(doc):
    """
    Enqueue background AI extraction for fulfilled placeholder documents.

    Non-fatal: if Redis/RQ is unavailable the upload still succeeds and
    extraction_status stays 'pending' for later retry.
    """
    import logging
    from services.document_extractor import EXTRACTION_SCHEMAS

    if doc.template_slug not in EXTRACTION_SCHEMAS:
        return

    logger = logging.getLogger(__name__)

    try:
        from redis import Redis
        from rq import Queue
        from config import Config

        conn = Redis.from_url(
            Config.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        q = Queue('doc_extraction', connection=conn)
        q.enqueue(
            'jobs.document_extraction.extract_document_job',
            doc_id=doc.id,
            org_id=doc.organization_id,
            job_timeout=300,
        )
    except Exception as e:
        logger.error(
            f"Failed to enqueue extraction for doc {doc.id}: {e}. "
            "extraction_status remains 'pending' for manual retry.",
            exc_info=True,
        )

