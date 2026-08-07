"""Task tool handlers.

Due dates follow the ``routes/tasks.py`` convention exactly: parsed in the
agent's local timezone, stored tz-aware in UTC, end-of-day when no time is
given. Activation events mirror the manual create and complete paths so AI
work counts toward activation the same way.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from models import ActivationEvent, Task, TaskType, db
from services.bob_tools.common import (
    MAX_LIST_RESULTS,
    TASK_PRIORITIES,
    TASK_STATUSES,
    ToolError,
    due_bucket,
    due_datetime_utc,
    get_contact_for_read,
    get_task_for_read,
    get_task_for_write,
    parse_local_date,
    parse_local_time,
    resolve_task_type,
    task_scope,
    task_summary,
    to_local_date_string,
    truncate,
)
from services.bob_tools.context import BobContext, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def list_task_types(args: dict, ctx: BobContext) -> ToolResult:
    types = (
        TaskType.query
        .filter_by(organization_id=ctx.organization_id)
        .order_by(TaskType.sort_order, TaskType.id)
        .all()
    )
    return ToolResult.success(
        summary=f'{len(types)} task type(s) available',
        data={
            'task_types': [
                {
                    'type': t.name,
                    'subtypes': [s.name for s in sorted(
                        t.subtypes, key=lambda s: (s.sort_order or 0, s.id),
                    )],
                }
                for t in types
            ]
        },
    )


def list_tasks(args: dict, ctx: BobContext) -> ToolResult:
    status = (args.get('status') or 'pending').strip().lower()
    if status not in TASK_STATUSES + ('all',):
        raise ToolError(
            f'status must be one of: {", ".join(TASK_STATUSES)}, all.'
        )

    query = task_scope(ctx)
    if status != 'all':
        query = query.filter(Task.status == status)

    if args.get('contact_id'):
        contact = get_contact_for_read(ctx, args['contact_id'])
        query = query.filter(Task.contact_id == contact.id)

    if args.get('transaction_id'):
        transaction = _authorize_transaction(ctx, args['transaction_id'], for_write=False)
        query = query.filter(Task.transaction_id == transaction.id)

    if args.get('due_after'):
        after = parse_local_date(args['due_after'], 'due_after')
        query = query.filter(Task.due_date >= due_datetime_utc(ctx, after, None))
    if args.get('due_before'):
        before = parse_local_date(args['due_before'], 'due_before')
        query = query.filter(Task.due_date <= due_datetime_utc(ctx, before, None))

    limit = _clamp(args.get('limit'), default=MAX_LIST_RESULTS, maximum=MAX_LIST_RESULTS)
    rows = query.order_by(Task.due_date.asc()).limit(limit + 1).all()
    truncated = len(rows) > limit
    rows = rows[:limit]

    return ToolResult.success(
        summary=f'{len(rows)} task(s) found',
        data={
            'tasks': [task_summary(t, ctx) for t in rows],
            'count': len(rows),
            'more_available': truncated,
        },
    )


def get_agenda(args: dict, ctx: BobContext) -> ToolResult:
    """Overdue, due today, and the next few days, in one call.

    This is the question agents ask most, and doing it as one tool keeps it a
    single round trip instead of three list_tasks calls.
    """
    horizon_days = _clamp(args.get('days_ahead'), default=7, maximum=30)
    today = ctx.today()
    upper = due_datetime_utc(ctx, today + timedelta(days=horizon_days), None)

    rows = (
        task_scope(ctx)
        .filter(Task.status == 'pending')
        .filter(Task.due_date <= upper)
        .order_by(Task.due_date.asc())
        .limit(MAX_LIST_RESULTS * 2)
        .all()
    )

    buckets: dict[str, list] = {'overdue': [], 'today': [], 'upcoming': []}
    for task in rows:
        buckets.setdefault(due_bucket(task, ctx), []).append(
            task_summary(task, ctx)
        )

    counts = {name: len(items) for name, items in buckets.items()}
    summary_bits = [
        f'{counts.get("overdue", 0)} overdue',
        f'{counts.get("today", 0)} due today',
        f'{counts.get("upcoming", 0)} upcoming',
    ]

    return ToolResult.success(
        summary=', '.join(summary_bits),
        data={
            'today': today.isoformat(),
            'timezone': ctx.timezone,
            'overdue': buckets.get('overdue', []),
            'due_today': buckets.get('today', []),
            'upcoming': buckets.get('upcoming', []),
            'counts': counts,
        },
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create_task(args: dict, ctx: BobContext) -> ToolResult:
    contact_id = args.get('contact_id')
    transaction_id = args.get('transaction_id') or ctx.active_transaction_id

    contact = None
    transaction = None
    if contact_id is not None:
        contact = get_contact_for_read(ctx, contact_id)
    if transaction_id is not None:
        transaction = _authorize_transaction(ctx, transaction_id, for_write=True)

    if contact is None and transaction is None:
        raise ToolError(
            'Provide contact_id and/or transaction_id. Transaction-only tasks '
            'may omit contact_id.'
        )

    subject = (args.get('subject') or '').strip()
    if not subject:
        raise ToolError('A subject is required, for example "Follow up on Conroe listings".')
    subject = subject[:200]

    due_date = parse_local_date(args.get('due_date'), 'due_date')
    scheduled = parse_local_time(args.get('scheduled_time'))
    utc_due = due_datetime_utc(ctx, due_date, scheduled)

    priority = (args.get('priority') or 'medium').strip().lower()
    if priority not in TASK_PRIORITIES:
        raise ToolError(f'priority must be one of: {", ".join(TASK_PRIORITIES)}.')

    task_type, subtype = resolve_task_type(ctx, args.get('type'), args.get('subtype'))

    user = ctx.load_user()
    if user is None:
        raise ToolError('Could not load your account to create the task.')

    property_address = _clean(args.get('property_address'))
    if not property_address and transaction is not None:
        property_address = _clean(getattr(transaction, 'street_address', None))

    task = Task(
        organization_id=ctx.organization_id,
        contact_id=contact.id if contact else None,
        transaction_id=transaction.id if transaction else None,
        assigned_to_id=ctx.user_id,
        created_by_id=ctx.user_id,
        type_id=task_type.id,
        subtype_id=subtype.id,
        subject=subject,
        description=truncate(args.get('description'), 1000),
        priority=priority,
        due_date=utc_due,
        scheduled_time=utc_due if scheduled else None,
        property_address=property_address,
    )

    try:
        db.session.add(task)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. task create failed org=%s user=%s',
                         ctx.organization_id, ctx.user_id)
        raise ToolError('The task could not be saved. Nothing was changed.')

    _record_task_created(ctx, user, task)

    if contact is not None:
        label = f'{contact.first_name} {contact.last_name}'.strip()
        record_url = f'/contact/{contact.id}'
    else:
        label = getattr(transaction, 'street_address', None) or f'transaction {transaction.id}'
        record_url = f'/transactions/{transaction.id}'

    result = ToolResult.success(
        summary=f'{subtype.name} for {label} on {due_date.isoformat()}',
        data={
            'created': True,
            'task': task_summary(task, ctx),
        },
        undoable=True,
        record_url=record_url,
    )
    result.data['undo_target_id'] = task.id
    return result


def complete_task(args: dict, ctx: BobContext) -> ToolResult:
    task = get_task_for_write(ctx, args.get('task_id'))
    if task.status == 'completed':
        return ToolResult.success(
            summary=f'"{task.subject}" was already done',
            data={'task': task_summary(task, ctx), 'already_completed': True},
        )

    user = ctx.load_user()
    if user is None:
        raise ToolError('Could not load your account to complete the task.')

    outcome = truncate(args.get('outcome'), 1000)
    try:
        task.status = 'completed'
        task.completed_at = datetime.now(timezone.utc)
        if outcome:
            task.outcome = outcome
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. task complete failed task=%s', task.id)
        raise ToolError('The task could not be completed. Nothing was changed.')

    _record_task_completed(ctx, user, task)

    result = ToolResult.success(
        summary=f'Completed "{task.subject}"',
        data={'task': task_summary(task, ctx)},
        undoable=True,
        record_url=f'/contact/{task.contact_id}' if task.contact_id else None,
    )
    result.data['undo_target_id'] = task.id
    return result


# ---------------------------------------------------------------------------
# High-risk: previewed, then executed on confirmation
# ---------------------------------------------------------------------------

EDITABLE_TASK_FIELDS = {
    'subject', 'description', 'priority', 'due_date', 'scheduled_time',
    'status', 'property_address',
}


def preview_update_task(args: dict, ctx: BobContext) -> dict:
    task = get_task_for_write(ctx, args.get('task_id'))
    changes = _validated_task_changes(args.get('fields'), ctx)

    diff = []
    for field, value in changes.items():
        if field == 'due_date':
            current = to_local_date_string(task.due_date, ctx)
            new = value.isoformat()
        else:
            current = getattr(task, field, None)
            new = value
        if str(current or '') == str(new or ''):
            continue
        diff.append({'field': field, 'from': truncate(current), 'to': truncate(new)})

    if not diff:
        raise ToolError('Those values already match what is on the task.')

    return {
        'action': 'update_task',
        'task_id': task.id,
        'subject': task.subject,
        'changes': diff,
    }


def update_task(args: dict, ctx: BobContext) -> ToolResult:
    task = get_task_for_write(ctx, args.get('task_id'))
    changes = _validated_task_changes(args.get('fields'), ctx)
    user = ctx.load_user()

    before = {
        'subject': task.subject,
        'description': task.description,
        'priority': task.priority,
        'status': task.status,
        'property_address': task.property_address,
        'due_date': to_local_date_string(task.due_date, ctx),
    }
    was_completed = task.status == 'completed'

    try:
        if 'due_date' in changes:
            scheduled = changes.get('scheduled_time')
            task.due_date = due_datetime_utc(ctx, changes['due_date'], scheduled)
            task.scheduled_time = task.due_date if scheduled else None
        for field in ('subject', 'description', 'priority', 'status',
                      'property_address'):
            if field in changes:
                setattr(task, field, changes[field])

        if task.status == 'completed' and not was_completed:
            task.completed_at = datetime.now(timezone.utc)
        elif task.status != 'completed':
            task.completed_at = None

        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. task update failed task=%s', task.id)
        raise ToolError('The task could not be updated. Nothing was changed.')

    if task.status == 'completed' and not was_completed and user is not None:
        _record_task_completed(ctx, user, task)

    result = ToolResult.success(
        summary=f'Updated "{task.subject}"',
        data={
            'task': task_summary(task, ctx),
            'updated_fields': sorted(changes.keys()),
        },
        undoable=True,
        record_url=f'/contact/{task.contact_id}' if task.contact_id else None,
    )
    result.data['undo_target_id'] = task.id
    result.data['undo_payload'] = before
    return result


def preview_delete_task(args: dict, ctx: BobContext) -> dict:
    task = get_task_for_write(ctx, args.get('task_id'))
    contact = task.contact
    return {
        'action': 'delete_task',
        'task_id': task.id,
        'subject': task.subject,
        'due_date': to_local_date_string(task.due_date, ctx),
        'contact_name': (
            f'{contact.first_name} {contact.last_name}'.strip() if contact else None
        ),
        'irreversible': True,
        'warning': 'Deleting a task cannot be undone. Completing it keeps the history.',
    }


def delete_task(args: dict, ctx: BobContext) -> ToolResult:
    task = get_task_for_write(ctx, args.get('task_id'))
    subject = task.subject

    try:
        db.session.delete(task)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. task delete failed task=%s', task.id)
        raise ToolError('The task could not be deleted. Nothing was changed.')

    return ToolResult.success(
        summary=f'Deleted "{subject}"',
        data={'deleted': True, 'subject': subject},
    )


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def undo_create_task(action, ctx: BobContext) -> str:
    task_id = (action.result or {}).get('undo_target_id')
    task = task_scope(ctx).filter(Task.id == task_id).first()
    if task is None:
        raise ToolError('That task no longer exists.')
    subject = task.subject
    db.session.delete(task)
    db.session.commit()
    return f'Removed "{subject}"'


def undo_complete_task(action, ctx: BobContext) -> str:
    task_id = (action.result or {}).get('undo_target_id')
    task = task_scope(ctx).filter(Task.id == task_id).first()
    if task is None:
        raise ToolError('That task no longer exists.')
    task.status = 'pending'
    task.completed_at = None
    db.session.commit()
    return f'Reopened "{task.subject}"'


def undo_update_task(action, ctx: BobContext) -> str:
    payload = (action.result or {}).get('undo_payload') or {}
    task_id = (action.result or {}).get('undo_target_id')
    task = task_scope(ctx).filter(Task.id == task_id).first()
    if task is None:
        raise ToolError('That task no longer exists.')

    for field in ('subject', 'description', 'priority', 'status',
                  'property_address'):
        if field in payload:
            setattr(task, field, payload[field])
    if payload.get('due_date'):
        task.due_date = due_datetime_utc(
            ctx, parse_local_date(payload['due_date'], 'due_date'), None,
        )
    task.completed_at = None if task.status != 'completed' else task.completed_at
    db.session.commit()
    return f'Reverted changes to "{task.subject}"'


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _authorize_transaction(ctx: BobContext, transaction_id, *, for_write: bool):
    from services.transaction_auth import (
        CAP_EDIT,
        CAP_VIEW,
        get_transaction_for_user,
    )

    class _UserStub:
        id = ctx.user_id
        organization_id = ctx.organization_id
        org_role = ctx.org_role
        is_authenticated = True

    capability = CAP_EDIT if for_write else CAP_VIEW
    try:
        tid = int(transaction_id)
    except (TypeError, ValueError):
        raise ToolError(f'transaction_id must be a number, got {transaction_id!r}.')

    transaction, decision = get_transaction_for_user(tid, _UserStub(), capability)
    if transaction is None:
        if decision.reason == 'not_found':
            raise ToolError(f'No transaction with id {tid} is available to you.')
        raise ToolError('You are not authorized for that transaction.')
    return transaction


def _validated_task_changes(fields, ctx: BobContext) -> dict:
    if not isinstance(fields, dict) or not fields:
        raise ToolError(
            'fields must be an object of task fields to change, for example '
            '{"due_date": "2026-08-06"}.'
        )

    unknown = sorted(set(fields) - EDITABLE_TASK_FIELDS)
    if unknown:
        raise ToolError(
            f'These fields cannot be updated: {", ".join(unknown)}. '
            f'Editable fields are: {", ".join(sorted(EDITABLE_TASK_FIELDS))}.'
        )

    changes = {}
    for field, raw in fields.items():
        if field == 'due_date':
            changes[field] = parse_local_date(raw, 'due_date')
        elif field == 'scheduled_time':
            changes[field] = parse_local_time(raw)
        elif field == 'priority':
            value = (raw or '').strip().lower()
            if value not in TASK_PRIORITIES:
                raise ToolError(f'priority must be one of: {", ".join(TASK_PRIORITIES)}.')
            changes[field] = value
        elif field == 'status':
            value = (raw or '').strip().lower()
            if value not in TASK_STATUSES:
                raise ToolError(f'status must be one of: {", ".join(TASK_STATUSES)}.')
            changes[field] = value
        elif field == 'subject':
            value = (raw or '').strip()
            if not value:
                raise ToolError('subject cannot be blank.')
            changes[field] = value[:200]
        elif field == 'description':
            changes[field] = truncate(raw, 1000)
        else:
            changes[field] = _clean(raw)

    # A scheduled time only means anything alongside a date.
    if 'scheduled_time' in changes and 'due_date' not in changes:
        raise ToolError('Include due_date when setting scheduled_time.')
    return changes


def _record_task_created(ctx: BobContext, user, task) -> None:
    from services.activation_service import (
        is_follow_up_task, is_user_activated, record_event,
        record_meaningful_action,
    )

    record_event(
        ActivationEvent.TASK_CREATED,
        user=user,
        data={'source': ctx.surface, 'has_contact': bool(task.contact_id)},
        surface=ctx.surface,
    )
    if not is_follow_up_task(task):
        return

    record_event(
        ActivationEvent.FOLLOW_UP_CREATED,
        user=user,
        data={'source': ctx.surface, 'activation_task_id': task.id},
        once=True,
        surface=ctx.surface,
    )
    record_meaningful_action(
        user, action='follow_up_created', surface=ctx.surface,
        data={'source': ctx.surface},
    )
    if is_user_activated(user):
        record_event(
            ActivationEvent.ACTIVATION_COMPLETED,
            user=user,
            data={'source': ctx.surface, 'activation_task_id': task.id},
            once=True,
            surface=ctx.surface,
        )


def _record_task_completed(ctx: BobContext, user, task) -> None:
    from services.activation_service import (
        is_follow_up_task, record_event, record_meaningful_action,
    )

    is_follow_up = is_follow_up_task(task)
    record_event(
        ActivationEvent.TASK_COMPLETED,
        user=user,
        data={'source': ctx.surface, 'is_follow_up': is_follow_up},
        surface=ctx.surface,
    )
    record_meaningful_action(
        user, action='task_completed', surface=ctx.surface,
        data={'is_follow_up': is_follow_up},
    )


def _clean(value, limit: int = 200):
    if value is None:
        return None
    text = ' '.join(str(value).strip().split())
    return text[:limit] or None


def _clamp(value, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))
