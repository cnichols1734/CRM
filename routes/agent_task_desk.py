"""Agent iPhone Tasks API. Attaches to the agent_api blueprint.

Scope is always assigned-to-me. Owners and admins are not given an implicit
org-wide list — AgentDesk is a personal CRM.
"""
from __future__ import annotations

from datetime import datetime, time, timezone

from flask import jsonify, request
from sqlalchemy.orm import joinedload

from models import Contact, Task, TaskSubtype, TaskType, db
from routes.agent_api import (  # noqa: F401
    _contact_visible,
    _json_body,
    _json_error,
    _parse_date,
    _parse_datetime,
    agent_api_bp,
    agent_jwt_required,
)
from services.bob_tools.common import (
    TASK_PRIORITIES,
    TASK_STATUSES,
    ToolError,
    due_bucket,
    due_datetime_utc,
    parse_local_date,
    parse_local_time,
    task_scope,
    to_local_date_string,
)
from services.bob_tools.context import BobContext
from services.bob_tools.tasks import (
    _clean,
    _record_task_completed,
    _record_task_created,
    complete_task,
    delete_task,
)
from services.tenant_service import org_query_for_id
from services.transaction_auth import CAP_VIEW, get_transaction_for_user

_registered = False

TASK_BUCKETS = ('today', 'overdue', 'upcoming', 'all')


def register(bp):
    """Idempotent. Attach task routes to agent_api blueprint."""
    global _registered
    if _registered:
        return
    _registered = True
    bp.add_url_rule(
        '/task-types',
        endpoint='agent_task_types',
        view_func=api_list_task_types,
        methods=['GET'],
    )
    bp.add_url_rule(
        '/tasks',
        endpoint='agent_tasks_list',
        view_func=api_list_tasks,
        methods=['GET'],
    )
    bp.add_url_rule(
        '/tasks',
        endpoint='agent_tasks_create',
        view_func=api_create_task,
        methods=['POST'],
    )
    bp.add_url_rule(
        '/tasks/<int:task_id>',
        endpoint='agent_task_get',
        view_func=api_get_task,
        methods=['GET'],
    )
    bp.add_url_rule(
        '/tasks/<int:task_id>',
        endpoint='agent_task_patch',
        view_func=api_patch_task,
        methods=['PATCH'],
    )
    bp.add_url_rule(
        '/tasks/<int:task_id>',
        endpoint='agent_task_delete',
        view_func=api_delete_task,
        methods=['DELETE'],
    )
    bp.add_url_rule(
        '/tasks/<int:task_id>/complete',
        endpoint='agent_task_complete',
        view_func=api_complete_task,
        methods=['POST'],
    )


def _ctx(user) -> BobContext:
    return BobContext.from_user(user, surface='agent_api')


def _mine(ctx: BobContext):
    """Assigned-to-me only. Admins do not get a silent org-wide list."""
    return task_scope(ctx).filter(Task.assigned_to_id == ctx.user_id).options(
        joinedload(Task.contact),
        joinedload(Task.task_type),
        joinedload(Task.task_subtype),
        joinedload(Task.transaction),
    )


def _get_mine(ctx: BobContext, task_id: int):
    return _mine(ctx).filter(Task.id == task_id).first()


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.isoformat()
    return str(value)


def _scheduled_hhmm(value, ctx: BobContext):
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(ctx.tzinfo).strftime('%H:%M')
    if isinstance(value, time):
        return value.strftime('%H:%M')
    return None


def serialize_task(task: Task, ctx: BobContext) -> dict:
    contact = task.contact
    return {
        'id': task.id,
        'subject': task.subject,
        'description': task.description or None,
        'status': task.status,
        'priority': task.priority,
        'due_date': to_local_date_string(task.due_date, ctx),
        'scheduled_time': _scheduled_hhmm(task.scheduled_time, ctx),
        'type_id': task.type_id,
        'subtype_id': task.subtype_id,
        'type': task.task_type.name if task.task_type else None,
        'subtype': task.task_subtype.name if task.task_subtype else None,
        'contact_id': task.contact_id,
        'contact_name': (
            f'{contact.first_name} {contact.last_name}'.strip()
            if contact else None
        ),
        'transaction_id': task.transaction_id,
        'property_address': task.property_address or None,
        'outcome': task.outcome or None,
        'completed_at': _iso(task.completed_at),
    }


