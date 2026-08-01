"""Shared contact import for Contacts page and B.O.B. chat attachments.

Deterministic column aliases first; optional AI header mapping only when the
required name columns cannot be resolved. Preview and execute share the same
normalize/dedupe/capacity rules.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import func

from models import Contact, Organization, db
from services.bob_attachments import (
    KIND_CSV,
    KIND_XLS,
    KIND_XLSX,
    MAX_SPREADSHEET_ROWS,
    MAX_UPLOAD_BYTES,
    AttachmentParseError,
    classify_kind,
    parse_attachment,
)
from services.contact_group_service import resolve_groups_by_name
from utils import format_phone_number

logger = logging.getLogger(__name__)

CANONICAL_FIELDS = (
    'first_name', 'last_name', 'email', 'phone', 'street_address',
    'city', 'state', 'zip_code', 'notes', 'groups',
)

COLUMN_ALIASES = {
    'first_name': {
        'first_name', 'firstname', 'first', 'first name', 'given name',
    },
    'last_name': {
        'last_name', 'lastname', 'last', 'last name', 'surname', 'family name',
    },
    'email': {
        'email', 'email 1', 'email address', 'e-mail', 'emailaddress',
    },
    'phone': {
        'phone', 'phone number', 'phone number 1', 'mobile', 'cell',
        'telephone', 'phone1',
    },
    'street_address': {
        'street_address', 'street', 'address', 'mailing address',
        'street address', 'address1',
    },
    'city': {'city', 'mailing city', 'town'},
    'state': {
        'state', 'mailing state/province', 'mailing state', 'province',
        'st',
    },
    'zip_code': {
        'zip_code', 'zip', 'postal', 'mailing postal code', 'postal code',
        'zipcode',
    },
    'notes': {'notes', 'note', 'comments', 'comment'},
    'groups': {'groups', 'group', 'tags', 'tag', 'lists', 'list'},
    'full_name': {'name', 'full name', 'contact name', 'contact'},
}


@dataclass
class MappedRow:
    row_number: int
    values: dict[str, Any]
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportPreview:
    total_rows: int = 0
    create_count: int = 0
    duplicate_count: int = 0
    invalid_count: int = 0
    invalid_phone_count: int = 0
    missing_name_count: int = 0
    capacity_remaining: int | None = None
    capacity_ok: bool = True
    sample: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)
    rows_to_create: list[dict[str, Any]] = field(default_factory=list)
    column_mapping: dict[str, str] = field(default_factory=dict)

    def to_preview_dict(self) -> dict[str, Any]:
        return {
            'kind': 'contact_import',
            'summary': (
                f'Import {self.create_count} contact(s)'
                if self.create_count != 1
                else 'Import 1 contact'
            ),
            'total_rows': self.total_rows,
            'create_count': self.create_count,
            'duplicate_count': self.duplicate_count,
            'invalid_count': self.invalid_count,
            'invalid_phone_count': self.invalid_phone_count,
            'missing_name_count': self.missing_name_count,
            'capacity_remaining': self.capacity_remaining,
            'capacity_ok': self.capacity_ok,
            'sample': self.sample[:5],
            'warnings': self.warnings,
            'error_details': self.error_details[:20],
            'column_mapping': self.column_mapping,
        }


@dataclass
class ImportResult:
    created: list[Contact] = field(default_factory=list)
    skipped_duplicates: int = 0
    invalid_count: int = 0
    invalid_phone_count: int = 0
    missing_name_count: int = 0
    error_details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ContactImportError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _norm_header(value: str) -> str:
    return ' '.join(str(value or '').strip().lower().replace('_', ' ').split())


def map_contact_columns(headers: list[str]) -> dict[str, str]:
    """Map source headers -> canonical fields using deterministic aliases."""
    mapping: dict[str, str] = {}
    used_targets: set[str] = set()
    for header in headers:
        key = _norm_header(header)
        for target, aliases in COLUMN_ALIASES.items():
            if target in used_targets:
                continue
            if key == target.replace('_', ' ') or key in aliases:
                mapping[header] = target
                used_targets.add(target)
                break
    return mapping


def _needs_ai_header_mapping(mapping: dict[str, str]) -> bool:
    targets = set(mapping.values())
    return 'first_name' not in targets and 'full_name' not in targets


def map_contact_columns_with_ai(headers: list[str]) -> dict[str, str]:
    """Fallback structured header mapper when aliases fail."""
    mapping = map_contact_columns(headers)
    if not _needs_ai_header_mapping(mapping):
        return mapping

    try:
        import json
        import openai
        from config import Config

        if not Config.OPENAI_API_KEY:
            return mapping

        client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model='gpt-4.1-mini',
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'Map spreadsheet headers to CRM contact fields. '
                        'Return JSON {"mapping": {"Header": "field"}}. '
                        f'Allowed fields: {", ".join(CANONICAL_FIELDS + ("full_name",))}. '
                        'Omit headers that are not contact fields.'
                    ),
                },
                {
                    'role': 'user',
                    'content': 'Headers: ' + ', '.join(headers[:50]),
                },
            ],
            response_format={'type': 'json_object'},
            temperature=0,
        )
        raw = response.choices[0].message.content or '{}'
        parsed = json.loads(raw)
        ai_map = parsed.get('mapping') or {}
        allowed = set(CANONICAL_FIELDS) | {'full_name'}
        for header, target in ai_map.items():
            if header in headers and target in allowed and target not in mapping.values():
                mapping[header] = target
    except Exception:
        logger.warning('AI header mapping failed', exc_info=True)
    return mapping


def parse_contact_rows(
    data: bytes,
    filename: str,
    mime: str = '',
    *,
    max_rows: int | None = None,
    use_ai_headers: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse a tabular file into normalized contact candidate dicts.

    Returns ``(rows, meta)`` where each row already uses canonical field names
    and ``meta`` carries mapping/truncation warnings.
    """
    kind = classify_kind(filename, mime)
    if kind not in {KIND_CSV, KIND_XLSX, KIND_XLS}:
        raise ContactImportError(
            'Contact import supports CSV and Excel (.xlsx/.xls) files.'
        )

    # Contacts page (max_rows=None) allows larger files; B.O.B. passes 500.
    row_cap = 20_000 if max_rows is None else max_rows
    upload_cap = 50 * 1024 * 1024 if max_rows is None else MAX_UPLOAD_BYTES

    try:
        parsed = parse_attachment(
            data,
            filename=filename,
            mime=mime,
            max_rows=row_cap,
            max_upload_bytes=upload_cap,
        )
    except AttachmentParseError as exc:
        raise ContactImportError(exc.message) from exc

    if not parsed.is_tabular:
        raise ContactImportError('That file is not a spreadsheet.')

    rows = parsed.rows
    headers = parsed.headers
    warnings = list(parsed.warnings)

    mapping = (
        map_contact_columns_with_ai(headers)
        if use_ai_headers else map_contact_columns(headers)
    )
    if _needs_ai_header_mapping(mapping):
        raise ContactImportError(
            'Could not find name columns. Include First Name / Last Name, '
            'or a full Name column.'
        )

    normalized = []
    for idx, row in enumerate(rows, start=1):
        values: dict[str, Any] = {field: None for field in CANONICAL_FIELDS}
        extras = []
        full_name = ''
        for header, value in row.items():
            target = mapping.get(header)
            text = '' if value is None else str(value).strip()
            if not target:
                if text:
                    extras.append(f'{header}: {text}')
                continue
            if target == 'full_name':
                full_name = text
            else:
                values[target] = text or None
        if full_name and not values.get('first_name') and not values.get('last_name'):
            parts = full_name.split(maxsplit=1)
            values['first_name'] = parts[0]
            values['last_name'] = parts[1] if len(parts) > 1 else ''
        if extras:
            existing_notes = values.get('notes') or ''
            joined = '; '.join(extras)
            values['notes'] = (
                f'{existing_notes}; {joined}'.strip('; ').strip()
                if existing_notes else joined
            )
        if values.get('groups'):
            values['groups'] = str(values['groups']).replace(',', ';')
        values['_row_number'] = idx
        normalized.append(values)

    meta = {
        'headers': headers,
        'column_mapping': mapping,
        'warnings': warnings,
        'total_rows': len(normalized),
    }
    return normalized, meta


