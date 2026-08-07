"""Server-built transaction setup facts and Bob briefing copy.

Used for rich page-entity hydration and the one-shot post-bootstrap chat
briefing. Facts are authorized by the caller (transaction already scoped).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from models import (
    ChatConversation,
    ChatMessage,
    ContractBootstrapSession,
    SellerCommissionTerms,
    Transaction,
    TransactionDocument,
    db,
)


def _document_has_file(doc: TransactionDocument) -> bool:
    return bool(
        getattr(doc, 'signed_file_path', None)
        or getattr(doc, 'source_file_path', None)
        or getattr(doc, 'signed_original_filename', None)
    )


def _yes_no(value: Any) -> str | None:
    if value in (True, 'yes', 'true', '1', 'Yes'):
        return 'yes'
    if value in (False, 'no', 'false', '0', 'No'):
        return 'no'
    if value in (None, ''):
        return None
    return str(value)


def _commission_summary(terms: SellerCommissionTerms | None) -> dict[str, Any] | None:
    if not terms:
        return None
    out: dict[str, Any] = {}
    if terms.listing_commission_percent is not None:
        out['listing_side_percent'] = float(terms.listing_commission_percent)
    if terms.listing_commission_flat is not None:
        out['listing_side_flat'] = float(terms.listing_commission_flat)
    if terms.coop_compensation_percent is not None:
        out['buyer_side_percent'] = float(terms.coop_compensation_percent)
    if terms.coop_compensation_flat is not None:
        out['buyer_side_flat'] = float(terms.coop_compensation_flat)
    if terms.notes:
        out['notes'] = str(terms.notes)[:300]
    if terms.source:
        out['source'] = terms.source
    return out or None


def _latest_bootstrap_session(
    *,
    transaction_id: int,
    org_id: int,
) -> ContractBootstrapSession | None:
    return (
        ContractBootstrapSession.query.filter_by(
            organization_id=org_id,
            matched_transaction_id=transaction_id,
        )
        .order_by(ContractBootstrapSession.id.desc())
        .first()
    )


def build_transaction_setup_facts(
    transaction: Transaction,
    *,
    organization_id: int | None = None,
) -> dict[str, Any]:
    """Collect deal setup facts for prompts and briefings."""
    org_id = organization_id or transaction.organization_id
    docs = TransactionDocument.query.filter_by(
        transaction_id=transaction.id,
        organization_id=org_id,
    ).all()

    filed = []
    needed = []
    for doc in docs:
        slug = (doc.template_slug or '').strip()
        name = (doc.template_name or slug or 'Document').strip()
        entry = {
            'slug': slug,
            'name': name,
            'filename': doc.signed_original_filename,
            'placeholder': bool(getattr(doc, 'is_placeholder', False)),
        }
        if _document_has_file(doc) and not getattr(doc, 'is_placeholder', False):
            filed.append(entry)
        elif getattr(doc, 'is_placeholder', False) and not _document_has_file(doc):
            needed.append(entry)
        elif not _document_has_file(doc):
            needed.append(entry)

    # Checklist Needed rows (document placeholders + missing expected docs).
    try:
        from services.checklist_service import build_checklist
        checklist = build_checklist(transaction, org_id)
        for item in checklist:
            if item.get('document_state') != 'missing':
                continue
            slug = (item.get('expected_document_slug') or item.get('key') or '').strip()
            title = (item.get('title') or slug).strip()
            if not slug and not title:
                continue
            if any(n.get('slug') == slug for n in needed if slug):
                continue
            if any(n.get('name') == title for n in needed):
                continue
            needed.append({
                'slug': slug,
                'name': title,
                'filename': None,
                'placeholder': True,
            })
    except Exception:
        pass

    intake = transaction.intake_data if isinstance(transaction.intake_data, dict) else {}
    questionnaire = {
        'built_before_1978': _yes_no(intake.get('built_before_1978')),
        'has_hoa': _yes_no(intake.get('has_hoa')),
        'special_districts': _yes_no(intake.get('special_districts')),
        'flood_hazard': _yes_no(intake.get('flood_hazard')),
        'has_septic': _yes_no(intake.get('has_septic')),
        'has_survey': intake.get('has_survey'),
        'referral_fee': _yes_no(intake.get('referral_fee')),
    }
    questionnaire = {k: v for k, v in questionnaire.items() if v not in (None, '')}

    terms = SellerCommissionTerms.query.filter_by(
        transaction_id=transaction.id,
        organization_id=org_id,
    ).first()

    bootstrap = _latest_bootstrap_session(
        transaction_id=transaction.id,
        org_id=org_id,
    )
    bootstrap_summary = None
    issues: list[str] = []
    if bootstrap:
        classification = bootstrap.classification or {}
        review = classification.get('review_summary') or {}
        identity = classification.get('document_identity') or {}
        bootstrap_summary = {
            'session_id': bootstrap.id,
            'status': bootstrap.status,
            'filename': bootstrap.original_filename,
            'identity_label': identity.get('label'),
            'identity_kind': identity.get('kind'),
            'route_action': (
                (classification.get('route_decision') or {}).get('action')
                or review.get('route_action')
            ),
            'applied_field_count': review.get('applied_field_count'),
            'document_count': review.get('document_count'),
            'batch_sibling_document_ids': review.get('batch_sibling_document_ids'),
        }
        err = classification.get('processing_error')
        if err:
            issues.append(str(err)[:240])
        if bootstrap.status == ContractBootstrapSession.STATUS_FAILED:
            issues.append(
                f'Bootstrap session for {bootstrap.original_filename or "upload"} failed.'
            )

    type_name = (
        transaction.transaction_type.name
        if transaction.transaction_type
        else None
    )
    return {
        'transaction_id': transaction.id,
        'address': transaction.street_address,
        'city': transaction.city,
        'state': transaction.state,
        'zip_code': transaction.zip_code,
        'status': transaction.status,
        'type': type_name,
        'documents_filed': filed,
        'documents_needed': needed,
        'questionnaire': questionnaire,
        'commission': _commission_summary(terms),
        'bootstrap': bootstrap_summary,
        'issues': issues,
    }


def _format_place(facts: dict[str, Any]) -> str:
    """Build a clean place string without duplicating city/state already in street."""
    street = str(facts.get('address') or '').strip()
    city = str(facts.get('city') or '').strip()
    state = str(facts.get('state') or '').strip()
    zip_code = str(facts.get('zip_code') or '').strip()
    if not street:
        return 'this property'

    street_l = street.lower()
    # Address already carries locality (common after listing extraction).
    if city and city.lower() in street_l:
        return street

    locality_bits = []
    if city:
        locality_bits.append(city)
    if state:
        locality_bits.append(state)
    locality = ', '.join(locality_bits)
    if locality and zip_code:
        locality = f'{locality} {zip_code}'
    elif zip_code:
        locality = zip_code
    if locality and locality.lower() not in street_l:
        return f'{street}, {locality}'
    return street


def _short_doc_name(doc: dict[str, Any]) -> str:
    name = (doc.get('name') or doc.get('slug') or 'Document').strip()
    # Prefer the human template name; filenames are usually noisy duplicates.
    replacements = (
        ('Residential Real Estate Listing Agreement', 'Listing Agreement'),
        ('Information About Brokerage Services', 'IABS'),
        ('Notice to Purchaser of Real Property Located in a Special Tax District',
         'Special Tax District Notice'),
        ("Seller's Estimated Net Proceeds", 'Seller Net Proceeds'),
        ('T-47.1 Residential Real Property Affidavit', 'T-47.1 Affidavit'),
        ('HOA / POA Addendum', 'HOA Addendum'),
    )
    for long, short in replacements:
        if name.lower() == long.lower() or name.lower().startswith(long.lower()):
            return short
    return name


def _questionnaire_bits(questionnaire: dict[str, Any]) -> list[str]:
    bits: list[str] = []
    hoa = questionnaire.get('has_hoa')
    if hoa == 'yes':
        bits.append('HOA')
    elif hoa == 'no':
        bits.append('no HOA')

    districts = questionnaire.get('special_districts')
    if districts == 'yes':
        bits.append('special tax district')
    elif districts == 'no':
        bits.append('no special tax district')

    pre1978 = questionnaire.get('built_before_1978')
    if pre1978 == 'yes':
        bits.append('built before 1978')
    elif pre1978 == 'no':
        bits.append('built 1978 or later')

    survey = str(questionnaire.get('has_survey') or '').strip().lower()
    if survey in ('yes',):
        bits.append('survey available')
    elif survey in ('no',):
        bits.append('no survey')
    elif survey in ('not_sure', 'not sure', 'unsure'):
        bits.append('survey unclear')

    septic = questionnaire.get('has_septic')
    if septic == 'yes':
        bits.append('septic')
    flood = questionnaire.get('flood_hazard')
    if flood == 'yes':
        bits.append('flood hazard')
    return bits


def _format_commission_line(commission: dict[str, Any]) -> str | None:
    listing = None
    buyer = None
    if commission.get('listing_side_flat') is not None:
        listing = f"${float(commission['listing_side_flat']):,.0f} listing"
    elif commission.get('listing_side_percent') is not None:
        listing = f"{float(commission['listing_side_percent']):g}% listing"

    if commission.get('buyer_side_percent') is not None:
        buyer = f"{float(commission['buyer_side_percent']):g}% buyer broker"
    elif commission.get('buyer_side_flat') is not None:
        buyer = f"${float(commission['buyer_side_flat']):,.0f} buyer broker"

    if listing and buyer:
        return f'{listing} + {buyer}'
    if listing or buyer:
        return listing or buyer

    notes = str(commission.get('notes') or '').strip()
    return notes or None


def format_setup_briefing(facts: dict[str, Any]) -> str:
    """Conversational assistant message from setup facts (no LLM)."""
    place = _format_place(facts)
    filed = facts.get('documents_filed') or []
    needed = facts.get('documents_needed') or []
    issues = facts.get('issues') or []

    lines = [
        f"**{place}** is set up from your upload.",
        '',
    ]

    if filed:
        lines.append(f'**Filed ({len(filed)})**')
        for doc in filed[:12]:
            lines.append(f'- {_short_doc_name(doc)}')
        if len(filed) > 12:
            lines.append(f'- …and {len(filed) - 12} more')
        lines.append('')
    else:
        lines.append('No PDFs filed on this deal yet.')
        lines.append('')

    if needed:
        lines.append(f'**Still need ({len(needed)})**')
        for doc in needed[:12]:
            lines.append(f'- {_short_doc_name(doc)}')
        lines.append('')
    elif filed:
        lines.append('Nothing else required from the questionnaire right now.')
        lines.append('')

    q_bits = _questionnaire_bits(facts.get('questionnaire') or {})
    if q_bits:
        lines.append('**Property** — ' + ' · '.join(q_bits))
        lines.append('')

    commission_line = _format_commission_line(facts.get('commission') or {})
    if commission_line:
        lines.append(f'**Commission** — {commission_line}')
        lines.append('')

    if issues:
        lines.append('**Heads up**')
        for issue in issues[:5]:
            lines.append(f'- {issue}')
        lines.append('')

    if needed:
        lines.append(
            'I can help chase the missing docs or walk next steps — what do you want first?'
        )
    else:
        lines.append(
            'Want a quick next-step plan, or anything to double-check on this file?'
        )
    return '\n'.join(lines).strip()


def conversation_title_for_transaction(transaction: Transaction) -> str:
    street = (transaction.street_address or 'Transaction').strip()
    # Keep titles short for the history dropdown.
    if len(street) > 40:
        street = street[:37] + '…'
    return f'{street} setup'


def ensure_transaction_conversation(
    *,
    user_id: int,
    org_id: int,
    transaction: Transaction,
    channel: str = 'web',
) -> ChatConversation:
    """Resume or create the deal-scoped web conversation."""
    existing = (
        ChatConversation.query.filter_by(
            user_id=user_id,
            organization_id=org_id,
            transaction_id=transaction.id,
            channel=channel,
        )
        .order_by(ChatConversation.updated_at.desc())
        .first()
    )
    if existing:
        return existing

    conversation = ChatConversation(
        user_id=user_id,
        organization_id=org_id,
        transaction_id=transaction.id,
        channel=channel,
        title=conversation_title_for_transaction(transaction),
    )
    db.session.add(conversation)
    db.session.flush()
    return conversation


def seed_setup_briefing(
    *,
    conversation: ChatConversation,
    transaction: Transaction,
    force: bool = False,
) -> tuple[ChatConversation, ChatMessage | None, bool]:
    """Idempotently seed the assistant setup briefing.

    Returns (conversation, message, created).
    """
    if conversation.setup_briefing_sent_at and not force:
        first = (
            conversation.messages.filter_by(role='assistant')
            .order_by(ChatMessage.created_at.asc())
            .first()
        )
        return conversation, first, False

    # Prefer briefing when bootstrap exists or docs/intake are present.
    facts = build_transaction_setup_facts(
        transaction,
        organization_id=conversation.organization_id,
    )
    has_signal = bool(
        facts.get('bootstrap')
        or facts.get('documents_filed')
        or facts.get('documents_needed')
        or facts.get('questionnaire')
    )
    if not has_signal and not force:
        return conversation, None, False

    text = format_setup_briefing(facts)
    message = ChatMessage(
        conversation_id=conversation.id,
        role='assistant',
        content=text,
    )
    db.session.add(message)
    conversation.setup_briefing_sent_at = datetime.utcnow()
    conversation.updated_at = datetime.utcnow()
    if not conversation.title:
        conversation.title = conversation_title_for_transaction(transaction)
    db.session.flush()
    return conversation, message, True


def rebuild_session_history_from_conversation(
    conversation: ChatConversation,
    *,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Build Flask session chat_history rows from persisted messages."""
    messages = (
        conversation.messages
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    rows = [
        {'role': m.role, 'content': m.content}
        for m in messages
        if m.role in ('user', 'assistant') and m.content
    ]
    if len(rows) > limit:
        rows = rows[-limit:]
    return rows


def page_entity_summary_from_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Compact summary dict for PageEntityContext / system prompt."""
    return {
        'address': facts.get('address'),
        'city': facts.get('city'),
        'state': facts.get('state'),
        'zip_code': facts.get('zip_code'),
        'status': facts.get('status'),
        'type': facts.get('type'),
        'documents_filed': [
            {
                'slug': d.get('slug'),
                'name': d.get('name'),
                'filename': d.get('filename'),
            }
            for d in (facts.get('documents_filed') or [])[:20]
        ],
        'documents_needed': [
            {'slug': d.get('slug'), 'name': d.get('name')}
            for d in (facts.get('documents_needed') or [])[:20]
        ],
        'questionnaire': facts.get('questionnaire') or {},
        'commission': facts.get('commission'),
        'bootstrap': facts.get('bootstrap'),
        'issues': facts.get('issues') or [],
    }