def _as_int(value, field_name):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f'{field_name} must be a number.') from exc


def _load_contact(user, contact_id):
    contact = org_query_for_id(Contact, user.organization_id).filter_by(
        id=contact_id,
    ).first()
    if not _contact_visible(user, contact):
        return None
    return contact


def _load_transaction(user, transaction_id):
    tx, decision = get_transaction_for_user(
        transaction_id, user=user, capability=CAP_VIEW,
    )
    if tx is None:
        return None, (
            404 if getattr(decision, 'reason', None) == 'not_found' else 403
        )
    return tx, None


def _resolve_type_pair(org_id, type_id, subtype_id):
    types = (
        TaskType.query
        .filter_by(organization_id=org_id)
        .order_by(TaskType.sort_order, TaskType.id)
        .all()
    )
    if not types:
        raise ToolError(
            'This organization has no task types configured yet, so tasks '
            'cannot be created.'
        )

    task_type = None
    if type_id is not None:
        task_type = next((row for row in types if row.id == type_id), None)
        if task_type is None:
            raise ToolError('Task type not found.')
    elif subtype_id is not None:
        subtype = TaskSubtype.query.filter_by(
            id=subtype_id, organization_id=org_id,
        ).first()
        if subtype is None:
            raise ToolError('Task subtype not found.')
        task_type = next(
            (row for row in types if row.id == subtype.task_type_id), None,
        )
        if task_type is None:
            raise ToolError('Task type not found.')
        return task_type, subtype
    else:
        task_type = types[0]

    subtypes = (
        TaskSubtype.query
        .filter_by(organization_id=org_id, task_type_id=task_type.id)
        .order_by(TaskSubtype.sort_order, TaskSubtype.id)
        .all()
    )
    if not subtypes:
        raise ToolError(
            f'The "{task_type.name}" task type has no subtypes configured.'
        )
    if subtype_id is not None:
        subtype = next((row for row in subtypes if row.id == subtype_id), None)
        if subtype is None:
            raise ToolError('Task subtype not found.')
        return task_type, subtype
    return task_type, subtypes[0]


def _page_args():
    page = request.args.get('page', 1, type=int) or 1
    page = max(page, 1)
    per_page = request.args.get('per_page', 50, type=int) or 50
    per_page = min(max(per_page, 1), 100)
    return page, per_page


@agent_jwt_required
def api_list_task_types(user):
    types = (
        TaskType.query
        .filter_by(organization_id=user.organization_id)
        .order_by(TaskType.sort_order, TaskType.id)
        .all()
    )
    return jsonify({
        'task_types': [
            {
                'id': task_type.id,
                'name': task_type.name,
                'subtypes': [
                    {'id': subtype.id, 'name': subtype.name}
                    for subtype in sorted(
                        task_type.subtypes,
                        key=lambda row: (row.sort_order or 0, row.id),
                    )
                ],
            }
            for task_type in types
        ],
    })


@agent_jwt_required
def api_list_tasks(user):
    ctx = _ctx(user)
    status = (request.args.get('status') or 'pending').strip().lower()
    if status not in TASK_STATUSES + ('all',):
        return _json_error(
            'status must be pending, completed, cancelled, or all.', 400,
        )
    bucket = (request.args.get('bucket') or 'all').strip().lower()
    if bucket not in TASK_BUCKETS:
        return _json_error(
            'bucket must be today, overdue, upcoming, or all.', 400,
        )

    query = _mine(ctx)
    if status != 'all':
        query = query.filter(Task.status == status)
    contact_id = request.args.get('contact_id', type=int)
    if contact_id:
        query = query.filter(Task.contact_id == contact_id)
    transaction_id = request.args.get('transaction_id', type=int)
    if transaction_id:
        query = query.filter(Task.transaction_id == transaction_id)

    rows = query.order_by(Task.due_date.asc(), Task.id.asc()).all()
    if bucket != 'all':
        rows = [task for task in rows if due_bucket(task, ctx) == bucket]

    page, per_page = _page_args()
    total = len(rows)
    start = (page - 1) * per_page
    items = rows[start:start + per_page]
    return jsonify({
        'tasks': [serialize_task(task, ctx) for task in items],
        'page': page,
        'per_page': per_page,
        'total': total,
    })


