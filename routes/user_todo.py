from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from models import db, UserTodo
from datetime import datetime

bp = Blueprint('user_todo', __name__)

@bp.route('/user_todo')
@login_required
def user_todo():
    """Render the user todo list page."""
    return render_template('user_todo.html')

@bp.route('/api/user_todos/get')
@login_required
def get_todos():
    """Get all todos for the current user."""
    from services.personal_todos import snapshot, rows_for
    rows = rows_for(current_user)
    payload = {
        'active': [todo.text for todo in rows if not todo.completed],
        'completed': [todo.text for todo in rows if todo.completed]
    }
    if request.args.get('include_ids') == '1':
        payload['identities'] = {
            'active': [row.client_id or str(row.id) for row in rows if not row.completed],
            'completed': [row.client_id or str(row.id) for row in rows if row.completed]
        }
    response = jsonify(payload)
    response.headers['X-Todo-Revision'] = snapshot(rows)['revision']
    return response

@bp.route('/api/user_todos/save', methods=['POST'])
@login_required
def save_todos():
    """Save the current state of the todo lists."""
    from services.personal_todos import lock_owner, rows_for, snapshot, replace_web, valid_text
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='Send your active and completed todos.'), 400
    active, completed = data.get('active', []), data.get('completed', [])
    if any(not isinstance(values, list) for values in (active, completed)) or any(
        not valid_text(value) for value in active + completed
    ) or len(active) + len(completed) > 10000:
        return jsonify(error='Todos must contain 1 to 500 characters.'), 400
    lock_owner(current_user)
    revision = snapshot(rows_for(current_user))['revision']
    if 'revision' in data and data['revision'] != revision:
        return jsonify(error='Your todos changed elsewhere. Refresh and try again.'), 409
    try:
        replace_web(current_user, active, completed, data.get('identities'))
    except ValueError as error:
        return jsonify(error=str(error)), 409
    revision = snapshot(rows_for(current_user))['revision']
    db.session.commit()
    response = jsonify(status='success')
    response.headers['X-Todo-Revision'] = revision
    return response
