from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from models import db, Task, Contact, TaskType, TaskSubtype, User
from datetime import datetime, timezone, time
import pytz
from sqlalchemy.orm import joinedload
from sqlalchemy import case
import logging

logger = logging.getLogger(__name__)

tasks_bp = Blueprint('tasks', __name__)

def get_user_timezone():
    """Helper function to get user's timezone"""
    return pytz.timezone('America/Chicago')

def convert_to_utc(dt, user_tz=None):
    """Helper function to convert datetime to UTC"""
    if not user_tz:
        user_tz = get_user_timezone()
    if not dt.tzinfo:
        dt = user_tz.localize(dt)
    return dt.astimezone(timezone.utc)

def convert_to_local(dt, user_tz=None):
    """Helper function to convert datetime from UTC to local time"""
    if not user_tz:
        user_tz = get_user_timezone()
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(user_tz)

@tasks_bp.route('/tasks')
@login_required
def tasks():
    view = request.args.get('view', 'my')
    status_filter = request.args.get('status', 'pending')
    user_tz = get_user_timezone()

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

    # Get current time in user's timezone
    now = datetime.now(user_tz)

    # Convert task due_dates and scheduled_times to user's timezone
    for task in tasks:
        if isinstance(task.due_date, datetime):
            task.due_date = convert_to_local(task.due_date, user_tz)
        if task.scheduled_time:
            task.scheduled_time = convert_to_local(task.scheduled_time, user_tz)

    return render_template('tasks/list.html', 
                         tasks=tasks, 
                         show_all=current_user.role == 'admin' and view == 'all',
                         current_status=status_filter,
                         now=now)

