"""Personal to-dos shared by BOB, the agent app and the web list."""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
from uuid import uuid4

from models import ClientBrowseInquiry, UserTodo, db
from services.bob_tools.common import MAX_LIST_RESULTS, ToolError
from services.bob_tools.context import BobContext, ToolResult
from services.personal_todos import empty_details, lock_owner, valid_text, validate_details

MAX_TODO_TEXT = 500
MAX_TODOS = 10000


def _scope(ctx):
    # Personal data stays private even when the caller is an org administrator.
    return UserTodo.query.filter_by(user_id=ctx.user_id, organization_id=ctx.organization_id)


def _rows(ctx):
    return _scope(ctx).order_by(UserTodo.completed, UserTodo.order, UserTodo.id).populate_existing().all()


def _summarize(todo, *, full=True):
    details = deepcopy(todo.details or empty_details())
    result = {'todo_id': todo.id, 'client_id': todo.client_id or str(todo.id),
              'text': todo.text, 'completed': bool(todo.completed), 'order': todo.order}
    if full:
        result['details'] = details
    else:
        result.update(due_date=details['due_date'], priority=details['priority'],
                      subtodo_count=len(details['subtodos']), comment_count=len(details['comments']))
    return result


def _state(todo):
    return {key: deepcopy(getattr(todo, key))
            for key in ('id', 'text', 'completed', 'order', 'client_id', 'details')}


def _lock(ctx):
    owner = ctx.load_user()
    if owner is None:
        raise ToolError('Your account is no longer available.')
    lock_owner(owner)


def _write(handler):
    """Lock before reading and save only the affected rows for guarded undo."""
    @wraps(handler)
    def run(args, ctx):
        _lock(ctx)
        if handler.__name__ == 'add_todo':
            targets = []
        elif handler.__name__ == 'reorder_todos':
            completed = args.get('completed', False)
            if type(completed) is not bool:
                raise ToolError('completed must be true or false.')
            targets = _scope(ctx).filter_by(completed=completed).populate_existing().all()
        else:
            targets = [_resolve(ctx, args, open_only=handler.__name__ == 'complete_todo')]
        before = {row.id: _state(row) for row in targets}
        result = handler(args, ctx)
        db.session.flush()
        if result.data.get('already_present') or result.data.get('undo_unavailable'):
            result.undoable = False
            return result
        identities = set(before)
        if result.data.get('todo'):
            identities.add(result.data['todo']['todo_id'])
        rows = _scope(ctx).filter(UserTodo.id.in_(identities)).populate_existing().all()
        after = {row.id: _state(row) for row in rows}
        versions = {row.id: row.updated_at.isoformat() if row.updated_at else None for row in rows}
        changed = sorted(key for key in before.keys() | after.keys()
                         if before.get(key) != after.get(key))
        result.undoable = bool(changed)
        if changed:
            result.data['undo_payload'] = [
                {'before': before.get(key), 'after': after.get(key), 'after_version': versions.get(key)}
                for key in changed]
        # The dispatcher commits the mutation and its audit record together.
        return result
    return run


def _result(todo, summary):
    db.session.flush()
    return ToolResult.success(summary=summary, data={'todo': _summarize(todo)}, record_url='/user_todo')


def _details(todo, changes):
    previous = todo.details or empty_details()
    details = deepcopy(previous)
    details.update(changes)
    try:
        todo.details = validate_details(details, previous)
    except ValueError as error:
        raise ToolError(str(error))


def _title(value):
    if not valid_text(value):
        raise ToolError('The title must contain 1 to 500 characters.')
    return value.strip()


