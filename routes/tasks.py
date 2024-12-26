from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from models import db, Task, Contact, TaskType, TaskSubtype, User
from datetime import datetime
from sqlalchemy.orm import joinedload
from sqlalchemy import case

tasks_bp = Blueprint('tasks', __name__)

@tasks_bp.route('/tasks')
@login_required
def tasks():
    view = request.args.get('view', 'my')
    status_filter = request.args.get('status', 'pending')

    query = Task.query.options(
        joinedload(Task.contact),
        joinedload(Task.assigned_to),
        joinedload(Task.task_type),
        joinedload(Task.task_subtype)
    )

    if current_user.role != 'admin' or view != 'all':
        query = query.filter_by(assigned_to_id=current_user.id)

    if status_filter != 'all':
        query = query.filter_by(status=status_filter)

    tasks = query.order_by(
        Task.due_date.asc(),
        case(
            (Task.priority == 'high', 1),
            (Task.priority == 'medium', 2),
            else_=3
        )
    ).all()

    # Convert task due_dates to date objects if they're datetime objects
    for task in tasks:
        if isinstance(task.due_date, datetime):
            task.due_date = task.due_date.date()

    return render_template('tasks.html', 
                         tasks=tasks, 
                         show_all=current_user.role == 'admin' and view == 'all',
                         current_status=status_filter,
                         now=datetime.now().date())

@tasks_bp.route('/tasks/new', methods=['GET', 'POST'])
@login_required
def create_task():
    if request.method == 'POST':
        try:
            contact_id = request.form.get('contact_id')
            contact = db.session.get(Contact, contact_id)
            if not contact:
                abort(404)

            task = Task(
                contact_id=contact_id,
                assigned_to_id=request.form.get('assigned_to_id', current_user.id),
                created_by_id=current_user.id,
                type_id=request.form.get('type_id'),
                subtype_id=request.form.get('subtype_id'),
                subject=request.form.get('subject'),
                description=request.form.get('description'),
                priority=request.form.get('priority', 'medium'),
                due_date=datetime.strptime(request.form.get('due_date'), '%Y-%m-%d'),
                property_address=request.form.get('property_address'),
                scheduled_time=datetime.strptime(request.form.get('scheduled_time'), '%Y-%m-%dT%H:%M') if request.form.get('scheduled_time') else None
            )

            db.session.add(task)
            db.session.commit()

            flash('Task created successfully!', 'success')
            return redirect(url_for('tasks.tasks'))

        except Exception as e:
            db.session.rollback()
            flash(f'Error creating task: {str(e)}', 'error')
            return redirect(url_for('tasks.tasks'))

    contacts = Contact.query.filter_by(user_id=current_user.id).all()
    task_types = TaskType.query.all()
    users = User.query.all() if current_user.role == 'admin' else [current_user]

    return render_template('create_task.html',
                         contacts=contacts,
                         task_types=task_types,
                         users=users)

@tasks_bp.route('/tasks/<int:task_id>/edit', methods=['POST'])
@login_required
def edit_task(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        abort(404)

    try:
        task.subject = request.form.get('subject')
        task.status = request.form.get('status')
        task.priority = request.form.get('priority')
        task.description = request.form.get('description')
        task.property_address = request.form.get('property_address')
        task.due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d')

        if request.form.get('scheduled_time'):
            scheduled_time = datetime.strptime(request.form.get('scheduled_time'), '%H:%M').time()
            task.scheduled_time = datetime.combine(task.due_date, scheduled_time)
        else:
            task.scheduled_time = None

        new_type_id = request.form.get('type_id')
        new_subtype_id = request.form.get('subtype_id')

        if new_type_id:
            task.type_id = int(new_type_id)
            if new_subtype_id:
                subtype = db.session.get(TaskSubtype, int(new_subtype_id))
                if subtype and str(subtype.task_type_id) == new_type_id:
                    task.subtype_id = int(new_subtype_id)

        if request.form.get('contact_id'):
            task.contact_id = int(request.form.get('contact_id'))

        db.session.commit()
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@tasks_bp.route('/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        abort(404)

    if not current_user.role == 'admin' and task.assigned_to_id != current_user.id:
        abort(403)

    try:
        db.session.delete(task)
        db.session.commit()
        flash('Task deleted successfully!', 'success')
        return {'status': 'success'}, 200
    except Exception as e:
        db.session.rollback()
        return {'status': 'error', 'message': str(e)}, 500

@tasks_bp.route('/tasks/types/<int:type_id>/subtypes')
@login_required
def get_task_subtypes(type_id):
    subtypes = TaskSubtype.query.filter_by(task_type_id=type_id).all()
    return jsonify([{
        'id': subtype.id,
        'name': subtype.name
    } for subtype in subtypes])

@tasks_bp.route('/tasks/<int:task_id>')
@login_required
def view_task(task_id):
    task = Task.query.options(
        joinedload(Task.contact),
        joinedload(Task.task_type),
        joinedload(Task.task_subtype),
        joinedload(Task.assigned_to)
    ).get_or_404(task_id)

    contacts = Contact.query.all()
    task_types = TaskType.query.all()
    task_subtypes = TaskSubtype.query.filter_by(task_type_id=task.task_type.id).all()

    # Check if it's an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'id': task.id,
            'subject': task.subject,
            'status': task.status,
            'priority': task.priority,
            'description': task.description,
            'due_date': task.due_date.isoformat(),
            'property_address': task.property_address,
            'scheduled_time': task.scheduled_time.isoformat() if task.scheduled_time else None,
            'contact': {
                'id': task.contact.id,
                'first_name': task.contact.first_name,
                'last_name': task.contact.last_name
            },
            'task_type': {
                'id': task.task_type.id,
                'name': task.task_type.name
            },
            'task_subtype': {
                'id': task.task_subtype.id,
                'name': task.task_subtype.name
            },
            'assigned_to': {
                'first_name': task.assigned_to.first_name,
                'last_name': task.assigned_to.last_name
            }
        })

    return render_template('view_task.html',
                         task=task,
                         contacts=contacts,
                         task_types=task_types,
                         task_subtypes=task_subtypes)

@tasks_bp.route('/tasks/<int:task_id>/quick-update', methods=['POST'])
@login_required
def quick_update_task(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        abort(404)
    
    if not current_user.role == 'admin' and task.assigned_to_id != current_user.id:
        abort(403)
        
    try:
        new_status = request.form.get('status')
        if new_status in ['pending', 'completed']:
            task.status = new_status
            
        new_priority = request.form.get('priority')
        if new_priority in ['low', 'medium', 'high']:
            task.priority = new_priority
            
        db.session.commit()
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