def _normalize_phone(raw: str | None) -> tuple[str | None, bool]:
    if not raw:
        return None, False
    digits = ''.join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) != 10:
        return None, True
    return format_phone_number(digits), False


def _existing_contact(owner_user_id: int, *, email, phone, first_name, last_name):
    base = Contact.query.filter(Contact.user_id == owner_user_id)
    if email:
        dup = base.filter(func.lower(Contact.email) == email).first()
        if dup:
            return dup
    if phone:
        dup = base.filter(Contact.phone == phone).first()
        if dup:
            return dup
    if not email and not phone and (first_name or last_name):
        dup = (
            base
            .filter(func.lower(Contact.first_name) == (first_name or '').lower())
            .filter(func.lower(Contact.last_name) == (last_name or '').lower())
            .first()
        )
        if dup:
            return dup
    return None


def _capacity_remaining(org_id: int) -> tuple[bool, int | None, str]:
    org = Organization.query.get(org_id)
    if org is None or org.max_contacts is None:
        return True, None, ''
    current = Contact.query.filter_by(organization_id=org_id).count()
    remaining = max(0, org.max_contacts - current)
    if remaining <= 0:
        return False, 0, (
            f'Contact limit reached ({org.max_contacts}). '
            'Upgrade to Pro for unlimited contacts.'
        )
    return True, remaining, ''


