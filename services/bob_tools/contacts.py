"""Contact tool handlers.

Reuses the dedupe rules, group resolution, and activation events the web UI
already applies, so a contact B.O.B. creates is indistinguishable from one the
agent typed in by hand.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, or_

from models import ActivationEvent, Contact, ContactGroup, Interaction, Task, db
from services.bob_tools.common import (
    MAX_CONTACT_NOTES,
    MAX_LIST_RESULTS,
    MAX_NOTE_FIELD,
    MAX_SEARCH_RESULTS,
    ToolError,
    contact_detail,
    contact_scope,
    contact_summary,
    get_contact_for_read,
    get_contact_for_write,
    resolve_contact_scope,
    task_summary,
    truncate,
)
from services.bob_tools.context import BobContext, ToolResult
from services.contact_group_service import (
    ContactGroupError,
    list_user_groups,
    resolve_group_by_fuzzy_name,
)
from utils import format_phone_number

logger = logging.getLogger(__name__)

# Fields update_contact is allowed to touch. Anything outside this set is
# rejected rather than silently ignored, so a hallucinated field name surfaces
# as a correctable error instead of a no-op the model reports as success.
EDITABLE_FIELDS = {
    'first_name', 'last_name', 'email', 'phone', 'street_address', 'city',
    'state', 'zip_code', 'notes', 'current_objective', 'move_timeline',
    'motivation', 'financial_status', 'additional_notes',
    'potential_commission',
}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

_DOWNGRADE_NOTE = (
    'Org-wide numbers were requested but this agent only has access to their '
    'own contacts, so these figures cover their contacts alone. Say so.'
)


def _apply_filters(query, args: dict):
    """Narrow a contact query by free text and structured location filters.

    Free text spans name, email, phone, and address. The structured filters are
    separate because "how many contacts in Houston" must not be answered by a
    substring that happened to match a street named Houston.
    """
    query_text = (args.get('query') or '').strip()
    if query_text:
        like = f'%{query_text.lower()}%'
        digits = ''.join(ch for ch in query_text if ch.isdigit())
        conditions = [
            func.lower(Contact.first_name).like(like),
            func.lower(Contact.last_name).like(like),
            func.lower(Contact.email).like(like),
            (func.lower(Contact.first_name) + ' ' + func.lower(Contact.last_name)).like(like),
            func.lower(Contact.city).like(like),
            func.lower(Contact.state).like(like),
            func.lower(Contact.zip_code).like(like),
            func.lower(Contact.street_address).like(like),
        ]
        if digits:
            conditions.append(Contact.phone.like(f'%{digits}%'))
        query = query.filter(or_(*conditions))

    # Exact, case-insensitive matches. City is the exception: agents say
    # "Houston" for a value stored as "Houston" or "North Houston".
    city = (args.get('city') or '').strip()
    if city:
        query = query.filter(func.lower(Contact.city).like(f'%{city.lower()}%'))

    state = (args.get('state') or '').strip()
    if state:
        query = query.filter(func.lower(Contact.state) == state.lower())

    zip_code = (args.get('zip_code') or '').strip()
    if zip_code:
        query = query.filter(func.lower(Contact.zip_code).like(f'{zip_code.lower()}%'))

    group_name = (args.get('group_name') or '').strip()
    if group_name:
        query = query.filter(
            Contact.groups.any(func.lower(ContactGroup.name).like(f'%{group_name.lower()}%'))
        )

    return query


def _filter_description(args: dict) -> str:
    parts = []
    for label, key in (
        ('matching "%s"', 'query'), ('in %s', 'city'), ('in %s', 'state'),
        ('in %s', 'zip_code'), ('in group %s', 'group_name'),
    ):
        value = (args.get(key) or '').strip() if args.get(key) else ''
        if value:
            parts.append(label % value)
    return ' '.join(parts)


def search_contacts(args: dict, ctx: BobContext) -> ToolResult:
    limit = _clamp(args.get('limit'), default=MAX_SEARCH_RESULTS, maximum=MAX_SEARCH_RESULTS)
    whole_org, downgraded = resolve_contact_scope(ctx, args)

    query = _apply_filters(contact_scope(ctx, whole_org=whole_org), args)

    # The true total, independent of the page size. Reporting len(rows) here is
    # how "how many contacts in Houston" ends up capped at the limit.
    total = query.count()
    rows = query.order_by(Contact.last_name, Contact.first_name).limit(limit).all()

    described = _filter_description(args)
    if total == 0:
        summary = f'No contacts {described}'.strip() if described else 'No contacts found'
    else:
        shown = f' (showing {len(rows)})' if total > len(rows) else ''
        summary = f'{total} contact(s) {described}{shown}'.strip()

    data = {
        'total_matching': total,
        'contacts': [contact_summary(c) for c in rows],
        'returned': len(rows),
        'more_available': total > len(rows),
        'scope': 'organization' if whole_org else 'mine',
    }
    if downgraded:
        data['scope_note'] = _DOWNGRADE_NOTE

    return ToolResult.success(summary=summary, data=data)


def count_contacts(args: dict, ctx: BobContext) -> ToolResult:
    """Aggregate counts, so "how many" never depends on a truncated list."""
    whole_org, downgraded = resolve_contact_scope(ctx, args)
    query = _apply_filters(contact_scope(ctx, whole_org=whole_org), args)
    total = query.count()

    scope_data = {'scope': 'organization' if whole_org else 'mine'}
    if downgraded:
        scope_data['scope_note'] = _DOWNGRADE_NOTE

    group_by = (args.get('group_by') or '').strip().lower()
    if not group_by:
        described = _filter_description(args)
        return ToolResult.success(
            summary=f'{total} contact(s) {described}'.strip(),
            data={'total': total, **scope_data},
        )

    columns = {
        'city': Contact.city,
        'state': Contact.state,
        # Collapse ZIP+4 so "77523-3525" counts under 77523 instead of showing
        # up as its own bucket in a breakdown.
        'zip_code': func.substr(Contact.zip_code, 1, 5),
    }
    if group_by not in columns and group_by != 'group':
        raise ToolError(
            'group_by must be one of: city, state, zip_code, group.'
        )

    ids = query.with_entities(Contact.id).subquery()

    if group_by == 'group':
        rows = (
            db.session.query(ContactGroup.name, func.count(func.distinct(Contact.id)))
            .join(Contact.groups)
            .filter(Contact.id.in_(db.select(ids.c.id)))
            .group_by(ContactGroup.name)
            .order_by(func.count(func.distinct(Contact.id)).desc())
            .limit(MAX_LIST_RESULTS)
            .all()
        )
    else:
        column = columns[group_by]
        rows = (
            db.session.query(column, func.count(Contact.id))
            .filter(Contact.id.in_(db.select(ids.c.id)))
            .group_by(column)
            .order_by(func.count(Contact.id).desc())
            .limit(MAX_LIST_RESULTS)
            .all()
        )

    breakdown = [
        {group_by: value or '(not set)', 'count': count}
        for value, count in rows
    ]
    top = ', '.join(f'{b[group_by]}: {b["count"]}' for b in breakdown[:5])

    return ToolResult.success(
        summary=f'{total} contact(s) across {len(breakdown)} {group_by} value(s). {top}'.strip(),
        data={
            'total': total, 'group_by': group_by, 'breakdown': breakdown,
            **scope_data,
        },
    )


def get_contact(args: dict, ctx: BobContext) -> ToolResult:
    contact = get_contact_for_read(ctx, args.get('contact_id'))

    tasks = (
        Task.query
        .filter_by(contact_id=contact.id, organization_id=ctx.organization_id)
        .order_by(Task.due_date.desc())
        .limit(5)
        .all()
    )
    interactions = (
        Interaction.query
        .filter_by(contact_id=contact.id, organization_id=ctx.organization_id)
        .order_by(Interaction.date.desc())
        .limit(5)
        .all()
    )

    data = contact_detail(contact, ctx)
    data['recent_tasks'] = [task_summary(t, ctx) for t in tasks]
    data['recent_interactions'] = [
        {
            'type': i.type,
            'date': i.date.date().isoformat() if i.date else None,
            'notes': truncate(i.notes),
        }
        for i in interactions
    ]

    return ToolResult.success(summary=f'Loaded {data["name"]}', data=data)


def list_contact_groups(args: dict, ctx: BobContext) -> ToolResult:
    # Deliberately uncached. The shared group cache holds live ORM instances for
    # five minutes, which go detached once the session that loaded them is gone.
    # A tool call is a cheap query and must not seed that cache for later
    # requests to trip over.
    groups = list_user_groups(
        ctx.organization_id, ctx.user_id, active_only=True, use_cache=False,
    )
    return ToolResult.success(
        summary=f'{len(groups)} group(s) available',
        data={
            'groups': [
                {'name': g.name, 'category': g.category} for g in groups
            ]
        },
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create_contact(args: dict, ctx: BobContext) -> ToolResult:
    first_name = _clean_name(args.get('first_name'))
    last_name = _clean_name(args.get('last_name'))
    if not first_name:
        raise ToolError('A first name is required to create a contact.')

    email = _clean_email(args.get('email'))
    phone = format_phone_number(args.get('phone')) if args.get('phone') else None
    if args.get('phone') and not phone:
        raise ToolError(
            f'{args.get("phone")!r} is not a usable 10-digit US phone number. '
            'Confirm the number with the agent.'
        )

    user = ctx.load_user()
    if user is None:
        raise ToolError('Could not load your account to create the contact.')

    allowed, reason = _org_can_add_contact(ctx)
    if not allowed:
        raise ToolError(reason)

    existing = _existing_contact(ctx, email=email, phone=phone,
                                first_name=first_name, last_name=last_name)
    if existing is not None:
        return ToolResult.success(
            summary=f'{existing.first_name} {existing.last_name} is already in the CRM',
            data={
                'created': False,
                'reason': 'duplicate',
                'existing_contact': contact_summary(existing),
                'message': (
                    'This person already exists. Use update_contact to change '
                    'their details, or create_task to add a follow-up.'
                ),
            },
        )

    contact = Contact(
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
        created_by_id=ctx.user_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        street_address=_clean_text(args.get('street_address')),
        city=_clean_text(args.get('city')),
        state=_clean_text(args.get('state')),
        zip_code=_clean_text(args.get('zip_code')),
        notes=truncate(args.get('notes'), MAX_NOTE_FIELD),
        current_objective=_clean_text(args.get('current_objective')),
        move_timeline=_clean_text(args.get('move_timeline')),
    )

    matched_groups, missing_groups = _resolve_groups(ctx, args.get('group_names'))
    if matched_groups:
        contact.groups = matched_groups

    try:
        db.session.add(contact)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. contact create failed org=%s user=%s',
                         ctx.organization_id, ctx.user_id)
        raise ToolError('The contact could not be saved. Nothing was changed.')

    _record_contact_created(ctx, user)

    name = f'{first_name} {last_name}'.strip()
    saved_groups = [g.name for g in contact.groups if g.is_active]
    result = ToolResult.success(
        summary=f'Created contact {name}',
        data={
            'created': True,
            'contact': contact_summary(contact),
            'saved': {
                'first_name': contact.first_name,
                'last_name': contact.last_name,
                'email': contact.email,
                'phone': contact.phone,
                'street_address': contact.street_address,
                'city': contact.city,
                'state': contact.state,
                'zip_code': contact.zip_code,
                'notes': contact.notes,
                'groups': saved_groups,
            },
            'groups_not_found': missing_groups,
        },
        undoable=True,
        record_url=f'/contact/{contact.id}',
    )
    result.data['undo_target_id'] = contact.id
    return result


def create_contact_group(args: dict, ctx: BobContext) -> ToolResult:
    from services.contact_group_service import create_group

    name = (args.get('name') or '').strip()
    category = (args.get('category') or '').strip() or 'Status'
    if not name:
        raise ToolError('A group name is required.')

    existing = resolve_group_by_fuzzy_name(
        ctx.organization_id, ctx.user_id, name, active_only=True,
    )
    if existing is not None:
        return ToolResult.success(
            summary=f'Group "{existing.name}" already exists',
            data={'created': False, 'group': {
                'name': existing.name, 'category': existing.category,
            }},
        )

    try:
        group = create_group(ctx.organization_id, ctx.user_id, name, category)
    except ContactGroupError as exc:
        raise ToolError(exc.message)

    result = ToolResult.success(
        summary=f'Created group "{group.name}"',
        data={'created': True, 'group': {
            'name': group.name, 'category': group.category,
        }},
        undoable=True,
    )
    result.data['undo_target_id'] = group.id
    return result


def undo_create_contact_group(action, ctx: BobContext) -> str:
    from services.contact_group_service import delete_group

    group_id = (action.result or {}).get('undo_target_id')
    try:
        delete_group(ctx.organization_id, ctx.user_id, group_id)
    except ContactGroupError as exc:
        raise ToolError(exc.message)
    return 'Removed the group'


def set_contact_groups(args: dict, ctx: BobContext) -> ToolResult:
    contact = get_contact_for_write(ctx, args.get('contact_id'))
    names = args.get('group_names') or []
    if not isinstance(names, list):
        raise ToolError('group_names must be a list of group names.')

    matched, missing = _resolve_groups(ctx, names)
    previous = [g.name for g in contact.groups if g.is_active]

    # Inactive memberships are kept: they are hidden from pickers, so the model
    # never saw them and must not be able to drop them.
    inactive = [g for g in contact.groups if not g.is_active]
    try:
        contact.groups = inactive + matched
        db.session.commit()
    except ContactGroupError as exc:
        db.session.rollback()
        raise ToolError(exc.message)
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. group assignment failed contact=%s', contact.id)
        raise ToolError('Groups could not be updated. Nothing was changed.')

    name = f'{contact.first_name} {contact.last_name}'.strip()
    return ToolResult.success(
        summary=f'Updated groups for {name}',
        data={
            'contact_id': contact.id,
            'groups': [g.name for g in matched],
            'previous_groups': previous,
            'groups_not_found': missing,
        },
        record_url=f'/contact/{contact.id}',
    )


# ---------------------------------------------------------------------------
# High-risk: previewed, then executed on confirmation
# ---------------------------------------------------------------------------

def preview_update_contact(args: dict, ctx: BobContext) -> dict:
    contact = get_contact_for_write(ctx, args.get('contact_id'))
    changes = _validated_changes(args.get('fields'))

    diff = []
    for field, new_value in changes.items():
        current = getattr(contact, field, None)
        if field == 'potential_commission' and current is not None:
            current = float(current)
        if str(current or '') == str(new_value or ''):
            continue
        diff.append({
            'field': field,
            'from': truncate(current) if current is not None else None,
            'to': truncate(new_value) if new_value is not None else None,
        })

    if not diff:
        raise ToolError('Those values already match what is on the contact.')

    return {
        'action': 'update_contact',
        'contact_id': contact.id,
        'contact_name': f'{contact.first_name} {contact.last_name}'.strip(),
        'changes': diff,
    }


def update_contact(args: dict, ctx: BobContext) -> ToolResult:
    contact = get_contact_for_write(ctx, args.get('contact_id'))
    changes = _validated_changes(args.get('fields'))

    before = {field: getattr(contact, field, None) for field in changes}
    for field, value in changes.items():
        setattr(contact, field, value)
    contact.update_last_contact_date()

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. contact update failed contact=%s', contact.id)
        raise ToolError('The contact could not be updated. Nothing was changed.')

    name = f'{contact.first_name} {contact.last_name}'.strip()
    result = ToolResult.success(
        summary=f'Updated {name}',
        data={
            'contact': contact_summary(contact),
            'updated_fields': sorted(changes.keys()),
        },
        undoable=True,
        record_url=f'/contact/{contact.id}',
    )
    result.data['undo_target_id'] = contact.id
    result.data['undo_payload'] = {
        field: (float(value) if field == 'potential_commission' and value is not None
                else value)
        for field, value in before.items()
    }
    return result


def preview_delete_contact(args: dict, ctx: BobContext) -> dict:
    contact = get_contact_for_write(ctx, args.get('contact_id'))
    task_count = Task.query.filter_by(
        contact_id=contact.id, organization_id=ctx.organization_id,
    ).count()
    interaction_count = Interaction.query.filter_by(
        contact_id=contact.id, organization_id=ctx.organization_id,
    ).count()

    return {
        'action': 'delete_contact',
        'contact_id': contact.id,
        'contact_name': f'{contact.first_name} {contact.last_name}'.strip(),
        'irreversible': True,
        'cascade': {
            'tasks_deleted': task_count,
            'interactions_deleted': interaction_count,
        },
        'warning': (
            f'Deleting this contact also deletes {task_count} task(s) and '
            f'{interaction_count} logged interaction(s). This cannot be undone.'
        ),
    }


def delete_contact(args: dict, ctx: BobContext) -> ToolResult:
    contact = get_contact_for_write(ctx, args.get('contact_id'))
    name = f'{contact.first_name} {contact.last_name}'.strip()

    try:
        Task.query.filter_by(
            contact_id=contact.id, organization_id=ctx.organization_id,
        ).delete(synchronize_session=False)
        Interaction.query.filter_by(
            contact_id=contact.id, organization_id=ctx.organization_id,
        ).delete(synchronize_session=False)
        # Clear the many-to-many rows explicitly. The bulk deletes above leave
        # the session out of sync, so the group collection may not be loaded at
        # flush time, which would strand rows in contact_groups.
        contact.groups = []
        db.session.flush()
        db.session.delete(contact)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. contact delete failed contact=%s', contact.id)
        raise ToolError('The contact could not be deleted. Nothing was changed.')

    return ToolResult.success(
        summary=f'Deleted {name}',
        data={'deleted': True, 'contact_name': name},
    )


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def append_contact_note(args: dict, ctx: BobContext) -> ToolResult:
    """Add a dated line to a contact's notes without touching what is there.

    Deliberately additive and therefore low risk, unlike update_contact's
    ``notes`` field which replaces the whole body and needs approval. Capturing
    "she wants a pool" right after a call is the most common thing an agent asks
    for, and it should not cost a confirmation click.
    """
    contact = get_contact_for_write(ctx, args.get('contact_id'))

    note = truncate(args.get('note'), MAX_NOTE_FIELD)
    if not note:
        raise ToolError('note cannot be blank.')

    stamp = ctx.today().strftime('%b %d, %Y')
    line = f'[{stamp}] {note}'
    previous = contact.notes or ''
    combined = f'{previous.rstrip()}\n{line}' if previous.strip() else line

    if len(combined) > MAX_CONTACT_NOTES:
        raise ToolError(
            'This contact\'s notes are full. Ask the agent to trim them on the '
            'contact page before adding more.'
        )

    try:
        contact.notes = combined
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. note append failed contact=%s', contact.id)
        raise ToolError('The note could not be saved. Nothing was changed.')

    name = f'{contact.first_name} {contact.last_name}'.strip()
    result = ToolResult.success(
        summary=f'Added a note to {name}',
        data={
            'contact_id': contact.id,
            'contact_name': name,
            'note_added': line,
        },
        undoable=True,
        record_url=f'/contact/{contact.id}',
    )
    # Undo restores the exact prior body rather than stripping the appended
    # line, so a concurrent edit cannot be silently clobbered by a regex.
    result.data['undo_target_id'] = contact.id
    result.data['undo_payload'] = {'previous_notes': previous, 'appended': line}
    return result


def undo_append_contact_note(action, ctx: BobContext) -> str:
    stored = action.result or {}
    contact = Contact.query.filter_by(
        id=stored.get('undo_target_id'),
        organization_id=ctx.organization_id,
    ).first()
    if contact is None:
        raise ToolError('That contact no longer exists.')

    payload = stored.get('undo_payload') or {}
    expected = payload.get('previous_notes') or ''
    appended = payload.get('appended') or ''
    current = contact.notes or ''
    rebuilt = f'{expected.rstrip()}\n{appended}' if expected.strip() else appended
    if current.strip() != rebuilt.strip():
        raise ToolError(
            'These notes changed since B.O.B. added that line, so it was left '
            'alone. Edit them on the contact page instead.'
        )

    contact.notes = expected or None
    db.session.commit()
    return 'Removed the note'


def undo_create_contact(action, ctx: BobContext) -> str:
    contact_id = (action.result or {}).get('undo_target_id')
    contact = contact_scope(ctx, whole_org=True).filter(Contact.id == contact_id).first()
    if contact is None:
        raise ToolError('That contact no longer exists.')

    has_tasks = Task.query.filter_by(
        contact_id=contact.id, organization_id=ctx.organization_id,
    ).count()
    if has_tasks:
        raise ToolError(
            'This contact now has tasks attached, so undo would delete real '
            'work. Delete it from the contact page if you still want it gone.'
        )

    name = f'{contact.first_name} {contact.last_name}'.strip()
    db.session.delete(contact)
    db.session.commit()
    return f'Removed {name}'


def undo_update_contact(action, ctx: BobContext) -> str:
    payload = (action.result or {}).get('undo_payload') or {}
    contact_id = (action.result or {}).get('undo_target_id')
    contact = contact_scope(ctx, whole_org=True).filter(Contact.id == contact_id).first()
    if contact is None:
        raise ToolError('That contact no longer exists.')

    for field, value in payload.items():
        if field in EDITABLE_FIELDS:
            setattr(contact, field, value)
    contact.update_last_contact_date()
    db.session.commit()
    name = f'{contact.first_name} {contact.last_name}'.strip()
    return f'Reverted changes to {name}'


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _validated_changes(fields) -> dict:
    if not isinstance(fields, dict) or not fields:
        raise ToolError(
            'fields must be an object of contact fields to change, for example '
            '{"phone": "8325551234"}.'
        )

    unknown = sorted(set(fields) - EDITABLE_FIELDS)
    if unknown:
        raise ToolError(
            f'These fields cannot be updated: {", ".join(unknown)}. '
            f'Editable fields are: {", ".join(sorted(EDITABLE_FIELDS))}.'
        )

    changes = {}
    for field, raw in fields.items():
        if field in ('first_name', 'last_name'):
            value = _clean_name(raw)
            if not value:
                raise ToolError(f'{field} cannot be blank.')
        elif field == 'email':
            value = _clean_email(raw)
        elif field == 'phone':
            value = format_phone_number(raw) if raw else None
            if raw and not value:
                raise ToolError(f'{raw!r} is not a usable 10-digit US phone number.')
        elif field == 'potential_commission':
            value = _clean_money(raw)
        elif field in ('notes', 'additional_notes'):
            value = truncate(raw, MAX_NOTE_FIELD)
        else:
            value = _clean_text(raw)
        changes[field] = value
    return changes


def _existing_contact(ctx: BobContext, *, email, phone, first_name, last_name):
    """Same dedupe ladder the CSV import and Magic Inbox use."""
    base = Contact.query.filter(
        Contact.organization_id == ctx.organization_id,
        Contact.user_id == ctx.user_id,
    )
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
            .filter(func.lower(Contact.first_name) == first_name.lower())
            .filter(func.lower(Contact.last_name) == (last_name or '').lower())
            .first()
        )
        if dup:
            return dup
    return None


def _org_can_add_contact(ctx: BobContext) -> tuple[bool, str]:
    """Contact cap check that works without a request context."""
    from models import Organization

    org = Organization.query.get(ctx.organization_id)
    if org is None or org.max_contacts is None:
        return True, ''
    current = Contact.query.filter_by(organization_id=ctx.organization_id).count()
    if current >= org.max_contacts:
        return False, (
            f'Contact limit reached ({org.max_contacts}). '
            'Upgrade to Pro for unlimited contacts.'
        )
    return True, ''


def _resolve_groups(ctx: BobContext, names):
    if not names:
        return [], []
    if isinstance(names, str):
        names = [names]

    matched, missing = [], []
    for name in names:
        group = resolve_group_by_fuzzy_name(
            ctx.organization_id, ctx.user_id, name, active_only=True,
        )
        if group is None:
            missing.append(str(name))
        elif group not in matched:
            matched.append(group)
    return matched, missing


def _record_contact_created(ctx: BobContext, user) -> None:
    from services.activation_service import record_event, record_meaningful_action

    record_event(
        ActivationEvent.CONTACT_CREATED,
        user=user,
        data={'source': ctx.surface},
        surface=ctx.surface,
    )
    record_meaningful_action(
        user, action='contact_created', surface=ctx.surface,
        data={'source': ctx.surface},
    )


def _clean_name(value) -> str:
    if value is None:
        return ''
    return ' '.join(str(value).strip().split())[:80]


def _clean_text(value, limit: int = 200):
    if value is None:
        return None
    text = ' '.join(str(value).strip().split())
    return text[:limit] or None


def _clean_email(value):
    if not value:
        return None
    cleaned = str(value).strip().lower()
    if not cleaned:
        return None
    if '@' not in cleaned or ' ' in cleaned:
        raise ToolError(f'{value!r} is not a valid email address.')
    return cleaned[:120]


def _clean_money(value):
    if value is None or value == '':
        return None
    try:
        return round(float(str(value).replace(',', '').replace('$', '')), 2)
    except (TypeError, ValueError):
        raise ToolError(f'{value!r} is not a valid dollar amount.')


def _clamp(value, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))
