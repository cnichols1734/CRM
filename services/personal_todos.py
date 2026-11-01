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
    items = [{'id': str(row.id), 'text': row.text, 'completed': row.completed} for row in rows]
    revision = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    return {'items': items, 'revision': revision}


def replace_web(user, active, completed):
    # Keep row identity, including references from follow-ups, when web users reorder.
    available = defaultdict(deque)
    for row in rows_for(user):
        available[row.text].append(row)
    for done, values in ((False, active), (True, completed)):
        for order, value in enumerate(values):
            row = available[value].popleft() if available[value] else UserTodo(user_id=user.id, organization_id=user.organization_id)
            row.text, row.completed, row.order = value, done, order
            db.session.add(row)
    for remaining in available.values():
        for row in remaining:
            db.session.delete(row)
    db.session.flush()


def valid_text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 500
