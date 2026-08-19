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

    # Contract bootstrap may create a seller file before ownership details are
    # known. Conventional is the safe questionnaire default; the agent can
    # change ownership type later without losing the reviewed contract.
    if transaction_type == 'seller' and not ownership_status:
        conventional_path = SCHEMAS_DIR / 'seller_conventional.json'
        if conventional_path.exists():
            with open(conventional_path, 'r') as f:
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


def _candidate_value(candidates: dict, *keys):
    """Return the first concrete bootstrap candidate value for the supplied keys."""
    for key in keys:
        raw = (candidates or {}).get(key)
        value = raw.get('value') if isinstance(raw, dict) and 'value' in raw else raw
        if value not in (None, '', [], {}):
            return value
    return None


def _normalized_boolean(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in ('true', 'yes', '1', 'present', 'applies'):
        return True
    if normalized in ('false', 'no', '0', 'absent', 'does_not_apply'):
        return False
    return None


def _schema_option_values(schema: dict) -> dict[str, set]:
    options = {}
    for section in schema.get('sections', []):
        for question in section.get('questions', []):
            options[question['id']] = {
                item.get('value') for item in question.get('options', [])
            }
    return options


def seller_intake_handoff_url(transaction, document=None) -> str | None:
    """URL for the seller questionnaire when a doc approval should hand off to it.

    Returns the intake questionnaire URL only when this is a seller-side
    transaction whose questionnaire has not been answered yet and the approved
    document is (or contains) a listing agreement. Otherwise returns None so
    callers fall back to the transaction detail page.
    """
    from flask import url_for

    if transaction is None:
        return None
    side = (getattr(transaction.transaction_type, 'name', '') or '').lower()
    if side != 'seller':
        return None
    if transaction.intake_data:
        return None

    is_listing_doc = False
    if document is not None:
        slug = (getattr(document, 'template_slug', '') or '').strip().lower().replace('_', '-')
        if slug == 'listing-agreement':
            is_listing_doc = True
        else:
            field_data = getattr(document, 'field_data', None)
            if isinstance(field_data, dict):
                identity = field_data.get('_document_identity') or {}
                if (identity.get('kind') or '') == 'listing_agreement':
                    is_listing_doc = True
    if not is_listing_doc:
        return None

    return url_for(
        'transactions.intake_questionnaire',
        id=transaction.id,
        source='document_review',
    )


def _evidence_pool(transaction, bootstrap_session=None) -> dict:
    """Flatten extracted fields from bootstrap + filed transaction documents.

    Later sources only fill gaps — they never overwrite an earlier concrete value.
    """
    pool: dict = {}

    def absorb(source: dict | None):
        if not isinstance(source, dict):
            return
        for key, raw in source.items():
            if str(key).startswith('_'):
                continue
            value = raw.get('value') if isinstance(raw, dict) and 'value' in raw else raw
            if value in (None, '', [], {}):
                continue
            if pool.get(key) in (None, '', [], {}):
                pool[key] = value

    if bootstrap_session is not None:
        absorb(getattr(bootstrap_session, 'extracted_candidates', None) or {})
        absorb((getattr(bootstrap_session, 'classification', None) or {}))

    docs = list(getattr(transaction, 'documents', None) or [])
    if not docs and getattr(transaction, 'id', None):
        try:
            from models import TransactionDocument
            docs = TransactionDocument.query.filter_by(
                transaction_id=transaction.id,
                organization_id=transaction.organization_id,
            ).all()
        except Exception:
            docs = []

    # Prefer listing agreement / disclosures that answer property facts.
    slug_rank = {
        'listing-agreement': 0,
        'sellers-disclosure': 1,
        'lead-paint': 2,
    }
    docs = sorted(
        docs,
        key=lambda d: slug_rank.get(
            (getattr(d, 'template_slug', None) or '').strip().lower().replace('_', '-'),
            50,
        ),
    )
    for doc in docs:
        absorb(getattr(doc, 'field_data', None) or {})

    return pool


def _infer_from_special_provisions(text: str) -> dict:
    """Deterministic property-fact hints from listing Special Provisions text."""
    import re

    hints = {}
    blob = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not blob:
        return hints
    lower = blob.lower()

    if re.search(
        r'\bhoa\b|\bpoa\b|owners[\'’]?\s+association|property\s+owners',
        lower,
    ):
        hints['has_hoa'] = True

    if re.search(
        r'existing\s+survey|survey\s+dated|provide(?:s|d)?\s+(?:an?\s+)?(?:existing\s+)?survey|'
        r't-?47(?:\.1)?',
        lower,
    ):
        hints['has_survey'] = 'yes'
    elif re.search(r'no\s+survey|survey\s+not\s+available|without\s+a\s+survey', lower):
        hints['has_survey'] = 'no'

    if re.search(
        r'\bmud\b|public\s+improvement\s+district|\bpid\b|special\s+tax(?:ing)?\s+district',
        lower,
    ):
        hints['special_districts'] = True

    if re.search(r'flood\s+hazard|flood\s+zone|sfha|fema\s+flood', lower):
        hints['flood_hazard'] = True

    if re.search(r'\bseptic\b|on-?site\s+sewer|on-?site\s+sewage', lower):
        hints['has_septic'] = True

    if re.search(r'referral\s+fee|referral\s+agreement', lower):
        hints['referral_fee'] = True

    return hints


def build_intake_prefill(transaction, schema: dict, bootstrap_session=None) -> tuple[dict, list[str]]:
    """Merge saved answers with conservative suggestions from reviewed docs.

    Saved agent answers always win. Suggestions are returned for display only and
    are not persisted until the agent saves the questionnaire.

    Only facts the document actually states are suggested. Unchecked addenda
    checkboxes on a listing agreement are never treated as "No".
    """
    merged = dict(transaction.intake_data or {})
    pool = _evidence_pool(transaction, bootstrap_session=bootstrap_session)
    if not pool and not bootstrap_session:
        return merged, []

    options = _schema_option_values(schema)
    suggestions = {}
    side = (getattr(transaction.transaction_type, 'name', '') or '').lower()

    if side == 'buyer':
        suggestions['buyer_stage'] = 'under_contract'

        contract_type = str(
            pool.get('purchase_contract_type')
            or pool.get('contract_type')
            or pool.get('document_type')
            or ''
        ).strip().lower().replace('-', '_').replace(' ', '_')
        contract_aliases = {
            'resale_one_to_four': 'resale_one_to_four',
            'one_to_four_family': 'resale_one_to_four',
            'one_to_four_family_residential_contract': 'resale_one_to_four',
            'residential_contract': 'resale_one_to_four',
            'condominium': 'condominium',
            'condominium_contract': 'condominium',
            'new_construction_complete': 'new_construction_complete',
            'new_home_completed': 'new_construction_complete',
            'new_construction_incomplete': 'new_construction_incomplete',
            'new_home_incomplete': 'new_construction_incomplete',
            'farm_and_ranch': 'farm_and_ranch',
            'farm_and_ranch_contract': 'farm_and_ranch',
            'other': 'not_sure',
            'unknown': 'not_sure',
        }
        if contract_type in contract_aliases:
            suggestions['purchase_type'] = contract_aliases[contract_type]

        financing = str(pool.get('financing_type') or '').strip().lower()
        if financing == 'cash':
            suggestions['financing'] = 'cash'
        elif financing in ('conventional', 'fha', 'va', 'usda', 'third_party'):
            suggestions['financing'] = 'third_party'
        elif financing in ('seller_financing', 'assumption', 'other'):
            suggestions['financing'] = 'assumption_seller_financing_other'
        elif financing == 'unknown':
            suggestions['financing'] = 'not_sure'

        for candidate_key, question_id in (
            ('built_before_1978', 'built_before_1978'),
            ('hoa_applicable', 'has_association'),
            ('has_hoa', 'has_association'),
        ):
            value = _normalized_boolean(pool.get(candidate_key))
            if value is not None and question_id not in suggestions:
                suggestions[question_id] = 'yes' if value else 'no'

        contingency = _normalized_boolean(pool.get('sale_of_other_property_contingency'))
        if contingency is not None:
            suggestions['buyer_sale_contingency'] = contingency

        lease_type = str(pool.get('temporary_lease_type') or '').strip().lower()
        lease_aliases = {
            'buyer_temporary_lease': 'buyer_temporary_lease',
            'seller_temporary_lease': 'seller_temporary_lease',
            'none': 'none',
            'unknown': 'not_sure',
        }
        if lease_type in lease_aliases:
            suggestions['temporary_lease'] = lease_aliases[lease_type]

    elif side == 'seller':
        # Explicit extracted fields first.
        for candidate_key, question_id in (
            ('built_before_1978', 'built_before_1978'),
            ('has_hoa', 'has_hoa'),
            ('hoa_applicable', 'has_hoa'),
            ('special_districts', 'special_districts'),
            ('flood_hazard', 'flood_hazard'),
            ('has_septic', 'has_septic'),
            ('referral_fee', 'referral_fee'),
        ):
            value = _normalized_boolean(pool.get(candidate_key))
            if value is not None and question_id not in suggestions:
                suggestions[question_id] = value

        survey = _normalized_boolean(pool.get('has_existing_survey'))
        if survey is True:
            suggestions['has_survey'] = 'yes'
        elif survey is False:
            suggestions['has_survey'] = 'no'
        else:
            survey_text = str(
                pool.get('survey_choice')
                or pool.get('survey_furnished_by')
                or ''
            ).strip().lower()
            if survey_text:
                if any(word in survey_text for word in ('existing', 'furnished', 'attached', 'yes')):
                    suggestions['has_survey'] = 'yes'
                elif any(word in survey_text for word in ('new survey', 'no survey', 'not available')):
                    suggestions['has_survey'] = 'no'

        # Special Provisions often carries HOA name + survey language when 2E
        # checkboxes did not extract cleanly.
        for key, value in _infer_from_special_provisions(
            pool.get('special_provisions') or '',
        ).items():
            if key not in suggestions:
                suggestions[key] = value

    suggested_fields = []
    for field_id, value in suggestions.items():
        if field_id in merged and merged[field_id] not in (None, ''):
            continue
        allowed = options.get(field_id)
        if allowed and value not in allowed:
            continue
        merged[field_id] = value
        suggested_fields.append(field_id)

    return merged, suggested_fields


def compute_document_diff(schema: dict, intake_data: dict, existing_docs: dict) -> dict:
    """
    Evaluate document rules and compute add/remove/keep diff against existing docs.

    Only document slots declared by this questionnaire are managed. Uploaded
    contracts, custom files, and documents owned by other workflows survive a
    questionnaire re-sync.

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

    schema_managed_slugs = {
        rule.get('slug') for rule in schema.get('document_rules', [])
        if rule.get('slug')
    }
    managed_slugs = set(existing_docs) & schema_managed_slugs

    to_keep = managed_slugs & required_slugs
    to_remove = managed_slugs - required_slugs
    to_add = required_slugs - managed_slugs

    # A reviewed, uploaded contract already attached by bootstrap satisfies the
    # questionnaire's contract slot even when its exact form family was unknown
    # during extraction. Never add a duplicate placeholder or delete that file.
    contract_slugs = {
        'seller-accepted-contract',
        'one-to-four-family-contract',
        'condominium-contract',
        'new-home-completed-construction-contract',
        'new-home-incomplete-construction-contract',
        'farm-and-ranch-contract',
        'purchase-contract',
    }
    required_contracts = required_slugs & contract_slugs
    fulfilled_contracts = [
        (slug, doc) for slug, doc in existing_docs.items()
        if slug in contract_slugs
        and not getattr(doc, 'is_placeholder', False)
        and getattr(doc, 'status', None) in ('filled', 'generated', 'sent', 'signed')
    ]
    satisfied_aliases = {}
    if required_contracts and fulfilled_contracts:
        actual_slug, _actual_doc = fulfilled_contracts[0]
        for required_slug in required_contracts:
            if required_slug == actual_slug:
                continue
            satisfied_aliases[required_slug] = actual_slug
            to_add.discard(required_slug)
        to_remove.discard(actual_slug)

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
        'satisfied_aliases': satisfied_aliases,
    }


def _split_listing_package(doc, file_bytes=None, *, require_confident=True):
    """Slice a listing packet into child documents. Never fails the upload."""
    import logging

    try:
        from models import db as _db
        from services.seller_workflow import ensure_listing_package_split

        children = ensure_listing_package_split(
            doc, file_bytes=file_bytes, require_confident=require_confident,
        )
        if children:
            _db.session.commit()
            logging.getLogger(__name__).info(
                'Split listing packet doc %s into %d child document(s)',
                doc.id, len(children),
            )
        return children
    except Exception:
        logging.getLogger(__name__).exception(
            'Listing package split failed for doc %s', getattr(doc, 'id', None),
        )
        try:
            from models import db as _db
            _db.session.rollback()
        except Exception:
            pass
        return []


def post_upload_processing(doc, file_bytes=None):
    """
    Split mixed packets, then enqueue background AI extraction.

    Non-fatal: if Redis/RQ is unavailable the upload still succeeds and
    extraction runs in a local background thread as a dev fallback.

    ``file_bytes`` lets an upload route hand over the PDF it already has in
    memory so the splitter does not re-download it from storage.
    """
    import logging
    import os
    from services.document_privacy import (
        apply_sensitivity_to_document,
        may_use_in_llm,
    )

    doc_id = doc.id
    org_id = doc.organization_id

    # Bind uploaded docs to matching pack requirements via evidence.
    # Non-fatal: evidence attach must never fail the upload itself.
    # All documents.py upload paths (fulfill, upload-static, upload-scan,
    # upload-for-signature, upload-external, upload-completed) funnel here,
    # as do offers/seller_contracts uploads — one hook covers every transition
    # from placeholder/empty to having a real file.
    try:
        from services.requirement_evidence import auto_attach_for_document
        from models import db as _db

        actor_id = None
        try:
            from flask_login import current_user
            if getattr(current_user, 'is_authenticated', False):
                actor_id = current_user.id
        except Exception:
            actor_id = None

        auto_attach_for_document(doc, actor_id=actor_id)

        # If this upload was classified at upload time and an open placeholder
        # exists for the same slug, the upload takes the placeholder's slot.
        from services.checklist_service import absorb_matching_placeholder
        absorb_matching_placeholder(doc, actor_id=actor_id)

        try:
            from models import Transaction as _Transaction
            from services.listing_prep_checklist import sync_listing_prep_checklist
            tx = _Transaction.query.get(doc.transaction_id)
            if tx:
                sync_listing_prep_checklist(tx, actor_id=actor_id)
        except Exception:
            logging.getLogger(__name__).exception(
                'Listing prep checklist sync failed for doc %s', doc_id,
            )

        _db.session.commit()
    except Exception:
        logging.getLogger(__name__).exception(
            'Failed to auto-attach requirement evidence for doc %s', doc_id,
        )
        try:
            from models import db as _db
            _db.session.rollback()
        except Exception:
            pass

    # Phase 3: stamp sensitivity from document type / transaction side.
    try:
        side = None
        if getattr(doc, 'transaction_id', None):
            from models import Transaction
            tx = Transaction.query.get(doc.transaction_id)
            if tx and getattr(tx, 'transaction_type', None):
                side = (tx.transaction_type.name or '').lower()
        apply_sensitivity_to_document(document=doc, transaction_side=side)
        from models import db
        doc.extraction_status = 'pending'
        doc.extraction_error = None
        doc.field_data = None
        db.session.flush()
    except Exception:
        logging.getLogger(__name__).exception(
            'Failed to apply sensitivity for doc %s', doc_id,
        )

    # A single upload is often a whole listing packet. Slice the supporting
    # forms into their own documents before extraction so each one is a real
    # file the agent can open, and so the listing PDF holds listing pages only.
    # Packets we cannot fully account for wait for the AI pass in extraction.
    for child in _split_listing_package(doc, file_bytes):
        try:
            post_upload_processing(child)
        except Exception:
            logging.getLogger(__name__).exception(
                'Post-upload processing failed for split child doc %s', child.id,
            )

    if not may_use_in_llm(doc):
        logging.getLogger(__name__).info(
            'Skipping extraction enqueue for doc %s (privacy)',
            doc_id,
        )
        # No AI pass is coming for this document, so this is the last chance to
        # file the packet. Split best-effort and flag what we could not name.
        for child in _split_listing_package(doc, file_bytes, require_confident=False):
            try:
                post_upload_processing(child)
            except Exception:
                logging.getLogger(__name__).exception(
                    'Post-upload processing failed for split child doc %s', child.id,
                )
        from models import db
        doc.extraction_status = 'failed'
        doc.extraction_error = 'BOB review skipped because this document is restricted by privacy controls.'
        db.session.commit()
        try:
            from services.document_review import finalize_document_review
            finalize_document_review(
                document_id=doc_id,
                org_id=org_id,
                manual_review_reason=(
                    'BOB did not send this restricted document to an AI model. '
                    'Review the original file manually.'
                ),
            )
        except Exception:
            logging.getLogger(__name__).exception(
                'Failed to create manual-review report for doc %s', doc_id,
            )
        return

    logger = logging.getLogger(__name__)
    inline_enabled = os.getenv('DOCUMENT_EXTRACTION_INLINE', '').lower() in ('1', 'true', 'yes')

    def run_in_background_thread():
        """Fallback for local/dev when Redis is not available."""
        import threading
        from flask import current_app

        app = current_app._get_current_object()

        def runner():
            with app.app_context():
                from jobs.document_extraction import extract_document_job
                extract_document_job(doc_id=doc_id, org_id=org_id)

        thread = threading.Thread(
            target=runner,
            name=f"document-extraction-{doc_id}",
            daemon=True,
        )
        thread.start()

    try:
        from config import Config
        if inline_enabled:
            from jobs.document_extraction import extract_document_job
            logger.info(f"Running inline document extraction for doc {doc_id}")
            extract_document_job(doc_id=doc_id, org_id=org_id, _inline=True)
            return

        if Config.SQLALCHEMY_DATABASE_URI.startswith('sqlite'):
            logger.info(f"Starting background document extraction thread for doc {doc_id}")
            run_in_background_thread()
            return

        if Config.FLASK_ENV != 'production' and not os.getenv('REDIS_URL'):
            logger.info(f"Starting dev background document extraction thread for doc {doc_id}")
            run_in_background_thread()
            return

        from redis import Redis
        from rq import Queue

        conn = Redis.from_url(
            Config.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        q = Queue('doc_extraction', connection=conn)
        q.enqueue(
            'jobs.document_extraction.extract_document_job',
            doc_id=doc_id,
            org_id=org_id,
            job_timeout=300,
        )
    except Exception as e:
        logger.warning(
            f"Failed to enqueue extraction for doc {doc_id}: {e}. "
            "Falling back to local background thread.",
            exc_info=True,
        )
        try:
            run_in_background_thread()
        except Exception:
            logger.error(
                f"Failed to start background extraction for doc {doc_id}. "
                "extraction_status may remain pending for manual retry.",
                exc_info=True,
            )
