"""Shared personal todo snapshots for the CRM and agent app."""
import hashlib
import json
from collections import defaultdict, deque

from models import User, UserTodo, db


def rows_for(user):
    return UserTodo.query.filter_by(user_id=user.id, organization_id=user.organization_id).order_by(UserTodo.completed, UserTodo.order, UserTodo.id).all()


def lock_owner(user):
    # Serialize writers even when this user has no todos yet.
    db.session.query(User.id).filter_by(id=user.id, organization_id=user.organization_id).with_for_update().one()


def snapshot(rows):
    items = [{'id': row.client_id or str(row.id), 'text': row.text, 'completed': row.completed, 'details': row.details or empty_details()} for row in rows]
    revision = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    return {'items': items, 'revision': revision}


def replace_web(user, active, completed, identities=None):
    if identities is not None:
        from uuid import UUID
        if not isinstance(identities, dict):
            raise ValueError('Refresh your to-do list and try again.')
        rows = rows_for(user)
        existing = {row.client_id or str(row.id): row for row in rows}
        all_ids = set()
        for key, values in [('active', active), ('completed', completed)]:
            ids = identities.get(key)
            if not isinstance(ids, list) or len(ids) != len(values):
                raise ValueError('Refresh your to-do list and try again.')
            for identity in ids:
                if not isinstance(identity, str) or identity in all_ids:
                    raise ValueError('Refresh your to-do list and try again.')
                if identity not in existing:
                    try:
                        if len(identity) > 36:
                            raise ValueError()
                        UUID(identity)
                    except ValueError:
                        raise ValueError('That to-do changed. Refresh and try again.')
                all_ids.add(identity)
        for key, done, values in [('active', False, active), ('completed', True, completed)]:
            for order, (identity, value) in enumerate(zip(identities[key], values)):
                row = existing.pop(identity, None)
                if row is None:
                    row = UserTodo(user_id=user.id, organization_id=user.organization_id, client_id=identity)
                    db.session.add(row)
                row.text, row.completed, row.order = value, done, order
        for row in existing.values():
            db.session.delete(row)
        db.session.flush()
        return

    # Keep row identity, including references from follow-ups, when web users reorder.
    old_rows = rows_for(user)
    available = defaultdict(deque)
    for row in old_rows:
        available[row.text].append(row)
    # Old pages cannot identify which duplicate was renamed, removed or completed.
    ambiguous = any(len(group) > 1 and any(row.details for row in group)
                    for group in available.values())
    if ambiguous and (active != [row.text for row in old_rows if not row.completed]
                      or completed != [row.text for row in old_rows if row.completed]):
        raise ValueError('Reload this page before changing duplicate to-dos with details.')
    plan = []
    for done, values in ((False, active), (True, completed)):
        for order, value in enumerate(values):
            row = available[value].popleft() if available[value] else None
            plan.append((row, value, done, order))
    removed = [row for remaining in available.values() for row in remaining]
    if any(row is None for row, _, _, _ in plan) and any(row.details for row in removed):
        raise ValueError('Reload this page before renaming a to-do with details.')
    for row, value, done, order in plan:
        if row is None:
            row = UserTodo(user_id=user.id, organization_id=user.organization_id)
        row.text, row.completed, row.order = value, done, order
        db.session.add(row)
    for row in removed:
        db.session.delete(row)
    db.session.flush()


def valid_text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 500


def empty_details():
    return {'description': '', 'due_date': None, 'priority': 0, 'subtodos': [], 'comments': []}


def validate_details(value, previous=None):
    from datetime import date, datetime, timezone
    from uuid import UUID
    if not isinstance(value, dict):
        raise ValueError('Invalid to-do details.')
    result = empty_details()
    description = value.get('description', '')
    if not isinstance(description, str) or len(description) > 10000:
        raise ValueError('Descriptions can contain up to 10,000 characters.')
    result['description'] = description
    due = value.get('due_date')
    if due is not None:
        if not isinstance(due, str) or len(due) != 10:
            raise ValueError('Choose a valid due date.')
        try:
            date.fromisoformat(due)
        except ValueError:
            raise ValueError('Choose a valid due date.')
    result['due_date'] = due
    priority = value.get('priority', 0)
    if type(priority) is not int or priority not in range(4):
        raise ValueError('Choose a valid priority.')
    result['priority'] = priority
    previous_comments = {item['id']: item for item in (previous or {}).get('comments', [])}
    for key, limit, max_text in [('subtodos', 100, 500), ('comments', 200, 4000)]:
        entries = value.get(key, [])
        if not isinstance(entries, list) or len(entries) > limit:
            raise ValueError(f'A to-do can contain up to {limit} {key}.')
        seen = set()
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                raise ValueError('Each entry needs a unique ID.')
            try:
                UUID(item['id'])
            except ValueError:
                raise ValueError('Each entry needs a unique ID.')
            if item['id'] in seen:
                raise ValueError('Each entry needs a unique ID.')
            seen.add(item['id'])
            text = item.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > max_text:
                raise ValueError(f'Entries must contain 1 to {max_text} characters.')
            entry = {'id': item['id'], 'text': text.strip()}
            if key == 'subtodos':
                if type(item.get('completed')) is not bool:
                    raise ValueError('Each sub-to-do needs a completion state.')
                entry['completed'] = item['completed']
            else:
                old = previous_comments.get(item['id'])
                entry['created_at'] = old['created_at'] if old else datetime.now(timezone.utc).isoformat()
            result[key].append(entry)
    return result if result != empty_details() else None
