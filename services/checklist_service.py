"""
Merged transaction checklist: requirements + document placeholders as one list.

Requirements that declare a document_slug fold the matching TransactionDocument
into the same row so the UI does not show the same real-world item twice.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from models import Transaction, TransactionDocument, TransactionRequirement, db
from services.deadline_rules import DeadlineRulesService
from services.requirement_evidence import evidence_for_requirements

_INTAKE_SCHEMAS_DIR = Path(__file__).parent.parent / 'intake_schemas'
_SLUG_NAME_CACHE: Optional[Dict[str, str]] = None


def _document_has_file(doc: TransactionDocument) -> bool:
    return bool(doc.signed_file_path or doc.source_file_path)


def _doc_summary(doc: Optional[TransactionDocument]) -> Optional[Dict[str, Any]]:
    if doc is None:
        return None
    return {
        'id': doc.id,
        'template_slug': doc.template_slug,
        'template_name': doc.template_name,
        'status': doc.status,
        'is_placeholder': bool(doc.is_placeholder),
        'extraction_status': doc.extraction_status,
    }


def _load_pack_safe(package_key: Optional[str], version: Optional[str] = None):
    if not package_key:
        return None
    ver = (version or 'v1').strip() or 'v1'
    try:
        return DeadlineRulesService.load_pack(package_key.strip(), ver)
    except FileNotFoundError:
        try:
            return DeadlineRulesService.load_pack(package_key.strip(), 'v1')
        except FileNotFoundError:
            return None


def _phase_label(pack: Optional[dict], phase_key: Optional[str]) -> Optional[str]:
    if not pack or not phase_key:
        return None
    for phase in pack.get('phases') or []:
        if phase.get('phase_key') == phase_key:
            return phase.get('name') or phase_key
    return None


def _phase_order(pack: Optional[dict]) -> Dict[str, int]:
    order: Dict[str, int] = {}
    if not pack:
        return order
    for idx, phase in enumerate(pack.get('phases') or []):
        key = phase.get('phase_key')
        if key:
            order[str(key)] = idx
    return order


def _intake_slug_names() -> Dict[str, str]:
    """Map template_slug → human name from any intake schema document_rules."""
    global _SLUG_NAME_CACHE
    if _SLUG_NAME_CACHE is not None:
        return _SLUG_NAME_CACHE

    names: Dict[str, str] = {}
    if _INTAKE_SCHEMAS_DIR.is_dir():
        for path in sorted(_INTAKE_SCHEMAS_DIR.glob('*.json')):
            try:
                with open(path, 'r') as f:
                    schema = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            for rule in schema.get('document_rules') or []:
                slug = (rule.get('slug') or '').strip()
                name = (rule.get('name') or '').strip()
                if slug and name and slug not in names:
                    names[slug] = name
    _SLUG_NAME_CACHE = names
    return names


def _expected_slug_for_requirement(
    requirement: TransactionRequirement,
    pack: Optional[dict] = None,
) -> Optional[str]:
    req_pack = pack
    if req_pack is None:
        req_pack = _load_pack_safe(
            requirement.package_key,
            requirement.deadline_rule_version,
        )
    if not req_pack:
        return None
    return DeadlineRulesService.expected_document_slug(
        req_pack, requirement.requirement_key,
    )


def ensure_expected_placeholder(
    transaction: Transaction,
    organization_id: int,
    requirement: TransactionRequirement,
    *,
    actor_id: Optional[int] = None,
) -> TransactionDocument:
    """
    Ensure a TransactionDocument placeholder exists for the requirement's
    pack-declared document_slug so upload/auto-attach can reach it.

    Idempotent: returns the existing row when the slug is already on the file.
    """
    from services import audit_service

    if not transaction or not getattr(transaction, 'id', None):
        raise ValueError('Transaction is required')
    if requirement.organization_id != organization_id:
        raise ValueError('Requirement belongs to a different organization')
    if transaction.organization_id != organization_id:
        raise ValueError('Transaction belongs to a different organization')
    if requirement.transaction_id != transaction.id:
        raise ValueError('Requirement belongs to a different transaction')

    expected_slug = _expected_slug_for_requirement(requirement)
    if not expected_slug:
        raise ValueError(
            f'Requirement {requirement.requirement_key!r} has no expected document'
        )

    existing = TransactionDocument.query.filter_by(
        organization_id=organization_id,
        transaction_id=transaction.id,
        template_slug=expected_slug,
    ).first()
    if existing:
        return existing

    template_name = (
        _intake_slug_names().get(expected_slug)
        or requirement.title
        or expected_slug
    )
    included_reason = f'Required by: {requirement.title or requirement.requirement_key}'

    doc = TransactionDocument(
        organization_id=organization_id,
        transaction_id=transaction.id,
        template_slug=expected_slug,
        template_name=template_name,
        included_reason=included_reason,
        status='pending',
        is_placeholder=True,
        document_source='placeholder',
    )
    db.session.add(doc)
    db.session.flush()

    audit_service.log_document_added(
        doc, included_reason, actor_id=actor_id,
    )
    return doc


def absorb_matching_placeholder(
    document: TransactionDocument,
    *,
    actor_id: Optional[int] = None,
) -> Optional[int]:
    """Fold a redundant placeholder into a real uploaded document.

    When an uploaded document is classified (at upload, classification confirm,
    or extraction retag) and an open placeholder for the same template_slug
    exists on the same transaction, the real document takes the placeholder's
    slot: the placeholder row is removed and its included_reason is preserved
    on the real document. Returns the absorbed placeholder id, or None.

    Does not commit; callers own the transaction boundary.
    """
    from services import audit_service

    if (
        document is None
        or not document.id
        or not document.transaction_id
        or document.is_placeholder
        or not document.template_slug
        or not _document_has_file(document)
    ):
        return None

    placeholder = (
        TransactionDocument.query.filter_by(
            organization_id=document.organization_id,
            transaction_id=document.transaction_id,
            template_slug=document.template_slug,
            is_placeholder=True,
        )
        .filter(TransactionDocument.id != document.id)
        .filter(TransactionDocument.signed_file_path.is_(None))
        .first()
    )
    if placeholder is None:
        return None

    if placeholder.included_reason and not document.included_reason:
        document.included_reason = placeholder.included_reason
    if placeholder.template_name and not document.template_name:
        document.template_name = placeholder.template_name

    placeholder_id = placeholder.id
    db.session.delete(placeholder)
    db.session.flush()

    try:
        audit_service.log_event(
            event_type='document_placeholder_fulfilled',
            transaction_id=document.transaction_id,
            document_id=document.id,
            description=(
                f'Uploaded document matched the required '
                f'{document.template_name or document.template_slug} slot'
            ),
            event_data={
                'template_slug': document.template_slug,
                'absorbed_placeholder_id': placeholder_id,
            },
            source='system',
            actor_id=actor_id,
        )
    except Exception:
        # Audit must never break the upload pipeline.
        pass

    return placeholder_id


def build_checklist(
    transaction: Transaction,
    organization_id: int,
) -> List[Dict[str, Any]]:
    """
    Build a merged, ordered checklist for a transaction.

    Query budget (bounded, no per-item queries):
      1. requirements for tx+org
      2. documents for tx+org
      3. evidence for those requirement ids
    Pack JSON / intake slug names are disk/cache (not DB queries).
    """
    if not transaction or not getattr(transaction, 'id', None):
        return []

    tx_id = transaction.id
    org_id = organization_id

    # Query 1
    requirements = TransactionRequirement.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
    ).all()

    # Query 2
    documents = TransactionDocument.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
    ).all()

    # Query 3
    evidence_map = evidence_for_requirements(
        tx_id,
        org_id,
        [r.id for r in requirements],
    )

    if not requirements and not documents:
        return []

    # Prefer pack from transaction resolution; fall back to requirement package_keys.
    pack = None
    pack_key = None
    try:
        pack_key, pack = DeadlineRulesService.resolve_pack_for_transaction(
            transaction,
        )
    except FileNotFoundError:
        pack = None
        pack_key = None

    if pack is None and requirements:
        # Use the most common package_key among requirements.
        pack = _load_pack_safe(requirements[0].package_key, requirements[0].deadline_rule_version)
        pack_key = requirements[0].package_key if pack else None

    slug_by_req_key: Dict[str, str] = (
        DeadlineRulesService.document_slugs_for_pack(pack) if pack else {}
    )
    # Also merge slugs from per-requirement packs if they differ.
    pack_cache: Dict[str, Optional[dict]] = {}
    if pack_key:
        pack_cache[pack_key] = pack

    docs_by_slug: Dict[str, TransactionDocument] = {}
    for doc in documents:
        slug = (doc.template_slug or '').strip()
        if not slug:
            continue
        # Prefer a fulfilled doc over a placeholder when duplicates exist.
        existing = docs_by_slug.get(slug)
        if existing is None:
            docs_by_slug[slug] = doc
        elif _document_has_file(doc) and not _document_has_file(existing):
            docs_by_slug[slug] = doc

    folded_doc_ids: Set[int] = set()
    items: List[Dict[str, Any]] = []

    for req in requirements:
        pk = (req.package_key or '').strip()
        req_pack = pack
        if pk and pk != (pack_key or ''):
            if pk not in pack_cache:
                pack_cache[pk] = _load_pack_safe(pk, req.deadline_rule_version)
            req_pack = pack_cache[pk]

        expected_slug = None
        if req_pack:
            expected_slug = DeadlineRulesService.expected_document_slug(
                req_pack, req.requirement_key,
            )
        if expected_slug is None:
            expected_slug = slug_by_req_key.get(req.requirement_key)

        matched_doc = None
        document_state = 'not_expected'
        if expected_slug:
            matched_doc = docs_by_slug.get(expected_slug)
            if matched_doc is not None:
                folded_doc_ids.add(matched_doc.id)
                document_state = (
                    'uploaded' if _document_has_file(matched_doc) else 'missing'
                )
            else:
                document_state = 'missing'

        phase_key = req.phase_key
        items.append({
            'kind': 'requirement',
            'key': req.requirement_key,
            'title': req.title,
            'phase_key': phase_key,
            'phase_label': _phase_label(req_pack, phase_key),
            'due_at': req.due_at,
            'work_status': req.work_status,
            'timing_state': req.timing_state,
            'responsible_party_label': req.responsible_party_label,
            'requirement_id': req.id,
            'expected_document_slug': expected_slug,
            'expected_document_exists': matched_doc is not None,
            'document': _doc_summary(matched_doc),
            'document_state': document_state,
            'evidence_count': len(evidence_map.get(req.id) or []),
            '_phase_order': _phase_order(req_pack).get(phase_key or '', 999),
        })

    # Empty placeholders that the questionnaire no longer requires (e.g. lead
    # paint after "built before 1978? No") must not appear as required docs.
    not_applicable_placeholder_slugs: Set[str] = set()
    try:
        from services.intake_service import (
            evaluate_document_rules,
            get_intake_schema,
        )
        type_name = (
            transaction.transaction_type.name
            if transaction.transaction_type
            else None
        )
        intake = transaction.intake_data if isinstance(transaction.intake_data, dict) else {}
        if type_name and intake:
            schema = get_intake_schema(type_name, transaction.ownership_status)
            if schema:
                required_slugs = {
                    d['slug'] for d in evaluate_document_rules(schema, intake)
                }
                managed_slugs = {
                    rule.get('slug')
                    for rule in schema.get('document_rules', [])
                    if rule.get('slug')
                }
                not_applicable_placeholder_slugs = managed_slugs - required_slugs
    except Exception:
        not_applicable_placeholder_slugs = set()

    for doc in documents:
        if doc.id in folded_doc_ids:
            continue
        slug = (doc.template_slug or '').strip()
        if (
            slug in not_applicable_placeholder_slugs
            and getattr(doc, 'is_placeholder', False)
            and not _document_has_file(doc)
        ):
            continue
        items.append({
            'kind': 'document',
            'key': doc.template_slug,
            'title': doc.template_name or doc.template_slug,
            'phase_key': None,
            'phase_label': None,
            'due_at': None,
            'work_status': None,
            'timing_state': None,
            'responsible_party_label': None,
            'requirement_id': None,
            'expected_document_slug': doc.template_slug,
            'expected_document_exists': True,
            'document': _doc_summary(doc),
            'document_state': (
                'uploaded' if _document_has_file(doc) else 'missing'
            ),
            'evidence_count': 0,
            '_phase_order': 9999,
        })

    def sort_key(item: Dict[str, Any]):
        due = item.get('due_at')
        # Dated first (ascending), then undated requirements by phase, then docs.
        if due is not None:
            # due_at is naive UTC everywhere today, but one aware value mixed in
            # would make the comparison raise and take down the whole checklist.
            if isinstance(due, datetime) and due.tzinfo is not None:
                due = due.astimezone(timezone.utc).replace(tzinfo=None)
            return (0, due, item.get('title') or '')
        kind_rank = 0 if item.get('kind') == 'requirement' else 1
        return (
            1,
            kind_rank,
            item.get('_phase_order', 9999),
            item.get('title') or '',
        )

    items.sort(key=sort_key)

    for item in items:
        item.pop('_phase_order', None)

    return items
