"""Personal todo-list tool handlers.

``UserTodo`` is the agent's private scratch list, separate from CRM ``Task``
rows: no contact, no due date, no calendar sync. It exists for "pick up the
lockbox" items that do not belong on a client's timeline.

The web page saves by replacing the whole list, so these handlers work on
individual rows and always re-normalize ``order`` afterwards to keep the two
surfaces consistent.
"""
from __future__ import annotations

import logging

from models import UserTodo, db
from services.bob_tools.common import MAX_LIST_RESULTS, ToolError, truncate
from services.bob_tools.context import BobContext, ToolResult

logger = logging.getLogger(__name__)

# The column is String(500); leave room rather than letting the DB truncate.
MAX_TODO_TEXT = 500

# A scratch list past this point is a task list in denial.
MAX_ACTIVE_TODOS = 100


def _scope(ctx: BobContext):
    """Todos are private to one user, so there is no org-admin widening here."""
    return UserTodo.query.filter_by(
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
    )


def _summarize(todo: UserTodo) -> dict:
    return {
        'todo_id': todo.id,
        'text': todo.text,
        'completed': bool(todo.completed),
    }


def _renumber(ctx: BobContext) -> None:
    """Rewrite ``order`` so the list page renders in a stable sequence."""
    for position, row in enumerate(
        _scope(ctx).order_by(UserTodo.completed, UserTodo.order, UserTodo.id).all()
    ):
        row.order = position


def list_todos(args: dict, ctx: BobContext) -> ToolResult:
    include_completed = bool(args.get('include_completed'))

    query = _scope(ctx)
    if not include_completed:
        query = query.filter_by(completed=False)

    rows = query.order_by(
        UserTodo.completed, UserTodo.order, UserTodo.id,
    ).limit(MAX_LIST_RESULTS).all()

    active = [_summarize(r) for r in rows if not r.completed]
    done = [_summarize(r) for r in rows if r.completed]

    if not rows:
        summary = 'Personal list is empty'
    elif include_completed:
        summary = f'{len(active)} open, {len(done)} done'
    else:
        summary = f'{len(active)} open item(s)'

    return ToolResult.success(
        summary=summary,
        data={'todos': active, 'completed': done, 'count': len(rows)},
    )


def add_todo(args: dict, ctx: BobContext) -> ToolResult:
    text = truncate(args.get('text'), MAX_TODO_TEXT)
    if not text:
        raise ToolError('text cannot be blank.')

    if _scope(ctx).filter_by(completed=False).count() >= MAX_ACTIVE_TODOS:
        raise ToolError(
            f'The personal list already has {MAX_ACTIVE_TODOS} open items. '
            'Ask the agent to clear some out first.'
        )

    existing = _scope(ctx).filter(
        UserTodo.completed.is_(False),
        db.func.lower(UserTodo.text) == text.lower(),
    ).first()
    if existing is not None:
        return ToolResult.success(
            summary=f'Already on the list: {text}',
            data={
                'todo': _summarize(existing),
                'already_present': True,
                'note': 'This item was already open, so nothing was added.',
            },
            record_url='/user_todo',
        )

    highest = db.session.query(
        db.func.max(UserTodo.order)
    ).filter_by(
        user_id=ctx.user_id, organization_id=ctx.organization_id,
    ).scalar()

    todo = UserTodo(
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        text=text,
        completed=False,
        order=(highest or 0) + 1,
    )

    try:
        db.session.add(todo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. todo add failed user=%s', ctx.user_id)
        raise ToolError('The item could not be added. Nothing was changed.')

    result = ToolResult.success(
        summary=f'Added to your list: {text}',
        data={'todo': _summarize(todo), 'already_present': False},
        undoable=True,
        record_url='/user_todo',
    )
    result.data['undo_target_id'] = todo.id
    return result


def undo_add_todo(action, ctx: BobContext) -> str:
    todo = _scope(ctx).filter_by(
        id=(action.result or {}).get('undo_target_id')
    ).first()
    if todo is None:
        raise ToolError('That list item no longer exists.')

    text = todo.text
    db.session.delete(todo)
    _renumber(ctx)
    db.session.commit()
    return f'Removed "{text}" from your list'


def complete_todo(args: dict, ctx: BobContext) -> ToolResult:
    todo = _resolve(ctx, args)

    if todo.completed:
        return ToolResult.success(
            summary=f'Already done: {todo.text}',
            data={'todo': _summarize(todo), 'already_completed': True},
            record_url='/user_todo',
        )

    try:
        todo.completed = True
        _renumber(ctx)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. todo complete failed todo=%s', todo.id)
        raise ToolError('The item could not be checked off. Nothing was changed.')

    result = ToolResult.success(
        summary=f'Checked off: {todo.text}',
        data={'todo': _summarize(todo), 'already_completed': False},
        undoable=True,
        record_url='/user_todo',
    )
    result.data['undo_target_id'] = todo.id
    return result


def undo_complete_todo(action, ctx: BobContext) -> str:
    todo = _scope(ctx).filter_by(
        id=(action.result or {}).get('undo_target_id')
    ).first()
    if todo is None:
        raise ToolError('That list item no longer exists.')

    todo.completed = False
    _renumber(ctx)
    db.session.commit()
    return f'Reopened "{todo.text}"'


def _resolve(ctx: BobContext, args: dict) -> UserTodo:
    """Find a todo by id, or by exact then partial text match.

    Text matching exists because the agent says "check off the lockbox one"
    rather than quoting an id, and list_todos is not always called first.
    """
    todo_id = args.get('todo_id')
    if todo_id is not None:
        try:
            todo_id = int(todo_id)
        except (TypeError, ValueError):
            raise ToolError(f'todo_id must be a number, got {todo_id!r}.')
        todo = _scope(ctx).filter_by(id=todo_id).first()
        if todo is None:
            raise ToolError(
                f'No list item with id {todo_id} is yours. Call list_todos first.'
            )
        return todo

    text = (args.get('text') or '').strip()
    if not text:
        raise ToolError('Pass either todo_id or text to identify the item.')

    open_items = _scope(ctx).filter_by(completed=False).all()
    lowered = text.lower()

    exact = [t for t in open_items if t.text.lower() == lowered]
    if len(exact) == 1:
        return exact[0]

    partial = [t for t in open_items if lowered in t.text.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ToolError(
            f'No open list item matches {text!r}. Call list_todos to see them.'
        )
    raise ToolError(
        f'{len(partial)} open items match {text!r}: '
        + '; '.join(f'{t.id}: {t.text}' for t in partial[:5])
        + '. Ask which one, or pass todo_id.'
    )
