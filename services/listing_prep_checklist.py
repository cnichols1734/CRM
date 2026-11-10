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
    'listing_docs_setup',
    'seller_home_facts',
    'comparative_market_analysis',
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
    'listing_docs_setup',
    'listing_agreement',
    'seller_disclosures',
    'listing_docs_complete',
    'listing_description',
    'showing_instructions',
})

TITLES = {
    'listing_docs_setup': 'Listing Docs Setup',
    'seller_home_facts': 'Send Sellers Home Facts Questionnaire',
    'comparative_market_analysis': 'Create Comparative Market Analysis',
    'listing_agreement': 'Sign Listing Agreement',
    'seller_disclosures': "Complete Seller's Disclosure",
    'listing_docs_complete': 'Upload Remaining Listing Documents',
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

# Own checklist rows — the remaining-docs item is everything else.
OWN_LISTING_CHECKLIST_SLUGS = frozenset({
    'listing-agreement',
    'sellers-disclosure',
})

HIDDEN_KEYS = frozenset({'photos_ready', 'offer_intake_ready'})

KEY_PHASE = {
    'listing_docs_setup': 'listing_docs',
    'seller_home_facts': 'listing_docs',
    'comparative_market_analysis': 'listing_docs',
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
    """Slugs the questionnaire (or fallback pack) expects on a listing file."""
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


def remaining_listing_slugs(transaction: Transaction) -> list[str]:
    """Supporting listing forms still needed after agreement and disclosure."""
    return [
        slug
        for slug in expected_listing_slugs(transaction)
        if slug not in OWN_LISTING_CHECKLIST_SLUGS
    ]


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

    remaining = remaining_listing_slugs(transaction)
    listing_docs_done = all(slug in uploaded for slug in remaining)

    done_by_key = {
        'listing_docs_setup': bool(transaction.intake_data),
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
        if req.requirement_key == 'seller_home_facts':
            sync_home_facts_requirement(transaction, req)
            continue
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
    remaining_count: Optional[int] = None,
) -> dict[str, Any]:
    status = (req.work_status or 'pending').lower()
    custom = req.source == 'manual' and req.phase_key == 'custom'
    return {
        'id': req.id,
        'key': req.requirement_key,
        'sort_rank': VISIBLE_KEYS.index(req.requirement_key) if req.requirement_key in VISIBLE_KEYS else len(VISIBLE_KEYS),
        'title': TITLES.get(req.requirement_key, req.title),
        'done': status in CLOSED_WORK_STATUSES,
        'auto': (not custom) and req.requirement_key in AUTO_KEYS,
        'custom': custom,
        'work_status': status,
        'due_at': req.due_at,
        'task_id': req.task_id,
        'source': req.source,
        'description': description if req.requirement_key == 'listing_description' else None,
        'remaining_count': (
            remaining_count if req.requirement_key == 'listing_docs_complete' else None
        ),
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
    documents = TransactionDocument.query.filter_by(transaction_id=transaction.id).all()
    uploaded = _uploaded_slugs(documents)
    remaining_count = sum(
        1 for slug in remaining_listing_slugs(transaction) if slug not in uploaded
    )

    groups = []
    from models import HomeFactsResponse
    home_facts = HomeFactsResponse.query.filter_by(
        organization_id=transaction.organization_id, transaction_id=transaction.id,
    ).first()
    for phase_key, label in LISTING_PREP_PHASES:
        items = []
        if phase_key == 'custom':
            items = [_row_payload(req) for req in custom_reqs]
        else:
            for key in VISIBLE_KEYS:
                req = reqs.get(key)
                if not req or KEY_PHASE.get(key) != phase_key:
                    continue
                row = _row_payload(
                    req,
                    description=description,
                    remaining_count=remaining_count,
                )
                if key == 'seller_home_facts':
                    row['response_status'] = home_facts_status(home_facts, req)
                items.append(row)
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


def home_facts_status(response, requirement):
    if response and response.state == 'submitted':
        return 'Submitted'
    if requirement.work_status == 'not_applicable':
        return 'Skipped'
    if requirement.work_status == 'completed':
        return 'Marked complete'
    return 'In progress' if response and response.answers else 'Not completed'


def set_home_facts_requirement_status(requirement, status, actor_id):
    from models import HomeFactsResponse, TransactionRequirementEvent
    if status not in ('pending', 'completed', 'not_applicable'):
        raise ValueError('Choose complete, incomplete or skipped.')
    response = HomeFactsResponse.query.filter_by(organization_id=requirement.organization_id,
        transaction_id=requirement.transaction_id).first()
    RequirementsService.update_work_status(requirement.id, status, actor_id=actor_id)
    db.session.add(TransactionRequirementEvent(organization_id=requirement.organization_id,
        requirement_id=requirement.id, event_type='home_facts_override', actor_id=actor_id,
        new_value={'work_status': status, 'submitted_revision': response.submitted_revision
            if response and response.state == 'submitted' else None}))
    db.session.flush()
    return requirement


def sync_home_facts_requirement(transaction, requirement):
    from models import HomeFactsResponse, TransactionRequirementEvent
    response = HomeFactsResponse.query.filter_by(organization_id=transaction.organization_id,
        transaction_id=transaction.id).first()
    override = TransactionRequirementEvent.query.filter_by(organization_id=transaction.organization_id,
        requirement_id=requirement.id, event_type='home_facts_override').order_by(TransactionRequirementEvent.id.desc()).first()
    submitted = response and response.state == 'submitted'
    manual = (override.new_value or {}) if override else None
    if manual and (not submitted or manual.get('submitted_revision') == response.submitted_revision):
        target = manual['work_status']
    else:
        target = 'completed' if submitted else 'pending'
    if requirement.work_status != target:
        RequirementsService.update_work_status(requirement.id, target)


def listing_prep_next_action(transaction, item):
    from flask import url_for
    if not item:
        return None
    key = item['key']
    target = f"#listing-prep-{item['id']}"
    cta = 'Open item'
    if key == 'listing_docs_setup':
        target = url_for('transactions.intake_questionnaire', id=transaction.id)
        cta = 'Set up listing docs'
    elif key == 'seller_home_facts':
        target = url_for('home_facts.agent_workspace', transaction_id=transaction.id)
        cta = 'Open Home Facts'
    elif key in ('listing_agreement', 'seller_disclosures', 'listing_docs_complete'):
        slug = {'listing_agreement': 'listing-agreement', 'seller_disclosures': 'sellers-disclosure'}.get(key)
        document = TransactionDocument.query.filter_by(transaction_id=transaction.id,
            organization_id=transaction.organization_id, template_slug=slug).first() if slug else None
        target = (url_for('transactions.document_form', id=transaction.id, doc_id=document.id)
            if document else '#listing-documents')
        cta = 'Open document' if document else 'Open listing documents'
    elif item.get('task_id'):
        target = url_for('tasks.view_task', task_id=item['task_id'])
        cta = 'Open task'
    return {'title': item['title'], 'description': 'Next item on the Preparing to List checklist.', 'cta': cta, 'url': target}