def preview_contact_import(
    rows: list[dict[str, Any]],
    *,
    actor_user_id: int,
    owner_user_id: int,
    org_id: int,
    warnings: list[str] | None = None,
    column_mapping: dict[str, str] | None = None,
) -> ImportPreview:
    preview = ImportPreview(
        total_rows=len(rows),
        warnings=list(warnings or []),
        column_mapping=dict(column_mapping or {}),
    )
    seen_keys: set[str] = set()
    capacity_ok, remaining, capacity_reason = _capacity_remaining(org_id)
    preview.capacity_remaining = remaining
    preview.capacity_ok = capacity_ok

    for row in rows:
        row_num = int(row.get('_row_number') or 0)
        first = (row.get('first_name') or '').strip()
        last = (row.get('last_name') or '').strip()
        if not first and not last:
            preview.invalid_count += 1
            preview.missing_name_count += 1
            preview.error_details.append(
                f'Row {row_num}: Missing both first and last name'
            )
            continue

        phone, invalid_phone = _normalize_phone(row.get('phone'))
        if invalid_phone:
            preview.invalid_phone_count += 1
        email = (row.get('email') or '').strip().lower() or None

        # Within-file dedupe.
        key = email or phone or f'name:{(first or "").lower()}|{(last or "").lower()}'
        if key in seen_keys:
            preview.duplicate_count += 1
            continue
        seen_keys.add(key)

        existing = _existing_contact(
            owner_user_id,
            email=email,
            phone=phone,
            first_name=first,
            last_name=last,
        )
        if existing is not None:
            preview.duplicate_count += 1
            continue

        candidate = {
            'first_name': first or '',
            'last_name': last or '',
            'email': (row.get('email') or '').strip() or None,
            'phone': phone,
            'street_address': (row.get('street_address') or '').strip() or None,
            'city': (row.get('city') or '').strip() or None,
            'state': (row.get('state') or '').strip() or None,
            'zip_code': (row.get('zip_code') or '').strip() or None,
            'notes': (row.get('notes') or '').strip() or None,
            'groups': (row.get('groups') or '').strip() or None,
            '_row_number': row_num,
        }
        preview.rows_to_create.append(candidate)
        if len(preview.sample) < 5:
            preview.sample.append({
                'name': f"{candidate['first_name']} {candidate['last_name']}".strip(),
                'email': candidate['email'],
                'phone': candidate['phone'],
            })

    preview.create_count = len(preview.rows_to_create)
    if remaining is not None and preview.create_count > remaining:
        preview.capacity_ok = False
        preview.warnings.append(
            capacity_reason or (
                f'Only {remaining} contact slot(s) remain; this import needs '
                f'{preview.create_count}.'
            )
        )
    elif not capacity_ok:
        preview.warnings.append(capacity_reason)
    return preview


