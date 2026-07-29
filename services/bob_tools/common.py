"""Shared scoping, parsing, and serialization for B.O.B. tool handlers.

Every record lookup in the tool layer goes through this module so tenant
scoping and ownership checks exist in exactly one place.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

from models import Contact, Task, TaskSubtype, TaskType
from services.bob_tools.context import BobContext

# Payload ceilings. The model pays for every character a tool returns, and a
# runaway note field is also the easiest way to smuggle prompt injection.
MAX_SEARCH_RESULTS = 10
MAX_LIST_RESULTS = 25
MAX_TEXT_FIELD = 400
MAX_NOTE_FIELD = 600

# Notes accumulate across many appends, so they get their own ceiling. The
# column is unbounded Text; this keeps a long history from dominating the
# model's context window on every get_contact.
MAX_CONTACT_NOTES = 8000

FOLLOW_UP_SUBTYPE_NAMES = ('Follow-up', 'Follow Up')

INTERACTION_TYPES = ('call', 'email', 'text', 'meeting', 'other')
TASK_PRIORITIES = ('low', 'medium', 'high')
TASK_STATUSES = ('pending', 'completed', 'cancelled')


class ToolError(Exception):
    """A handler failure with a message safe to show the agent and the model."""


def truncate(value, limit: int = MAX_TEXT_FIELD) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + '...'


# ---------------------------------------------------------------------------
# Scoped lookups
# ---------------------------------------------------------------------------

def contact_scope(ctx: BobContext, whole_org: bool = False):
    """Contacts this context may read.

    Defaults to the contacts the agent owns, which is what "my contacts" means
    and what the web UI shows until you flip to the org-wide view. Org admins
    may opt into the whole organization with ``whole_org``; for everyone else it
    is ignored, since they have no org-wide visibility to grant.
    """
    query = Contact.query.filter_by(organization_id=ctx.organization_id)
    if not (whole_org and ctx.is_org_admin):
        query = query.filter_by(user_id=ctx.user_id)
    return query


def resolve_contact_scope(ctx: BobContext, args: dict) -> tuple[bool, bool]:
    """Interpret a ``scope`` argument.

    Returns (whole_org, downgraded) where ``downgraded`` means org-wide was
    asked for but the agent lacks the role, so the caller can say so rather than
    silently reporting a narrower number.
    """
    requested = (args.get('scope') or 'mine').strip().lower()
    if requested not in ('mine', 'organization'):
        raise ToolError("scope must be either 'mine' or 'organization'.")
    if requested == 'organization':
        return (True, False) if ctx.is_org_admin else (False, True)
    return False, False


def task_scope(ctx: BobContext):
    """Tasks this context may read."""
    query = Task.query.filter_by(organization_id=ctx.organization_id)
    if not ctx.is_org_admin:
        query = query.filter_by(assigned_to_id=ctx.user_id)
    return query


def get_contact_for_read(ctx: BobContext, contact_id) -> Contact:
    # Looking a specific id up keeps full admin reach: an owner asking about a
    # teammate's contact by id should get it, same as the web UI.
    contact = contact_scope(ctx, whole_org=True).filter(
        Contact.id == _as_int(contact_id, 'contact_id')
    ).first()
    if contact is None:
        raise ToolError(
            f'No contact with id {contact_id} is available to you. '
            'Use search_contacts to find the right contact_id.'
        )
    return contact


def get_contact_for_write(ctx: BobContext, contact_id) -> Contact:
    contact = get_contact_for_read(ctx, contact_id)
    if contact.user_id != ctx.user_id and not ctx.is_org_admin:
        raise ToolError('That contact belongs to another agent, so it cannot be changed here.')
    return contact


def get_task_for_read(ctx: BobContext, task_id) -> Task:
    task = task_scope(ctx).filter(Task.id == _as_int(task_id, 'task_id')).first()
    if task is None:
        raise ToolError(
            f'No task with id {task_id} is available to you. '
            'Use list_tasks or get_agenda to find the right task_id.'
        )
    return task


def get_task_for_write(ctx: BobContext, task_id) -> Task:
    task = get_task_for_read(ctx, task_id)
    owns = task.assigned_to_id == ctx.user_id or task.created_by_id == ctx.user_id
    if not owns and not ctx.is_org_admin:
        raise ToolError('That task is assigned to another agent, so it cannot be changed here.')
    return task


def _as_int(value, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(f'{field_name} must be a number, got {value!r}.')


# ---------------------------------------------------------------------------
# Task type resolution
# ---------------------------------------------------------------------------

def resolve_task_type(ctx: BobContext, type_name: str | None,
                      subtype_name: str | None) -> tuple[TaskType, TaskSubtype]:
    """Map human type/subtype names to this org's rows.

    Falls back to the first available type rather than failing, because a
    missing category should never block an agent from capturing a follow-up.
    """
    types = (
        TaskType.query
        .filter_by(organization_id=ctx.organization_id)
        .order_by(TaskType.sort_order, TaskType.id)
        .all()
    )
    if not types:
        raise ToolError(
            'This organization has no task types configured yet, so tasks '
            'cannot be created. An admin needs to set them up first.'
        )

    task_type = _match_by_name(types, type_name)

    subtypes = (
        TaskSubtype.query
        .filter_by(organization_id=ctx.organization_id, task_type_id=task_type.id)
        .order_by(TaskSubtype.sort_order, TaskSubtype.id)
        .all()
    )
    if not subtypes:
        raise ToolError(
            f'The "{task_type.name}" task type has no subtypes configured, '
            'so a task cannot be created under it.'
        )

    subtype = _match_by_name(subtypes, subtype_name)

    # A follow-up is the activation-defining action, so honour it across types
    # rather than silently downgrading to the type's first subtype.
    if subtype_name and _normalize(subtype_name) in {_normalize(n) for n in FOLLOW_UP_SUBTYPE_NAMES}:
        follow_up = next(
            (s for s in subtypes
             if _normalize(s.name) in {_normalize(n) for n in FOLLOW_UP_SUBTYPE_NAMES}),
            None,
        )
        if follow_up is not None:
            subtype = follow_up

    return task_type, subtype


def _normalize(value: str | None) -> str:
    return ''.join(ch for ch in (value or '').lower() if ch.isalnum())


def _match_by_name(rows, requested: str | None):
    if requested:
        key = _normalize(requested)
        exact = next((r for r in rows if _normalize(r.name) == key), None)
        if exact is not None:
            return exact
        partial = next((r for r in rows if key and key in _normalize(r.name)), None)
        if partial is not None:
            return partial
    return rows[0]


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def parse_local_date(value: str | None, field_name: str = 'date') -> date:
    if not value:
        raise ToolError(f'{field_name} is required (format YYYY-MM-DD).')
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except ValueError:
        raise ToolError(
            f'{field_name} must be formatted YYYY-MM-DD, got {value!r}.'
        )


def parse_local_time(value: str | None) -> time | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ('%H:%M', '%I:%M %p', '%I%p', '%I %p'):
        try:
            return datetime.strptime(raw.upper(), fmt).time()
        except ValueError:
            continue
    raise ToolError(f'Could not read a time from {value!r}. Use 24-hour HH:MM.')


def due_datetime_utc(ctx: BobContext, due_date: date,
                     scheduled: time | None) -> datetime:
    """Convert an agent-local due date into the UTC value the column expects.

    Mirrors ``routes/tasks.py`` create/edit: an unscheduled task lands at end of
    the local day, and everything is stored tz-aware in UTC.
    """
    naive = datetime.combine(due_date, scheduled or time(23, 59, 59))
    localized = ctx.tzinfo.localize(naive)
    return localized.astimezone(timezone.utc)


def to_local_date_string(value, ctx: BobContext) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(ctx.tzinfo).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def contact_summary(contact: Contact) -> dict:
    """Compact contact shape for search results and lists."""
    return {
        'contact_id': contact.id,
        'name': f'{contact.first_name} {contact.last_name}'.strip(),
        'email': contact.email,
        'phone': contact.phone,
        'groups': [g.name for g in contact.groups if g.is_active],
        'last_contact_date': (
            contact.last_contact_date.isoformat()
            if contact.last_contact_date else None
        ),
    }


def contact_detail(contact: Contact, ctx: BobContext) -> dict:
    data = contact_summary(contact)
    data.update({
        'street_address': contact.street_address,
        'city': contact.city,
        'state': contact.state,
        'zip_code': contact.zip_code,
        'notes': truncate(contact.notes, MAX_NOTE_FIELD),
        'current_objective': truncate(contact.current_objective),
        'move_timeline': truncate(contact.move_timeline),
        'motivation': truncate(contact.motivation),
        'financial_status': truncate(contact.financial_status),
        'additional_notes': truncate(contact.additional_notes),
        'potential_commission': (
            float(contact.potential_commission)
            if contact.potential_commission is not None else None
        ),
        'owned_by_you': contact.user_id == ctx.user_id,
        'created_at': to_local_date_string(contact.created_at, ctx),
    })
    return data


def task_summary(task: Task, ctx: BobContext) -> dict:
    contact = task.contact
    return {
        'task_id': task.id,
        'subject': task.subject,
        'status': task.status,
        'priority': task.priority,
        'due_date': to_local_date_string(task.due_date, ctx),
        'type': task.task_type.name if task.task_type else None,
        'subtype': task.task_subtype.name if task.task_subtype else None,
        'contact_id': task.contact_id,
        'contact_name': (
            f'{contact.first_name} {contact.last_name}'.strip()
            if contact else None
        ),
        'description': truncate(task.description),
        'assigned_to_you': task.assigned_to_id == ctx.user_id,
    }


def due_bucket(task: Task, ctx: BobContext) -> str:
    """Classify a task against the agent's local today."""
    due = to_local_date_string(task.due_date, ctx)
    if due is None:
        return 'undated'
    today = ctx.today().isoformat()
    if due < today:
        return 'overdue'
    if due == today:
        return 'today'
    return 'upcoming'
