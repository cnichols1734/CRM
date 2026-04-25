"""
Magic Inbox orchestrator: take a normalized inbound payload, run the AI
extraction, dedupe, create Contact rows, write status back to the
InboundMessage row, and notify the user.

Single AI path — vCards, CSVs, photos, signatures, all the same call.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func

from models import (
    Contact,
    ContactGroup,
    InboundMessage,
    User,
    db,
)
from services.ai_service import (
    INBOX_PRIMARY_MODEL,
    generate_contact_extraction,
)
from services.inbound_payload import NormalizedInbound
from services.notification_service import create_notification
from services.sendgrid_outbound import (
    make_undo_token,
    send_inbox_receipt,
)
from utils import format_phone_number

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cost approximation — only used so we can store something useful on the row.
# Real per-call cost lives in OpenAI's billing console.
# ---------------------------------------------------------------------------
# Prices in USD per 1M tokens (input, output). Update if pricing drifts.
_MODEL_PRICING = {
    'gpt-5.4-nano': (0.20, 1.25),
    'gpt-5-nano': (0.20, 1.25),
    'gpt-5-mini': (0.25, 2.00),
    'gpt-5.1': (1.25, 10.00),
}


def _estimate_cost_cents(model: str | None,
                         tokens_in: int | None,
                         tokens_out: int | None) -> Decimal | None:
    if not model or tokens_in is None or tokens_out is None:
        return None
    in_p, out_p = _MODEL_PRICING.get(model, _MODEL_PRICING.get(INBOX_PRIMARY_MODEL,
                                                              (0.20, 1.25)))
    cost_usd = (tokens_in / 1_000_000.0) * in_p + (tokens_out / 1_000_000.0) * out_p
    return Decimal(str(round(cost_usd * 100, 4)))


# ---------------------------------------------------------------------------
# Limits / helpers
# ---------------------------------------------------------------------------

def _org_can_add_more(org, count: int = 1) -> tuple[bool, str]:
    """`tenant_service.org_can_add_contact` requires current_user — this
    variant works in the webhook context where we have the org explicitly.
    """
    if org is None or org.max_contacts is None:
        return True, ''
    current_count = Contact.query.filter_by(organization_id=org.id).count()
    if current_count + count > org.max_contacts:
        return False, (
            f'Contact limit reached ({org.max_contacts}). '
            'Upgrade to Pro for unlimited.'
        )
    return True, ''


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    return format_phone_number(raw)


def _normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = str(raw).strip().lower()
    return cleaned or None


def _title_case_name(raw: str | None) -> str:
    if not raw:
        return ''
    return ' '.join(part.capitalize() for part in str(raw).strip().split())


def _existing_contact_for(user_id: int, *, email: str | None, phone: str | None,
                          first_name: str, last_name: str) -> Contact | None:
    """Mirrors the dedupe rules from `routes/contacts.py` lines 657-688."""
    if email:
        dup = (Contact.query
               .filter(Contact.user_id == user_id)
               .filter(func.lower(Contact.email) == email)
               .first())
        if dup:
            return dup
    if phone:
        dup = (Contact.query
               .filter(Contact.user_id == user_id, Contact.phone == phone)
               .first())
        if dup:
            return dup
    if not email and not phone and (first_name or last_name):
        dup = (Contact.query
               .filter(Contact.user_id == user_id)
               .filter(func.lower(Contact.first_name) == first_name.lower())
               .filter(func.lower(Contact.last_name) == last_name.lower())
               .first())
        if dup:
            return dup
    return None


def _group_lookup_key(value: str | None) -> str:
    return ''.join(ch for ch in (value or '').lower() if ch.isalnum())


def _resolve_group_by_name(org_id: int,
                           requested_name: str | None) -> ContactGroup | None:
    """Resolve body/alias group hints to an existing group in the org."""
    key = _group_lookup_key(requested_name)
    if not key:
        return None
    groups = (ContactGroup.query
              .filter(ContactGroup.organization_id == org_id)
              .all())
    for group in groups:
        if _group_lookup_key(group.name) == key:
            return group
    return None


def _build_notes(raw_notes: str | None,
                 sender_email: str | None,
                 source_kind: str) -> str | None:
    pieces = []
    if raw_notes:
        pieces.append(str(raw_notes).strip())
    provenance_bits = []
    if sender_email:
        provenance_bits.append(f'forwarded from {sender_email}')
    provenance_bits.append(f'via Magic Inbox ({source_kind})')
    pieces.append('Added ' + ', '.join(provenance_bits) + '.')
    notes = '\n\n'.join(p for p in pieces if p)
    return notes[:2000] if notes else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_inbound(user: User, message: InboundMessage,
                    bundle: NormalizedInbound) -> dict:
    """Process a normalized inbound payload end-to-end.

    Updates *message* in place with status, ai cost, and created contact
    ids, then commits. Always returns a small dict summarising what happened
    so the webhook can log a one-liner.

    Returns shape::

        {
          'created_contacts': [Contact, ...],
          'status': 'processed' | 'over_limit' | 'failed' | 'rejected',
          'reason': '<human-readable, or empty>',
        }
    """
    org = user.organization
    if org is None:
        message.status = 'failed'
        message.error_message = 'User has no organization'
        message.processed_at = datetime.utcnow()
        db.session.commit()
        return {'created_contacts': [], 'status': 'failed',
                'reason': 'no organization'}

    # --- Empty payload guard -------------------------------------------
    if not bundle.cleaned_text and not bundle.image_blocks:
        message.status = 'rejected'
        message.error_message = 'Empty payload'
        message.processed_at = datetime.utcnow()
        db.session.commit()
        return {'created_contacts': [], 'status': 'rejected',
                'reason': 'empty payload'}

    # --- AI extraction --------------------------------------------------
    try:
        result = generate_contact_extraction(
            text=bundle.cleaned_text,
            image_blocks=bundle.image_blocks,
        )
    except Exception as e:
        logger.exception('Magic Inbox AI extraction failed inbound_id=%s', message.id)
        message.status = 'failed'
        message.error_message = f'AI extraction failed: {e}'[:1000]
        message.processed_at = datetime.utcnow()
        db.session.commit()
        return {'created_contacts': [], 'status': 'failed',
                'reason': 'AI extraction failed'}

    meta = result.get('_meta') or {}
    ai_model = meta.get('model') or INBOX_PRIMARY_MODEL
    tokens_in = meta.get('tokens_in')
    tokens_out = meta.get('tokens_out')

    message.ai_model = ai_model
    message.ai_tokens_in = tokens_in
    message.ai_tokens_out = tokens_out
    message.ai_cost_cents = _estimate_cost_cents(ai_model, tokens_in, tokens_out)

    raw_contacts = result.get('contacts') or []
    if not isinstance(raw_contacts, list):
        raw_contacts = []

    # --- Filter / build candidates -------------------------------------
    candidates = []
    for entry in raw_contacts:
        if not isinstance(entry, dict):
            continue
        confidence = (entry.get('confidence') or 'low').lower()

        first = _title_case_name(entry.get('first_name'))
        last = _title_case_name(entry.get('last_name'))
        email = _normalize_email(entry.get('email'))
        phone = _normalize_phone(entry.get('phone'))

        # Drop garbage rows: need a name AND at least one of email/phone, OR
        # 'high'/'medium' confidence with a full name.
        has_name = bool(first or last)
        has_signal = bool(email or phone)
        if not has_name:
            continue
        if not has_signal and confidence == 'low':
            continue

        # Drop self-references (the user forwarded their own signature).
        if email and user.email and email == user.email.lower():
            continue

        candidates.append({
            'first_name': first,
            'last_name': last,
            'email': email,
            'phone': phone,
            'street_address': (entry.get('street_address') or '').strip() or None,
            'city': (entry.get('city') or '').strip() or None,
            'state': (entry.get('state') or '').strip() or None,
            'zip_code': (entry.get('zip_code') or '').strip() or None,
            'notes': entry.get('notes'),
            'group_name': (entry.get('group_name') or '').strip() or None,
        })

    # --- Free-tier limit check (per user_id is the contact owner) ------
    allowed, limit_reason = _org_can_add_more(org, count=len(candidates))
    if not allowed:
        message.status = 'over_limit'
        message.error_message = limit_reason
        message.processed_at = datetime.utcnow()
        db.session.commit()
        return {'created_contacts': [], 'status': 'over_limit',
                'reason': limit_reason}

    # --- Dedupe + create -----------------------------------------------
    created: list[Contact] = []
    skipped_dupes = 0

    for c in candidates:
        existing = _existing_contact_for(
            user.id, email=c['email'], phone=c['phone'],
            first_name=c['first_name'], last_name=c['last_name'],
        )
        if existing is not None:
            skipped_dupes += 1
            continue

        contact = Contact(
            organization_id=org.id,
            user_id=user.id,
            created_by_id=user.id,
            first_name=c['first_name'] or '',
            last_name=c['last_name'] or '',
            email=c['email'],
            phone=c['phone'],
            street_address=c['street_address'],
            city=c['city'],
            state=c['state'],
            zip_code=c['zip_code'],
            notes=_build_notes(c['notes'],
                               sender_email=message.sender_email,
                               source_kind=bundle.source_kind),
        )
        group = _resolve_group_by_name(
            org.id,
            c.get('group_name') or bundle.plus_alias,
        )
        if group is not None:
            contact.groups.append(group)
        db.session.add(contact)
        created.append(contact)

    if created:
        try:
            db.session.commit()
        except Exception:
            logger.exception('Magic Inbox commit failed inbound_id=%s', message.id)
            db.session.rollback()
            message.status = 'failed'
            message.error_message = 'Database error while saving contacts'
            message.processed_at = datetime.utcnow()
            db.session.commit()
            return {'created_contacts': [], 'status': 'failed',
                    'reason': 'db error'}

    message.created_contact_ids = [c.id for c in created]
    message.status = 'processed' if created else 'rejected'
    message.error_message = (None if created
                             else f'No new contacts (dupes={skipped_dupes})')
    message.processed_at = datetime.utcnow()
    db.session.commit()

    # --- Side effects: notification + receipt email --------------------
    if created:
        _send_in_app_notification(user, created)
        _send_receipt_email(
            user, message, created, skipped_count=skipped_dupes)

    logger.info(
        'Magic Inbox processed inbound_id=%s created=%d skipped_dupes=%d kind=%s',
        message.id, len(created), skipped_dupes, bundle.source_kind,
    )

    return {
        'created_contacts': created,
        'status': message.status,
        'reason': message.error_message or '',
    }


# ---------------------------------------------------------------------------
# Side effects (best-effort — never block the webhook)
# ---------------------------------------------------------------------------

def _send_in_app_notification(user: User, contacts: list[Contact]) -> None:
    try:
        from flask import url_for
        if len(contacts) == 1:
            c = contacts[0]
            name = f'{c.first_name} {c.last_name}'.strip() or 'a new contact'
            title = f'Saved {name} to your CRM'
            try:
                action_url = url_for('contacts.view_contact', contact_id=c.id)
            except Exception:
                action_url = '/contacts'
        else:
            title = f'Saved {len(contacts)} contacts to your CRM'
            action_url = '/contacts'

        create_notification(
            user_id=user.id,
            organization_id=user.organization_id,
            category='magic_inbox',
            title=title,
            body='Forwarded via your magic inbox.',
            icon='fa-paper-plane',
            action_url=action_url,
        )
    except Exception:
        logger.exception(
            'Failed to write magic inbox notification user_id=%s', user.id,
        )


def _send_receipt_email(user: User, message: InboundMessage,
                        contacts: list[Contact],
                        skipped_count: int = 0) -> None:
    """Send the receipt to the Magic Inbox owner, never the outside sender."""
    try:
        token = make_undo_token(message.id)
        send_inbox_receipt(
            user, contacts,
            undo_token=token,
            inbound_recipient=message.recipient_address,
            sender_email=message.sender_email,
            source_kind=message.source_kind,
            source_subject=message.subject,
            skipped_count=skipped_count,
            sent_at=message.created_at,
        )
    except Exception:
        logger.exception(
            'Failed to send magic inbox receipt user_id=%s inbound_id=%s',
            user.id, message.id,
        )