def execute_contact_import(
    rows: list[dict[str, Any]],
    *,
    actor_user_id: int,
    owner_user_id: int,
    org_id: int,
    source: str = 'csv_import',
) -> ImportResult:
    """Create contacts in one transaction from already-normalized rows."""
    preview = preview_contact_import(
        rows,
        actor_user_id=actor_user_id,
        owner_user_id=owner_user_id,
        org_id=org_id,
    )
    result = ImportResult(
        skipped_duplicates=preview.duplicate_count,
        invalid_count=preview.invalid_count,
        invalid_phone_count=preview.invalid_phone_count,
        missing_name_count=preview.missing_name_count,
        error_details=list(preview.error_details),
        warnings=list(preview.warnings),
    )

    if not preview.capacity_ok:
        raise ContactImportError(
            preview.warnings[0] if preview.warnings else 'Contact limit reached.'
        )
    if not preview.rows_to_create:
        return result

    # Re-check capacity against the exact create set.
    capacity_ok, remaining, reason = _capacity_remaining(org_id)
    if not capacity_ok or (remaining is not None and len(preview.rows_to_create) > remaining):
        raise ContactImportError(reason or 'Contact limit reached.')

    created: list[Contact] = []
    try:
        for candidate in preview.rows_to_create:
            # Re-dedupe at write time in case the CRM changed since preview.
            existing = _existing_contact(
                owner_user_id,
                email=(candidate.get('email') or '').lower() or None,
                phone=candidate.get('phone'),
                first_name=candidate.get('first_name') or '',
                last_name=candidate.get('last_name') or '',
            )
            if existing is not None:
                result.skipped_duplicates += 1
                continue

            contact = Contact(
                organization_id=org_id,
                user_id=owner_user_id,
                created_by_id=actor_user_id,
                first_name=candidate.get('first_name') or '',
                last_name=candidate.get('last_name') or '',
                email=candidate.get('email'),
                phone=candidate.get('phone'),
                street_address=candidate.get('street_address'),
                city=candidate.get('city'),
                state=candidate.get('state'),
                zip_code=candidate.get('zip_code'),
                notes=candidate.get('notes'),
            )
            if candidate.get('groups'):
                group_names = [
                    name.strip()
                    for name in str(candidate['groups']).split(';')
                    if name.strip()
                ]
                if group_names:
                    groups, missing = resolve_groups_by_name(
                        org_id, owner_user_id, group_names, active_only=True,
                    )
                    if missing:
                        result.error_details.append(
                            f"Row {candidate.get('_row_number')}: "
                            f"Some groups not found: {', '.join(missing)}"
                        )
                    contact.groups = groups
            db.session.add(contact)
            created.append(contact)

        if not created:
            db.session.rollback()
            return result

        db.session.commit()
        result.created = created
    except Exception:
        db.session.rollback()
        logger.exception('Contact import failed source=%s', source)
        raise ContactImportError('Database error while importing contacts.')

    return result


def rows_from_attachment_bytes(
    data: bytes,
    *,
    filename: str,
    mime: str,
    bob_limits: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Convenience wrapper used by B.O.B. import_contacts."""
    return parse_contact_rows(
        data,
        filename,
        mime,
        max_rows=MAX_SPREADSHEET_ROWS if bob_limits else None,
        use_ai_headers=True,
    )