def _integer(value, name, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ToolError(f'{name} must be an integer of at least {minimum}.')
    return value


def _resolve(ctx, args, *, open_only=False):
    todo_id = args.get('todo_id')
    if todo_id is not None:
        todo_id = _integer(todo_id, 'todo_id', minimum=1)
        todo = _scope(ctx).filter_by(id=todo_id).populate_existing().first()
        if todo is None:
            raise ToolError('That to-do is not on your personal list. Call list_todos first.')
        return todo
    text = args.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ToolError('Pass todo_id or text to identify the to-do.')
    query = _scope(ctx).populate_existing()
    if open_only:
        query = query.filter_by(completed=False)
    lowered = text.strip().lower()
    exact = query.filter(db.func.lower(UserTodo.text) == lowered).limit(6).all()
    matches = exact or query.filter(db.func.lower(UserTodo.text).contains(lowered, autoescape=True)).limit(6).all()
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ToolError('No to-do matches that text. Call list_todos first.')
    raise ToolError('Multiple to-dos match: ' + '; '.join(f'{row.id}: {row.text}' for row in matches[:5])
                    + '. Ask which one, then pass todo_id.')


def list_todos(args, ctx):
    include_completed = args.get('include_completed', False)
    if type(include_completed) is not bool:
        raise ToolError('include_completed must be true or false.')
    offset = _integer(args.get('offset', 0), 'offset')
    query = _scope(ctx)
    if not include_completed:
        query = query.filter_by(completed=False)
    total = query.count()
    rows = query.order_by(UserTodo.completed, UserTodo.order, UserTodo.id).offset(offset).limit(MAX_LIST_RESULTS).all()
    return ToolResult.success(
        summary=f'{total} personal to-do(s)',
        data={'todos': [_summarize(row, full=False) for row in rows if not row.completed],
              'completed': [_summarize(row, full=False) for row in rows if row.completed],
              'count': len(rows), 'total_matching': total,
              'next_offset': offset + len(rows) if offset + len(rows) < total else None})


def get_todo(args, ctx):
    todo = _resolve(ctx, args)
    return _result(todo, todo.text)


@_write
def add_todo(args, ctx):
    text = _title(args.get('text'))
    existing = _scope(ctx).filter(UserTodo.completed.is_(False), db.func.lower(UserTodo.text) == text.lower()).first()
    if existing:
        result = _result(existing, f'Already on your list: {text}')
        result.data.update(already_present=True, note='Nothing was added or changed. Use update_todo to change this item.')
        return result
    if _scope(ctx).count() >= MAX_TODOS:
        raise ToolError('Your personal list is full. Remove some items before adding another.')
    highest = db.session.query(db.func.max(UserTodo.order)).filter_by(
        user_id=ctx.user_id, organization_id=ctx.organization_id, completed=False).scalar()
    todo = UserTodo(user_id=ctx.user_id, organization_id=ctx.organization_id,
                    client_id=str(uuid4()), text=text, completed=False, order=(highest if highest is not None else -1) + 1)
    _details(todo, {key: args[key] for key in ('description', 'due_date', 'priority') if key in args})
    db.session.add(todo)
    result = _result(todo, f'Added to your list: {text}')
    result.data['already_present'] = False
    return result


@_write
def update_todo(args, ctx):
    todo = _resolve(ctx, args)
    if not any(key in args for key in ('title', 'description', 'due_date', 'priority', 'completed')):
        raise ToolError('Provide a title, description, due date, priority or completion change.')
    if 'title' in args:
        todo.text = _title(args['title'])
    if 'completed' in args:
        if type(args['completed']) is not bool:
            raise ToolError('completed must be true or false.')
        todo.completed = args['completed']
    _details(todo, {key: args[key] for key in ('description', 'due_date', 'priority') if key in args})
    return _result(todo, f'Updated: {todo.text}')


@_write
def complete_todo(args, ctx):
    todo = _resolve(ctx, args, open_only=True)
    already = bool(todo.completed)
    todo.completed = True
    result = _result(todo, f'{"Already done" if already else "Checked off"}: {todo.text}')
    result.data['already_completed'] = already
    return result


@_write
def delete_todo(args, ctx):
    todo = _resolve(ctx, args)
    result = _result(todo, f'Deleted: {todo.text}')
    linked = ClientBrowseInquiry.query.filter_by(
        organization_id=ctx.organization_id, followup_todo_id=todo.id).first()
    if linked is not None:
        result.data['undo_unavailable'] = True
        result.data['note'] = 'This inquiry follow-up cannot be restored automatically after deletion.'
        result.summary += '. Undo is unavailable for linked inquiry follow-ups.'
    db.session.delete(todo)
    return result


def _reorder(entries, ids, *, key):
    expected_type = type(entries[0][key]) if entries else str
    if (not isinstance(ids, list) or any(type(value) is not expected_type for value in ids)
            or len(ids) != len(entries) or len(set(ids)) != len(ids)
            or set(ids) != {entry[key] for entry in entries}):
        raise ToolError('Provide every current ID exactly once. Read the list again before reordering.')
    by_id = {entry[key]: entry for entry in entries}
    return [by_id[identity] for identity in ids]


@_write
def reorder_todos(args, ctx):
    completed = args.get('completed', False)
    if type(completed) is not bool:
        raise ToolError('completed must be true or false.')
    rows = [row for row in _rows(ctx) if row.completed == completed]
    ordered = _reorder([{'id': row.id, 'row': row} for row in rows], args.get('todo_ids'), key='id')
    for position, entry in enumerate(ordered):
        entry['row'].order = position
    return ToolResult.success(summary='Reordered your personal to-dos.',
                              data={'todo_ids': args['todo_ids'], 'completed': completed}, record_url='/user_todo')


def _entry(todo, args, key, id_key):
    entries = deepcopy((todo.details or empty_details())[key])
    entry = next((item for item in entries if item['id'] == args.get(id_key)), None)
    if entry is None:
        raise ToolError(f'That entry is not on this to-do. Call get_todo to find its {id_key}.')
    return entries, entry


@_write
def add_subtodo(args, ctx):
    todo = _resolve(ctx, args)
    entries = deepcopy((todo.details or empty_details())['subtodos'])
    entry = {'id': str(uuid4()), 'text': _title(args.get('title')), 'completed': False}
    entries.append(entry)
    _details(todo, {'subtodos': entries})
    return _result(todo, f'Added sub-to-do: {entry["text"]}')


@_write
def update_subtodo(args, ctx):
    todo = _resolve(ctx, args)
    entries, entry = _entry(todo, args, 'subtodos', 'subtodo_id')
    if not any(key in args for key in ('title', 'completed')):
        raise ToolError('Provide a title or completion change.')
    if 'title' in args:
        entry['text'] = _title(args['title'])
    if 'completed' in args:
        entry['completed'] = args['completed']
    _details(todo, {'subtodos': entries})
    return _result(todo, f'Updated sub-to-do: {entry["text"]}')


@_write
def delete_subtodo(args, ctx):
    todo = _resolve(ctx, args)
    entries, entry = _entry(todo, args, 'subtodos', 'subtodo_id')
    entries.remove(entry)
    _details(todo, {'subtodos': entries})
    return _result(todo, f'Deleted sub-to-do: {entry["text"]}')


@_write
def reorder_subtodos(args, ctx):
    todo = _resolve(ctx, args)
    entries = (todo.details or empty_details())['subtodos']
    _details(todo, {'subtodos': _reorder(entries, args.get('subtodo_ids'), key='id')})
    return _result(todo, 'Reordered the sub-to-dos.')


@_write
def add_todo_comment(args, ctx):
    todo = _resolve(ctx, args)
    entries = deepcopy((todo.details or empty_details())['comments'])
    entries.append({'id': str(uuid4()), 'text': args.get('comment')})
    _details(todo, {'comments': entries})
    return _result(todo, f'Added a comment to: {todo.text}')


@_write
def update_todo_comment(args, ctx):
    todo = _resolve(ctx, args)
    entries, entry = _entry(todo, args, 'comments', 'comment_id')
    entry['text'] = args.get('comment')
    _details(todo, {'comments': entries})
    return _result(todo, f'Updated a comment on: {todo.text}')


@_write
def delete_todo_comment(args, ctx):
    todo = _resolve(ctx, args)
    entries, entry = _entry(todo, args, 'comments', 'comment_id')
    entries.remove(entry)
    _details(todo, {'comments': entries})
    return _result(todo, f'Deleted a comment from: {todo.text}')


def undo_todo_change(action, ctx):
    _lock(ctx)
    changes = (action.result or {}).get('undo_payload')
    if not changes:
        # Actions created before detailed to-dos shipped only stored a target ID.
        row = _resolve(ctx, {'todo_id': (action.result or {}).get('undo_target_id')})
        if action.executed_at and row.updated_at and row.updated_at > action.executed_at:
            raise ToolError('This to-do changed after that action. Read it again before making another change.')
        if action.tool_name == 'add_todo':
            if row.details:
                raise ToolError('This to-do has details added later. Delete it explicitly if you still want to remove it.')
            db.session.delete(row)
        elif action.tool_name == 'complete_todo':
            row.completed = False
        else:
            raise ToolError('This older action cannot be undone automatically.')
        db.session.flush()
        return 'Undid the personal to-do change.'
    identities = [(change['after'] or change['before'])['id'] for change in changes]
    rows = {row.id: row for row in _scope(ctx).filter(UserTodo.id.in_(identities)).populate_existing().all()}
    for change in changes:
        expected = change['after']
        identity = (expected or change['before'])['id']
        row = rows.get(identity)
        version = row.updated_at.isoformat() if row and row.updated_at else None
        if ((_state(row) if row else None) != expected
                or ('after_version' in change and version != change['after_version'])):
            raise ToolError('This to-do changed after that action. Read it again before making another change.')
        if row is None:
            client_id = change['before'].get('client_id')
            if (db.session.get(UserTodo, identity) is not None
                    or (client_id and _scope(ctx).filter_by(client_id=client_id).first() is not None)):
                raise ToolError('This to-do cannot be restored because its ID is already in use.')
    for change in changes:
        previous = change['before']
        row = rows.get((change['after'] or previous)['id'])
        if previous is None:
            db.session.delete(row)
        else:
            if row is None:
                row = UserTodo(user_id=ctx.user_id, organization_id=ctx.organization_id)
                db.session.add(row)
            for key, value in previous.items():
                setattr(row, key, deepcopy(value))
    db.session.flush()
    return 'Undid the personal to-do change.'


undo_add_todo = undo_todo_change
undo_complete_todo = undo_todo_change
