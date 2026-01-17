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
    active_todos = UserTodo.query.filter_by(
        user_id=current_user.id,
        completed=False
    ).order_by(UserTodo.order).all()
    
    completed_todos = UserTodo.query.filter_by(
        user_id=current_user.id,
        completed=True
    ).order_by(UserTodo.order).all()
    
    return jsonify({
        'active': [todo.text for todo in active_todos],
        'completed': [todo.text for todo in completed_todos]
    })

@bp.route('/api/user_todos/save', methods=['POST'])
@login_required
def save_todos():
    """Save the current state of the todo lists."""
    data = request.get_json()
    
    # Delete all existing todos for this user
    UserTodo.query.filter_by(user_id=current_user.id).delete()
    
    # Add active todos
    for i, text in enumerate(data.get('active', [])):
        todo = UserTodo(
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            text=text,
            completed=False,
            order=i
        )
        db.session.add(todo)
    
    # Add completed todos
    for i, text in enumerate(data.get('completed', [])):
        todo = UserTodo(
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            text=text,
            completed=True,
            order=i
        )
        db.session.add(todo)
    
    db.session.commit()
    return jsonify({'status': 'success'}) 