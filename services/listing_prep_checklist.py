"""Preparing-to-List checklist: seed, auto-check, and grouped display."""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Optional

from models import (
    SellerListingProfile,
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    db,
)
from services.deadline_rules import DeadlineRulesService
from services.requirements_service import CLOSED_WORK_STATUSES, RequirementsService

LISTING_PREP_PHASES = (
    ('listing_docs', 'Listing Documents'),
    ('property_prep', 'Property & Marketing Prep'),
    ('mls_setup', 'MLS Setup'),
    ('custom', 'Your items'),
)

VISIBLE_KEYS = (
    'listing_agreement',
    'seller_disclosures',
    'listing_docs_complete',
    'confirm_property_details',
    'verify_room_sizes',
    'schedule_photography',
    'confirm_showing_availability',
    'signage_lockbox',
    'mls_input_attested',
    'upload_photos_media',
    'listing_description',
    'showing_instructions',
    'review_mls_accuracy',
)

AUTO_KEYS = frozenset({
    'listing_agreement',
    'seller_disclosures',
    'listing_docs_complete',
    'listing_description',
    'showing_instructions',
})

TITLES = {
    'listing_agreement': 'Sign Listing Agreement',
    'seller_disclosures': "Complete Seller's Disclosure",
    'listing_docs_complete': 'Upload Listing Documents',
    'confirm_property_details': 'Confirm Property Details with Seller',
    'verify_room_sizes': 'Verify Room Sizes, Features & Amenities',
    'schedule_photography': 'Schedule Photography',
    'confirm_showing_availability': 'Confirm Showing Availability & Instructions',
    'signage_lockbox': 'Install Sign and Lockbox',
    'mls_input_attested': 'Complete MLS Listing Input',
    'upload_photos_media': 'Upload Photos & Media',
    'listing_description': 'Add Property Description',
    'showing_instructions': 'Enter Showing Instructions',
    'review_mls_accuracy': 'Review MLS Listing for Accuracy',
}

FALLBACK_LISTING_SLUGS = (
    'listing-agreement',
    'iabs',
    'sellers-disclosure',
    'wire-fraud-warning',
    'seller-net-proceeds',
)

HIDDEN_KEYS = frozenset({'photos_ready', 'offer_intake_ready'})

KEY_PHASE = {
    'listing_agreement': 'listing_docs',
    'seller_disclosures': 'listing_docs',
    'listing_docs_complete': 'listing_docs',
    'confirm_property_details': 'property_prep',
    'verify_room_sizes': 'property_prep',
    'schedule_photography': 'property_prep',
    'confirm_showing_availability': 'property_prep',
    'signage_lockbox': 'property_prep',
    'mls_input_attested': 'mls_setup',
    'upload_photos_media': 'mls_setup',
    'listing_description': 'mls_setup',
    'showing_instructions': 'mls_setup',
    'review_mls_accuracy': 'mls_setup',
}


def _document_has_file(doc: Optional[TransactionDocument]) -> bool:
    if doc is None:
        return False
    return bool(doc.signed_file_path or doc.source_file_path)


def _is_seller(transaction: Transaction) -> bool:
    name = getattr(getattr(transaction, 'transaction_type', None), 'name', '') or ''
    return name.lower() == 'seller'


def listing_description_text(profile: Optional[SellerListingProfile]) -> str:
    extra = (getattr(profile, 'extra_data', None) or {}) if profile else {}
    return str(extra.get('listing_description') or '').strip()


def listing_description_source(profile: Optional[SellerListingProfile]) -> str:
    extra = (getattr(profile, 'extra_data', None) or {}) if profile else {}
    source = str(extra.get('listing_description_source') or '').strip().lower()
    return source if source in ('ai', 'manual') else ''


def expected_listing_slugs(transaction: Transaction) -> list[str]:
    """Slugs that must be on file for 'Upload Listing Documents'."""
    from services.intake_service import evaluate_document_rules, get_intake_schema

    type_name = getattr(getattr(transaction, 'transaction_type', None), 'name', '') or 'seller'
    schema = get_intake_schema(type_name, transaction.ownership_status)
    intake = transaction.intake_data or {}
    if schema and intake:
        slugs = [
            rule['slug']
            for rule in evaluate_document_rules(schema, intake)
            if rule.get('slug')
        ]
        if slugs:
            return slugs
    if schema:
        slugs = [
            rule.get('slug')
            for rule in (schema.get('document_rules') or [])
            if rule.get('always') and rule.get('slug')
        ]
        if slugs:
            return slugs
    return list(FALLBACK_LISTING_SLUGS)


def _uploaded_slugs(documents: Iterable[TransactionDocument]) -> set[str]:
    uploaded = set()
    for doc in documents:
        slug = (doc.template_slug or '').strip()
        if slug and _document_has_file(doc) and not doc.is_placeholder:
            uploaded.add(slug)
        elif slug and _document_has_file(doc):
            uploaded.add(slug)
    return uploaded


def _set_auto_status(req: TransactionRequirement, done: bool, *, actor_id: Optional[int] = None) -> bool:
    current = (req.work_status or 'pending').lower()
    if current in ('not_applicable', 'superseded', 'waived', 'cancelled'):
        return False
    target = 'completed' if done else 'pending'
    if current == target:
        if done and req.title != TITLES.get(req.requirement_key, req.title):
            req.title = TITLES[req.requirement_key]
        return False
    RequirementsService.update_work_status(req.id, target, actor_id=actor_id)
    title = TITLES.get(req.requirement_key)
    if title:
        req.title = title
    return True