@tasks_bp.route('/tasks/new', methods=['GET', 'POST'])
@login_required
def create_task():
    if request.method == 'POST':
        try:
            contact_id = request.form.get('contact_id')
            contact = db.session.get(Contact, contact_id)
            if not contact:
                abort(404)

            user_tz = get_user_timezone()
            
            # Parse the date in user's timezone
            due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d')
            
            # If there's a scheduled time, combine it with the date
            if request.form.get('scheduled_time'):
                time_str = request.form.get('scheduled_time')
                scheduled_time = datetime.strptime(time_str, '%H:%M').time()
                due_date = datetime.combine(due_date.date(), scheduled_time)
            else:
                # If no time specified, use end of day
                due_date = datetime.combine(due_date.date(), time(23, 59, 59))
            
            # Convert to UTC for storage
            utc_due_date = convert_to_utc(due_date, user_tz)

            task = Task(
                organization_id=current_user.organization_id,
                contact_id=contact_id,
                assigned_to_id=request.form.get('assigned_to_id', current_user.id),
                created_by_id=current_user.id,
                type_id=request.form.get('type_id'),
                subtype_id=request.form.get('subtype_id'),
                subject=request.form.get('subject'),
                description=request.form.get('description'),
                priority=request.form.get('priority', 'medium'),
                due_date=utc_due_date,
                property_address=request.form.get('property_address'),
                scheduled_time=utc_due_date if request.form.get('scheduled_time') else None
            )

            db.session.add(task)
            db.session.commit()
            
            # Sync to Google Calendar (non-blocking)
            try:
                from services import calendar_service
                from models import UserEmailIntegration
                
                # Check if assigned user has calendar sync enabled
                integration = UserEmailIntegration.query.filter_by(user_id=task.assigned_to_id).first()
                if integration and integration.calendar_sync_enabled:
                    logger.info(f"Calendar sync enabled for user {task.assigned_to_id}, syncing task {task.id}")
                    base_url = request.url_root.rstrip('/')
                    result = calendar_service.sync_task_to_calendar(task, base_url)
                    if result:
                        logger.info(f"Calendar event created for task {task.id}")
                    else:
                        logger.warning(f"Calendar sync returned False for task {task.id}")
                else:
                    logger.debug(f"Calendar sync not enabled for user {task.assigned_to_id}")
            except Exception as e:
                logger.warning(f"Calendar sync failed for task {task.id}: {e}")
                # Don't fail the task creation if calendar sync fails

            flash('Task created successfully!', 'success')
            
            # Handle return navigation
            return_to = request.form.get('return_to')
            if return_to == 'contact':
                contact_id = request.form.get('return_contact_id')
                return redirect(url_for('contacts.view_contact', contact_id=contact_id))
            return redirect(url_for('tasks.tasks'))

        except Exception as e:
            db.session.rollback()
            flash(f'Error creating task: {str(e)}', 'error')
            return redirect(url_for('tasks.tasks'))

    # Filter by organization for multi-tenancy
    contacts = Contact.query.filter_by(
        user_id=current_user.id,
        organization_id=current_user.organization_id
    ).all()
    task_types = TaskType.query.filter_by(organization_id=current_user.organization_id).all()
    # Only show users from the same organization
    users = User.query.filter_by(organization_id=current_user.organization_id).all() if current_user.role == 'admin' else [current_user]

    return render_template('tasks/create.html',
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
        user_tz = get_user_timezone()
        
        task.subject = request.form.get('subject')
        task.status = request.form.get('status')
        task.priority = request.form.get('priority')
        task.description = request.form.get('description')
        task.property_address = request.form.get('property_address')
        
        # Parse the date in user's timezone
        due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d')
        
        # If there's a scheduled time, combine it with the date
        if request.form.get('scheduled_time'):
            time_str = request.form.get('scheduled_time')
            scheduled_time = datetime.strptime(time_str, '%H:%M').time()
            due_date = datetime.combine(due_date.date(), scheduled_time)
        else:
            # If no time specified, use end of day
            due_date = datetime.combine(due_date.date(), time(23, 59, 59))
        
        # Convert to UTC for storage
        utc_due_date = convert_to_utc(due_date, user_tz)
        task.due_date = utc_due_date
        task.scheduled_time = utc_due_date if request.form.get('scheduled_time') else None

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
        
        # Sync to Google Calendar (non-blocking)
        try:
            from services import calendar_service
            base_url = request.url_root.rstrip('/')
            calendar_service.update_calendar_event(task, base_url)
        except Exception as e:
            logger.warning(f"Calendar sync failed for task {task.id}: {e}")
        
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
        # Delete from Google Calendar first (before task is deleted)
        try:
            from services import calendar_service
            calendar_service.delete_calendar_event(task)
        except Exception as e:
            logger.warning(f"Calendar delete failed for task {task.id}: {e}")
        
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
    # CRITICAL: Filter by organization for multi-tenancy
    task = Task.query.options(
        joinedload(Task.contact),
        joinedload(Task.task_type),
        joinedload(Task.task_subtype),
        joinedload(Task.assigned_to)
    ).filter_by(id=task_id, organization_id=current_user.organization_id).first_or_404()

    user_tz = get_user_timezone()

    # Convert dates to user's timezone
    if isinstance(task.due_date, datetime):
        task.due_date = convert_to_local(task.due_date, user_tz)
    if task.scheduled_time:
        task.scheduled_time = convert_to_local(task.scheduled_time, user_tz)

    # Filter by organization for multi-tenancy
    contacts = Contact.query.filter_by(organization_id=current_user.organization_id).all()
    task_types = TaskType.query.filter_by(organization_id=current_user.organization_id).all()
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

    return render_template('tasks/view.html',
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
        was_completed = task.status == 'completed'
        
        if new_status in ['pending', 'completed']:
            task.status = new_status
            # Set completed_at timestamp when task is marked as completed
            if new_status == 'completed':
                task.completed_at = datetime.now(timezone.utc)
            else:
                task.completed_at = None
            
        new_priority = request.form.get('priority')
        if new_priority in ['low', 'medium', 'high']:
            task.priority = new_priority
            
        db.session.commit()
        
        # Sync completion status to Google Calendar (non-blocking)
        try:
            from services import calendar_service
            if new_status == 'completed' and not was_completed:
                # Task was just completed - mark event as completed
                calendar_service.mark_event_completed(task)
            elif new_status == 'pending' and was_completed:
                # Task was uncompleted - update event to remove completed prefix
                base_url = request.url_root.rstrip('/')
                calendar_service.update_calendar_event(task, base_url)
        except Exception as e:
            logger.warning(f"Calendar sync failed for task {task.id}: {e}")
        
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