@agent_jwt_required
def api_get_task(user, task_id):
    ctx = _ctx(user)
    task = _get_mine(ctx, task_id)
    if task is None:
        return _json_error('Task not found.', 404)
    return jsonify({'task': serialize_task(task, ctx)})


@agent_jwt_required
def api_create_task(user):
    data = _json_body()
    ctx = _ctx(user)
    try:
        contact_id = _as_int(data.get('contact_id'), 'contact_id')
        transaction_id = _as_int(data.get('transaction_id'), 'transaction_id')
        if contact_id is None and transaction_id is None:
            return _json_error(
                'A contact or a transaction is required.', 400,
            )

        contact = None
        transaction = None
        if contact_id is not None:
            contact = _load_contact(user, contact_id)
            if contact is None:
                return _json_error('Contact not found.', 404)
        if transaction_id is not None:
            transaction, status = _load_transaction(user, transaction_id)
            if transaction is None:
                return _json_error('Transaction not found.', status)

        subject = (data.get('subject') or '').strip()
        if not subject:
            return _json_error('A subject is required.', 400)
        subject = subject[:200]

        due_date = parse_local_date(data.get('due_date'), 'due_date')
        scheduled = parse_local_time(data.get('scheduled_time'))
        utc_due = due_datetime_utc(ctx, due_date, scheduled)

        priority = (data.get('priority') or 'medium').strip().lower()
        if priority not in TASK_PRIORITIES:
            return _json_error(
                f'priority must be one of: {", ".join(TASK_PRIORITIES)}.',
                400,
            )

        type_id = _as_int(data.get('type_id'), 'type_id')
        subtype_id = _as_int(data.get('subtype_id'), 'subtype_id')
        task_type, subtype = _resolve_type_pair(
            user.organization_id, type_id, subtype_id,
        )

        property_address = _clean(data.get('property_address'))
        if not property_address and transaction is not None:
            property_address = _clean(getattr(transaction, 'street_address', None))

        description = (data.get('description') or '').strip() or None

        task = Task(
            organization_id=user.organization_id,
            contact_id=contact.id if contact else None,
            transaction_id=transaction.id if transaction else None,
            assigned_to_id=user.id,
            created_by_id=user.id,
            type_id=task_type.id,
            subtype_id=subtype.id,
            subject=subject,
            description=description,
            priority=priority,
            due_date=utc_due,
            scheduled_time=utc_due if scheduled else None,
            property_address=property_address,
        )
        db.session.add(task)
        db.session.commit()
    except ToolError as exc:
        db.session.rollback()
        return _json_error(str(exc), 400)
    except Exception:
        db.session.rollback()
        return _json_error('The task could not be saved.', 500)

    _record_task_created(ctx, user, task)
    task = _get_mine(ctx, task.id)
    return jsonify({'task': serialize_task(task, ctx)}), 201