def seed_listing_prep_checklist(
    transaction: Transaction,
    organization_id: int,
    *,
    actor_id: Optional[int] = None,
) -> dict[str, Any]:
    """Create listing-pack rows for a seller file. Safe to call repeatedly."""
    if not _is_seller(transaction):
        return {'created': 0, 'skipped': 0}
    result = DeadlineRulesService.apply_pack_to_transaction(
        transaction_id=transaction.id,
        organization_id=organization_id,
        pack_key='listing',
        anchors={},
        side='seller',
        source='deadline_pack',
        actor_id=actor_id,
    )
    for req in TransactionRequirement.query.filter_by(
        transaction_id=transaction.id,
        organization_id=organization_id,
    ).all():
        title = TITLES.get(req.requirement_key)
        if title and req.title != title:
            req.title = title
        if req.requirement_key in VISIBLE_KEYS and req.work_status == 'waiting':
            req.work_status = 'pending'
        expected_phase = KEY_PHASE.get(req.requirement_key)
        if expected_phase and req.phase_key != expected_phase:
            req.phase_key = expected_phase
    return result


def sync_listing_prep_checklist(
    transaction: Transaction,
    *,
    actor_id: Optional[int] = None,
    documents: Optional[list[TransactionDocument]] = None,
) -> None:
    """Flip auto-checked listing-prep rows from documents and listing profile."""
    if not _is_seller(transaction):
        return

    org_id = transaction.organization_id
    seed_listing_prep_checklist(transaction, org_id, actor_id=actor_id)

    if documents is None:
        documents = TransactionDocument.query.filter_by(transaction_id=transaction.id).all()
    uploaded = _uploaded_slugs(documents)
    profile = SellerListingProfile.query.filter_by(
        transaction_id=transaction.id,
        organization_id=org_id,
    ).first()

    expected = expected_listing_slugs(transaction)
    listing_docs_done = bool(expected) and all(slug in uploaded for slug in expected)

    done_by_key = {
        'listing_agreement': 'listing-agreement' in uploaded,
        'seller_disclosures': 'sellers-disclosure' in uploaded,
        'listing_docs_complete': listing_docs_done,
        'listing_description': bool(listing_description_text(profile)),
        'showing_instructions': bool((getattr(profile, 'public_showing_instructions', None) or '').strip()),
    }

    reqs = TransactionRequirement.query.filter_by(
        transaction_id=transaction.id,
        organization_id=org_id,
    ).all()
    for req in reqs:
        if req.requirement_key not in done_by_key:
            title = TITLES.get(req.requirement_key)
            if title and req.title != title:
                req.title = title
            continue
        _set_auto_status(req, done_by_key[req.requirement_key], actor_id=actor_id)


def _row_payload(
    req: TransactionRequirement,
    *,
    description: Optional[str] = None,
) -> dict[str, Any]:
    status = (req.work_status or 'pending').lower()
    custom = req.source == 'manual' and req.phase_key == 'custom'
    return {
        'id': req.id,
        'key': req.requirement_key,
        'title': TITLES.get(req.requirement_key, req.title),
        'done': status == 'completed',
        'auto': (not custom) and req.requirement_key in AUTO_KEYS,
        'custom': custom,
        'work_status': status,
        'due_at': req.due_at,
        'task_id': req.task_id,
        'source': req.source,
        'description': description if req.requirement_key == 'listing_description' else None,
    }


def listing_prep_groups(transaction: Transaction) -> list[dict[str, Any]]:
    """Grouped visible checklist rows for the seller preparing-to-list card."""
    all_reqs = TransactionRequirement.query.filter_by(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
    ).all()
    reqs = {
        req.requirement_key: req
        for req in all_reqs
        if req.requirement_key in VISIBLE_KEYS
    }
    custom_reqs = [
        req for req in all_reqs
        if req.source == 'manual' and req.phase_key == 'custom'
    ]
    custom_reqs.sort(key=lambda req: (req.created_at or req.id, req.id))
    profile = SellerListingProfile.query.filter_by(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
    ).first()
    description = listing_description_text(profile)

    groups = []
    for phase_key, label in LISTING_PREP_PHASES:
        items = []
        if phase_key == 'custom':
            items = [_row_payload(req) for req in custom_reqs]
        else:
            for key in VISIBLE_KEYS:
                req = reqs.get(key)
                if not req or KEY_PHASE.get(key) != phase_key:
                    continue
                items.append(_row_payload(req, description=description))
        if items:
            groups.append({
                'key': phase_key,
                'label': label,
                'rows': items,
                'done_count': sum(1 for item in items if item['done']),
                'total': len(items),
            })
    return groups


def create_custom_listing_item(
    transaction: Transaction,
    title: str,
    *,
    due_at=None,
    actor_id: Optional[int] = None,
) -> TransactionRequirement:
    """Add a manual Preparing-to-List row. Creates a task only when dated."""
    clean = (title or '').strip()
    if not clean:
        raise ValueError('Enter an item name.')
    req = RequirementsService.create_requirement(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
        package_key='listing',
        phase_key='custom',
        requirement_key=f'custom_{uuid.uuid4().hex[:12]}',
        title=clean[:300],
        work_status='pending',
        source='manual',
        due_at=due_at,
        due_at_manual_override=due_at is not None,
        assignee_user_id=actor_id,
    )
    if due_at is not None:
        RequirementsService.sync_linked_task(req, actor_id=actor_id)
    return req


def delete_custom_listing_item(
    req: TransactionRequirement,
    *,
    actor_id: Optional[int] = None,
) -> None:
    if req.source != 'manual' or req.phase_key != 'custom':
        raise ValueError('Only custom items can be removed.')
    task = RequirementsService._linked_task(req)
    if task is not None and task.status != 'cancelled':
        task.status = 'cancelled'
    db.session.delete(req)


def first_open_listing_prep_item(groups: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for group in groups:
        for item in group.get('rows') or []:
            if not item.get('done'):
                return item
    return None
