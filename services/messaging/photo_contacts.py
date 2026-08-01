"""Telegram photo → contact extraction for B.O.B.

Reuses Magic Inbox's vision extraction (``generate_contact_extraction``) and
image prep (``image_to_base64_jpeg``). Telegram decides whether to ask before
creating or to create immediately when the caption/text is a clear save ask.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from models import BobAction, Contact, User, db
from services.ai_service import generate_contact_extraction
from services.bob_tools.context import BobContext, ToolResult
from services.bob_tools.registry import (
    CONFIRMATION_TTL_MINUTES,
    dispatch as bob_dispatch,
)
from services.inbound_payload import image_to_base64_jpeg
from utils import format_phone_number

logger = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_CANDIDATES = 5
PHOTO_SOURCE = 'telegram_photo'

# Caption/text that means "just save it" rather than "show me what you see".
_CREATE_INTENT_RE = re.compile(
    r'(?i)\b('
    r'create(\s+(this|the|a|my))?\s+contact'
    r'|add(\s+(this|the|a|my))?\s+contact'
    r'|save(\s+(this|the|a|my))?\s+contact'
    r'|add(\s+(them|him|her|this|these))?\s+to\s+(my\s+)?(crm|contacts?)'
    r'|save(\s+(them|him|her|this|these))?\s+to\s+(my\s+)?(crm|contacts?)'
    r'|new\s+contact'
    r'|put\s+(this|them|him|her)\s+in\s+(my\s+)?(crm|contacts?)'
    r')\b'
)


class PhotoContactError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def wants_create_contact(text: str) -> bool:
    """True when the agent already asked to create/save a contact."""
    return bool(_CREATE_INTENT_RE.search(text or ''))


def download_telegram_photo(transport: Any, file_id: str) -> bytes:
    try:
        meta = transport.get_file(file_id)
    except Exception as exc:
        logger.warning('Telegram getFile for photo failed: %s', exc)
        raise PhotoContactError(
            "Couldn't download that photo. Try again."
        ) from exc

    file_path = (meta or {}).get('file_path') or ''
    file_size = int((meta or {}).get('file_size') or 0)
    if file_size and file_size > MAX_PHOTO_BYTES:
        raise PhotoContactError(
            'That photo is too large. Try a clearer crop or a smaller image.'
        )
    if not file_path:
        raise PhotoContactError("Couldn't find that photo file. Try again.")

    try:
        data = transport.download_file(file_path)
    except Exception as exc:
        logger.warning('Telegram photo download failed: %s', exc)
        raise PhotoContactError(
            "Couldn't download that photo. Try again."
        ) from exc

    if not data:
        raise PhotoContactError('That photo looked empty.')
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoContactError(
            'That photo is too large. Try a clearer crop or a smaller image.'
        )
    return data


def extract_contact_candidates(
    image_bytes: bytes,
    *,
    caption: str = '',
    user: User,
) -> list[dict]:
    """Run Magic Inbox extraction on one photo and return usable candidates."""
    b64 = image_to_base64_jpeg(image_bytes)
    if not b64:
        raise PhotoContactError(
            "Couldn't read that image. Try a clearer photo of the card or email."
        )

    try:
        result = generate_contact_extraction(
            text=caption or '',
            image_blocks=[b64],
        )
    except Exception as exc:
        logger.warning('Telegram photo extraction failed: %s', exc)
        raise PhotoContactError(
            "Couldn't pull contact info from that photo. Try again, or type the details."
        ) from exc

    raw = result.get('contacts') or []
    if not isinstance(raw, list):
        return []

    user_email = (getattr(user, 'email', None) or '').strip().lower()
    candidates: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        normalized = _normalize_candidate(entry, user_email=user_email)
        if normalized is not None:
            candidates.append(normalized)
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates


def candidate_to_create_args(
    candidate: dict,
    *,
    provenance: str = 'Added via Telegram photo.',
) -> dict:
    """Shape a candidate for ``create_contact``."""
    notes = (candidate.get('notes') or '').strip()
    if notes:
        notes = f'{notes}\n\n{provenance}'
    else:
        notes = provenance

    args = {
        'first_name': candidate['first_name'],
        'last_name': candidate.get('last_name') or '',
        'email': candidate.get('email'),
        'phone': candidate.get('phone'),
        'street_address': candidate.get('street_address'),
        'city': candidate.get('city'),
        'state': candidate.get('state'),
        'zip_code': candidate.get('zip_code'),
        'notes': notes,
    }
    group_name = candidate.get('group_name')
    if group_name:
        args['group_names'] = [group_name]
    return {k: v for k, v in args.items() if v not in (None, '', [])}


def format_candidate_preview(candidates: list[dict]) -> str:
    """Human-readable preview of what B.O.B. read from the photo."""
    if not candidates:
        return "I didn't find usable contact info in that photo."

    if len(candidates) == 1:
        lines = ['Found this contact info:', '', _format_fields(candidates[0])]
        lines.extend([
            '',
            'Create this contact in your CRM?',
        ])
        return '\n'.join(lines)

    lines = [f'Found {len(candidates)} people in that photo:', '']
    for i, candidate in enumerate(candidates, start=1):
        name = _display_name(candidate)
        lines.append(f'**{i}. {name}**')
        lines.append(_format_fields(candidate, skip_name=True))
        lines.append('')
    lines.append('Create all of them in your CRM?')
    return '\n'.join(lines).rstrip()


def format_saved_contact(contact: Contact) -> str:
    """Everything B.O.B. persisted on the contact row."""
    groups = [
        g.name for g in (contact.groups or [])
        if getattr(g, 'is_active', True)
    ]
    fields = {
        'first_name': contact.first_name,
        'last_name': contact.last_name,
        'email': contact.email,
        'phone': contact.phone,
        'street_address': contact.street_address,
        'city': contact.city,
        'state': contact.state,
        'zip_code': contact.zip_code,
        'notes': contact.notes,
        'groups': groups,
    }
    name = _display_name(fields)
    body = _format_fields(fields, skip_name=True)
    return f'**{name}**\n{body}' if body else f'**{name}**'


def format_saved_contacts(contacts: list[Contact]) -> str:
    if not contacts:
        return 'No contacts were saved.'
    if len(contacts) == 1:
        return 'Saved to your CRM:\n\n' + format_saved_contact(contacts[0])
    chunks = ['Saved to your CRM:', '']
    for contact in contacts:
        chunks.append(format_saved_contact(contact))
        chunks.append('')
    return '\n'.join(chunks).rstrip()


def create_pending_photo_action(
    *,
    ctx: BobContext,
    candidates: list[dict],
    conversation_id: Optional[int] = None,
) -> BobAction:
    """Store extracted create args as a pending BobAction for Confirm/Cancel."""
    contacts_args = [candidate_to_create_args(c) for c in candidates]
    preview = {
        'source': PHOTO_SOURCE,
        'batch': True,
        'count': len(contacts_args),
        'contacts': candidates,
        'summary': (
            f'Create {len(contacts_args)} contact(s) from photo'
            if len(contacts_args) != 1
            else f"Create contact {_display_name(candidates[0])}"
        ),
    }
    # First contact args stay at top level so a generic confirm still works
    # for the single-contact case; batch confirm reads arguments['contacts'].
    arguments = {'contacts': contacts_args}
    if len(contacts_args) == 1:
        arguments.update(contacts_args[0])

    action = BobAction(
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
        conversation_id=conversation_id,
        tool_name='create_contact',
        arguments=arguments,
        preview=preview,
        status=BobAction.STATUS_PENDING,
        summary=preview['summary'][:300],
        surface=ctx.surface,
        expires_at=datetime.utcnow() + timedelta(minutes=CONFIRMATION_TTL_MINUTES),
    )
    db.session.add(action)
    db.session.commit()
    return action


def is_photo_pending_action(action: BobAction) -> bool:
    preview = action.preview or {}
    return preview.get('source') == PHOTO_SOURCE


def confirm_photo_contacts(action: BobAction, ctx: BobContext) -> tuple[ToolResult, list[Contact]]:
    """Execute a photo pending action (one or many creates)."""
    loaded = BobAction.query.filter_by(
        id=action.id,
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
    ).first()
    if loaded is None:
        return ToolResult.failure('That pending action was not found.'), []
    if loaded.status != BobAction.STATUS_PENDING:
        return ToolResult.failure(
            f'That action was already {loaded.status}.'
        ), []
    if loaded.is_expired:
        loaded.status = BobAction.STATUS_EXPIRED
        db.session.commit()
        return ToolResult.failure(
            'That confirmation expired. Send the photo again if you still want it.'
        ), []

    batch = (loaded.arguments or {}).get('contacts')
    if not isinstance(batch, list) or not batch:
        from services.bob_tools import confirm_action
        result = confirm_action(loaded.id, ctx)
        return result, _contacts_from_create_result(result)

    created: list[Contact] = []
    duplicates: list[str] = []
    errors: list[str] = []
    last_undoable_id: Optional[int] = None
    record_urls: list[str] = []

    for args in batch:
        if not isinstance(args, dict):
            continue
        result = bob_dispatch(
            'create_contact', args, ctx,
            conversation_id=loaded.conversation_id,
        )
        if result.ok and (result.data or {}).get('created'):
            created.extend(_contacts_from_create_result(result))
            if result.undoable and result.action_id:
                last_undoable_id = result.action_id
            if result.record_url:
                record_urls.append(result.record_url)
        elif result.ok and (result.data or {}).get('reason') == 'duplicate':
            existing = (result.data or {}).get('existing_contact') or {}
            duplicates.append(existing.get('name') or result.summary)
        else:
            errors.append(result.error or result.summary)

    loaded.status = BobAction.STATUS_EXECUTED
    loaded.executed_at = datetime.utcnow()
    db.session.commit()

    if not created and duplicates and not errors:
        return ToolResult.success(
            summary='Already in your CRM: ' + ', '.join(duplicates),
            data={'created': False, 'duplicates': duplicates},
        ), []

    if not created:
        return ToolResult.failure(
            errors[0] if errors else 'Could not create those contacts.'
        ), []

    summary = format_saved_contacts(created)
    if duplicates:
        summary += '\n\nAlready in CRM: ' + ', '.join(duplicates)
    return ToolResult.success(
        summary=summary,
        data={
            'created': True,
            'count': len(created),
            'contact_ids': [c.id for c in created],
        },
        undoable=last_undoable_id is not None,
        action_id=last_undoable_id,
        record_url=record_urls[0] if len(record_urls) == 1 else None,
    ), created


def create_contacts_now(
    *,
    ctx: BobContext,
    candidates: list[dict],
    conversation_id: Optional[int] = None,
) -> tuple[str, list[Contact], Optional[int], list[str]]:
    """Create immediately (create-intent path). Returns body, contacts, undo id, urls."""
    created: list[Contact] = []
    duplicates: list[str] = []
    errors: list[str] = []
    undoable_id: Optional[int] = None
    record_urls: list[str] = []

    for candidate in candidates:
        args = candidate_to_create_args(candidate)
        result = bob_dispatch(
            'create_contact', args, ctx, conversation_id=conversation_id,
        )
        if result.ok and (result.data or {}).get('created'):
            contacts = _contacts_from_create_result(result)
            created.extend(contacts)
            if result.undoable and result.action_id:
                undoable_id = result.action_id
            if result.record_url:
                record_urls.append(result.record_url)
        elif result.ok and (result.data or {}).get('reason') == 'duplicate':
            existing = (result.data or {}).get('existing_contact') or {}
            duplicates.append(existing.get('name') or result.summary)
        else:
            errors.append(result.error or result.summary)

    if created:
        body = format_saved_contacts(created)
        if duplicates:
            body += '\n\nAlready in CRM: ' + ', '.join(duplicates)
        return body, created, undoable_id, record_urls

    if duplicates and not errors:
        return (
            'Already in your CRM: ' + ', '.join(duplicates),
            [],
            None,
            [],
        )

    return (
        errors[0] if errors else 'Could not create a contact from that photo.',
        [],
        None,
        [],
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _normalize_candidate(entry: dict, *, user_email: str) -> Optional[dict]:
    confidence = (entry.get('confidence') or 'low').lower()
    first = _title_case_name(entry.get('first_name'))
    last = _title_case_name(entry.get('last_name'))
    email = _normalize_email(entry.get('email'))
    phone = _normalize_phone(entry.get('phone'))

    # create_contact requires first_name — promote last-only cards.
    if not first and last:
        first, last = last, ''

    has_name = bool(first)
    has_signal = bool(email or phone)
    if not has_name:
        return None
    if not has_signal and confidence == 'low':
        return None
    if email and user_email and email == user_email:
        return None

    return {
        'first_name': first,
        'last_name': last,
        'email': email,
        'phone': phone,
        'street_address': (entry.get('street_address') or '').strip() or None,
        'city': (entry.get('city') or '').strip() or None,
        'state': (entry.get('state') or '').strip() or None,
        'zip_code': (entry.get('zip_code') or '').strip() or None,
        'notes': (entry.get('notes') or '').strip() or None,
        'group_name': (entry.get('group_name') or '').strip() or None,
        'confidence': confidence,
    }


def _normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = str(raw).strip().lower()
    return cleaned or None


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    return format_phone_number(raw)


def _title_case_name(raw: str | None) -> str:
    if not raw:
        return ''
    return ' '.join(part.capitalize() for part in str(raw).strip().split())


def display_name(fields: dict) -> str:
    return (
        f"{fields.get('first_name') or ''} {fields.get('last_name') or ''}"
    ).strip() or 'Contact'


# Back-compat for internal callers.
_display_name = display_name


_FIELD_LABELS = (
    ('first_name', 'First name'),
    ('last_name', 'Last name'),
    ('email', 'Email'),
    ('phone', 'Phone'),
    ('street_address', 'Address'),
    ('city', 'City'),
    ('state', 'State'),
    ('zip_code', 'ZIP'),
    ('notes', 'Notes'),
    ('groups', 'Groups'),
    ('group_name', 'Group'),
)


def format_fields(fields: dict, *, skip_name: bool = False) -> str:
    lines = []
    for key, label in _FIELD_LABELS:
        if skip_name and key in ('first_name', 'last_name'):
            continue
        value = fields.get(key)
        if value in (None, '', [], ()):
            continue
        if isinstance(value, (list, tuple)):
            value = ', '.join(str(v) for v in value if v)
            if not value:
                continue
        # Keep notes readable but bounded for chat.
        text = str(value).strip()
        if key == 'notes' and len(text) > 400:
            text = text[:397].rstrip() + '...'
        lines.append(f'- **{label}:** {text}')
    return '\n'.join(lines)


_format_fields = format_fields


def _contacts_from_create_result(result: ToolResult) -> list[Contact]:
    data = result.data or {}
    contact_id = None
    if isinstance(data.get('contact'), dict):
        contact_id = data['contact'].get('contact_id')
    contact_id = contact_id or data.get('undo_target_id')
    if not contact_id:
        return []
    contact = db.session.get(Contact, int(contact_id))
    return [contact] if contact is not None else []