@agent_jwt_required
def api_patch_task(user, task_id):
    ctx = _ctx(user)
    task = _get_mine(ctx, task_id)
    if task is None:
        return _json_error('Task not found.', 404)

    data = _json_body()
    was_completed = task.status == 'completed'
    try:
        if 'subject' in data:
            subject = (data.get('subject') or '').strip()
            if not subject:
                raise ToolError('subject cannot be blank.')
            task.subject = subject[:200]
        if 'description' in data:
            value = data.get('description')
            task.description = (value or '').strip() or None
        if 'priority' in data:
            priority = (data.get('priority') or '').strip().lower()
            if priority not in TASK_PRIORITIES:
                raise ToolError(
                    f'priority must be one of: {", ".join(TASK_PRIORITIES)}.'
                )
            task.priority = priority
        if 'status' in data:
            status = (data.get('status') or '').strip().lower()
            if status not in TASK_STATUSES:
                raise ToolError(
                    f'status must be one of: {", ".join(TASK_STATUSES)}.'
                )
            task.status = status
        if 'property_address' in data:
            task.property_address = _clean(data.get('property_address'))

        if 'type_id' in data or 'subtype_id' in data:
            type_id = (
                _as_int(data.get('type_id'), 'type_id')
                if 'type_id' in data else task.type_id
            )
            subtype_id = (
                _as_int(data.get('subtype_id'), 'subtype_id')
                if 'subtype_id' in data else task.subtype_id
            )
            task_type, subtype = _resolve_type_pair(
                user.organization_id, type_id, subtype_id,
            )
            task.type_id = task_type.id
            task.subtype_id = subtype.id

        if 'contact_id' in data:
            raw = data.get('contact_id')
            if raw in (None, ''):
                task.contact_id = None
            else:
                contact = _load_contact(user, _as_int(raw, 'contact_id'))
                if contact is None:
                    db.session.rollback()
                    return _json_error('Contact not found.', 404)
                task.contact_id = contact.id
        if 'transaction_id' in data:
            raw = data.get('transaction_id')
            if raw in (None, ''):
                task.transaction_id = None
            else:
                transaction, tx_status = _load_transaction(
                    user, _as_int(raw, 'transaction_id'),
                )
                if transaction is None:
                    db.session.rollback()
                    return _json_error('Transaction not found.', tx_status)
                task.transaction_id = transaction.id

        if task.contact_id is None and task.transaction_id is None:
            raise ToolError('A contact or a transaction is required.')

        if 'due_date' in data or 'scheduled_time' in data:
            if 'due_date' in data:
                due_date = parse_local_date(data.get('due_date'), 'due_date')
            else:
                due_date = parse_local_date(
                    to_local_date_string(task.due_date, ctx), 'due_date',
                )
            if 'scheduled_time' in data:
                scheduled = parse_local_time(data.get('scheduled_time'))
            elif task.scheduled_time is not None:
                hhmm = _scheduled_hhmm(task.scheduled_time, ctx)
                scheduled = parse_local_time(hhmm)
            else:
                scheduled = None
            utc_due = due_datetime_utc(ctx, due_date, scheduled)
            task.due_date = utc_due
            task.scheduled_time = utc_due if scheduled else None

        if task.status == 'completed' and not was_completed:
            task.completed_at = datetime.now(timezone.utc)
        elif task.status != 'completed':
            task.completed_at = None

        db.session.commit()
    except ToolError as exc:
        db.session.rollback()
        return _json_error(str(exc), 400)
    except Exception:
        db.session.rollback()
        return _json_error('The task could not be updated.', 500)

    if task.status == 'completed' and not was_completed:
        _record_task_completed(ctx, user, task)
    task = _get_mine(ctx, task.id)
    return jsonify({'task': serialize_task(task, ctx)})


@agent_jwt_required
def api_complete_task(user, task_id):
    ctx = _ctx(user)
    task = _get_mine(ctx, task_id)
    if task is None:
        return _json_error('Task not found.', 404)

    data = _json_body()
    try:
        complete_task(
            {'task_id': task.id, 'outcome': data.get('outcome')},
            ctx,
        )
    except ToolError as exc:
        return _json_error(str(exc), 400)

    task = _get_mine(ctx, task_id)
    return jsonify({'task': serialize_task(task, ctx)})


@agent_jwt_required
def api_delete_task(user, task_id):
    ctx = _ctx(user)
    task = _get_mine(ctx, task_id)
    if task is None:
        return _json_error('Task not found.', 404)
    try:
        delete_task({'task_id': task.id}, ctx)
    except ToolError as exc:
        return _json_error(str(exc), 400)
    return jsonify({'ok': True})


register(agent_api_bp)
