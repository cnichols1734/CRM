from flask import Flask, render_template, redirect, url_for, flash, request, abort, Response, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from urllib.parse import urlparse
from datetime import datetime, timedelta
from models import db, User, ContactGroup, Contact, Interaction, Task, TaskType, TaskSubtype
from forms import RegistrationForm, LoginForm, ContactForm
import csv
from io import StringIO
from werkzeug.utils import secure_filename
import os
from sqlalchemy.orm import joinedload

app = Flask(__name__)

def create_app():
    app = Flask(__name__)
    app.config.from_object('config.Config')

    # Initialize extensions
    db.init_app(app)
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'login'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Routes
    @app.route('/')
    @login_required
    def index():
        show_all = request.args.get('view') == 'all' and current_user.role == 'admin'
        sort_by = request.args.get('sort', 'created_at')
        sort_dir = request.args.get('dir', 'desc')
        search_query = request.args.get('q', '').strip()

        # Start with base query
        if show_all:
            query = Contact.query
        else:
            query = Contact.query.filter_by(user_id=current_user.id)

        # Apply search if query exists
        if search_query:
            search_filter = (
                    (Contact.first_name.ilike(f'%{search_query}%')) |
                    (Contact.last_name.ilike(f'%{search_query}%')) |
                    (Contact.email.ilike(f'%{search_query}%')) |
                    (Contact.phone.ilike(f'%{search_query}%'))
            )
            query = query.filter(search_filter)

        # Apply sorting
        if sort_by == 'owner':
            # Join with User table and sort by owner's name
            query = query.join(User, Contact.user_id == User.id)
            if sort_dir == 'asc':
                query = query.order_by(User.first_name.asc(), User.last_name.asc())
            else:
                query = query.order_by(User.first_name.desc(), User.last_name.desc())
        elif sort_by == 'potential_commission':
            # Handle potential_commission sorting with NULL values
            from sqlalchemy import func
            if sort_dir == 'asc':
                query = query.order_by(func.coalesce(Contact.potential_commission, 0).asc())
            else:
                query = query.order_by(func.coalesce(Contact.potential_commission, 0).desc())
        else:
            # Map frontend column names to model attributes
            sort_map = {
                'name': [Contact.first_name, Contact.last_name],
                'email': [Contact.email],
                'phone': [Contact.phone],
                'address': [Contact.street_address],
                'notes': [Contact.notes],
                'created_at': [Contact.created_at]
            }

            if sort_by in sort_map:
                sort_attrs = sort_map[sort_by]
                if sort_dir == 'asc':
                    query = query.order_by(*[attr.asc() for attr in sort_attrs])
                else:
                    query = query.order_by(*[attr.desc() for attr in sort_attrs])

        contacts = query.all()
        return render_template('index.html',
                               contacts=contacts,
                               show_all=show_all,
                               current_sort=sort_by,
                               current_dir=sort_dir)

    @app.route('/contact/<int:contact_id>')
    @login_required
    def view_contact(contact_id):
        contact = Contact.query.get_or_404(contact_id)
        if not current_user.role == 'admin' and contact.user_id != current_user.id:
            abort(403)
        all_groups = ContactGroup.query.all()
        return render_template('view_contact.html', contact=contact, all_groups=all_groups)

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for('index'))

        form = RegistrationForm()
        if form.validate_on_submit():
            user = User(
                username=form.username.data,
                email=form.email.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                role='agent'
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            flash('Registration successful!', 'success')
            return redirect(url_for('login'))

        return render_template('register.html', form=form)

    @app.route('/debug_users')
    def debug_users():
        users = User.query.all()
        output = []
        for user in users:
            output.append(f"Username: {user.username}, Email: {user.email}")
        return "<br>".join(output)

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('You have been logged out successfully.', 'success')
        return redirect(url_for('login'))

    @app.route('/profile')
    @login_required
    def view_user_profile():
        return render_template('user_profile.html', user=current_user)

    @app.route('/test_password/<username>/<password>')
    def test_password(username, password):
        user = User.query.filter_by(username=username).first()
        if user:
            return f"Password check result: {user.check_password(password)}"
        return "User not found"

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('index'))

        form = LoginForm()
        if form.validate_on_submit():
            # Check if input is email or username
            user = User.query.filter(
                (User.username == form.username.data) |
                (User.email == form.username.data)
            ).first()

            if user and user.check_password(form.password.data):
                login_user(user)
                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('index'))
            else:
                flash('Invalid username/email or password', 'error')

        return render_template('login.html', form=form)

    @app.route('/contact/new', methods=['GET', 'POST'])
    @login_required
    def create_contact():
        form = ContactForm()
        form.group_ids.choices = [(g.id, g.name) for g in ContactGroup.query.order_by('sort_order')]

        if form.validate_on_submit():
            contact = Contact(
                user_id=current_user.id,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                email=form.email.data,
                phone=form.phone.data,
                street_address=form.street_address.data,
                city=form.city.data,
                state=form.state.data,
                zip_code=form.zip_code.data,
                notes=form.notes.data,
                potential_commission=form.potential_commission.data or 5000.00
            )

            selected_groups = ContactGroup.query.filter(
                ContactGroup.id.in_(form.group_ids.data)
            ).all()
            contact.groups = selected_groups

            db.session.add(contact)
            db.session.commit()
            flash('Contact created successfully!', 'success')
            return redirect(url_for('index'))

        return render_template('contact_form.html', form=form)

    @app.route('/contacts/<int:contact_id>/edit', methods=['POST'])
    @login_required
    def edit_contact(contact_id):
        contact = Contact.query.get_or_404(contact_id)

        # Check if user has permission to edit this contact
        if not current_user.role == 'admin' and contact.user_id != current_user.id:
            abort(403)

        # Get form data with validation and print for debugging
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')

        print("Form data received:")
        print(f"First Name: {first_name}")
        print(f"Last Name: {last_name}")
        print("All form data:", request.form)

        # Validate required fields
        if not first_name or not last_name:
            error_msg = 'First name and last name are required'
            print(f"Validation error: {error_msg}")
            return {
                'status': 'error',
                'message': error_msg
            }, 400

        try:
            # Update contact information from form data
            contact.first_name = first_name
            contact.last_name = last_name
            contact.email = request.form.get('email')
            contact.phone = request.form.get('phone')
            contact.street_address = request.form.get('street_address')
            contact.city = request.form.get('city')
            contact.state = request.form.get('state')
            contact.zip_code = request.form.get('zip_code')
            contact.notes = request.form.get('notes')
            contact.potential_commission = float(request.form.get('potential_commission', 5000.00))

            # Handle groups
            selected_group_ids = request.form.getlist('group_ids')
            print(f"Selected group IDs: {selected_group_ids}")

            contact.groups = ContactGroup.query.filter(
                ContactGroup.id.in_(selected_group_ids)
            ).all()

            db.session.commit()
            flash('Contact updated successfully!', 'success')
            return {'status': 'success'}, 200

        except Exception as e:
            db.session.rollback()
            error_msg = f"Error updating contact: {str(e)}"
            print(error_msg)
            return {'status': 'error', 'message': error_msg}, 500

    @app.route('/contacts/<int:contact_id>/delete', methods=['POST'])
    @login_required
    def delete_contact(contact_id):
        contact = Contact.query.get_or_404(contact_id)

        # Check if user has permission to delete this contact
        if not current_user.role == 'admin' and contact.user_id != current_user.id:
            abort(403)

        try:
            db.session.delete(contact)
            db.session.commit()
            flash('Contact deleted successfully!', 'success')
            return {'status': 'success'}, 200
        except Exception as e:
            db.session.rollback()
            print(f"Error deleting contact: {str(e)}")
            return {'status': 'error', 'message': 'Error deleting contact'}, 500

    @app.route('/import-contacts', methods=['POST'])
    @login_required
    def import_contacts():
        if 'file' not in request.files:
            return {'status': 'error', 'message': 'No file uploaded'}, 400

        file = request.files['file']
        if file.filename == '':
            return {'status': 'error', 'message': 'No file selected'}, 400

        if not file.filename.endswith('.csv'):
            return {'status': 'error', 'message': 'Please upload a CSV file'}, 400

        try:
            # Read CSV file
            stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_data = csv.DictReader(stream)

            success_count = 0
            error_count = 0

            for row in csv_data:
                try:
                    # Create new contact
                    contact = Contact(
                        user_id=current_user.id,
                        first_name=row['first_name'].strip(),
                        last_name=row['last_name'].strip(),
                        email=row['email'].strip() if row['email'] else None,
                        phone=row['phone'].strip() if row['phone'] else None,
                        street_address=row['street_address'].strip() if row['street_address'] else None,
                        city=row['city'].strip() if row['city'] else None,
                        state=row['state'].strip() if row['state'] else None,
                        zip_code=row['zip_code'].strip() if row['zip_code'] else None,
                        notes=row['notes'].strip() if row['notes'] else None
                    )

                    # Handle groups if provided
                    if 'groups' in row and row['groups']:
                        group_names = [name.strip() for name in row['groups'].split(';')]
                        groups = ContactGroup.query.filter(ContactGroup.name.in_(group_names)).all()
                        contact.groups = groups

                    db.session.add(contact)
                    success_count += 1

                except Exception as e:
                    error_count += 1
                    continue

            db.session.commit()
            return {
                'status': 'success',
                'success_count': success_count,
                'error_count': error_count
            }

        except Exception as e:
            return {
                'status': 'error',
                'message': f'Error processing CSV file: {str(e)}'
            }, 500

    @app.route('/export-contacts')
    @login_required
    def export_contacts():
        # Get contacts based on current view and filters
        show_all = request.args.get('view') == 'all' and current_user.role == 'admin'
        search_query = request.args.get('q', '').strip()

        if show_all:
            query = Contact.query
        else:
            query = Contact.query.filter_by(user_id=current_user.id)

        # Apply search if query exists
        if search_query:
            search_filter = (
                    (Contact.first_name.ilike(f'%{search_query}%')) |
                    (Contact.last_name.ilike(f'%{search_query}%')) |
                    (Contact.email.ilike(f'%{search_query}%')) |
                    (Contact.phone.ilike(f'%{search_query}%'))
            )
            query = query.filter(search_filter)

        contacts = query.all()

        # Create CSV in memory
        output = StringIO()
        writer = csv.writer(output)

        # Write headers
        writer.writerow(['first_name', 'last_name', 'email', 'phone', 'street_address',
                         'city', 'state', 'zip_code', 'notes', 'groups'])

        # Write contact data
        for contact in contacts:
            groups = ';'.join([group.name for group in contact.groups])
            writer.writerow([
                contact.first_name,
                contact.last_name,
                contact.email or '',
                contact.phone or '',
                contact.street_address or '',
                contact.city or '',
                contact.state or '',
                contact.zip_code or '',
                contact.notes or '',
                groups
            ])

        # Prepare response
        output.seek(0)
        filename = f"{current_user.first_name}_{current_user.last_name}_contacts.csv"

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv",
            }
        )

    @app.route('/dashboard')
    @login_required
    def dashboard():
        # Get the view parameter (default to 'my' for non-admins)
        view = request.args.get('view', 'my')

        # For non-admin users, always show their own contacts
        if current_user.role != 'admin':
            show_all = False
            contacts = Contact.query.filter_by(user_id=current_user.id).all()
        else:
            # For admin users, respect the view parameter
            show_all = view == 'all'
            if show_all:
                contacts = Contact.query.all()
            else:
                contacts = Contact.query.filter_by(user_id=current_user.id).all()

        # Calculate key metrics
        total_contacts = len(contacts)
        total_commission = sum(c.potential_commission or 0 for c in contacts)
        avg_commission = total_commission / total_contacts if total_contacts > 0 else 0

        # Get top contacts by commission
        top_contacts = sorted(
            contacts,
            key=lambda x: x.potential_commission or 0,
            reverse=True
        )[:5]

        # Get group distribution
        groups = ContactGroup.query.all()
        group_stats = []
        for group in groups:
            contact_count = len([c for c in contacts if group in c.groups])
            if contact_count > 0:  # Only include groups with contacts
                group_stats.append({
                    'name': group.name,
                    'count': contact_count
                })

        # Get upcoming tasks (next 7 days)
        now = datetime.now()
        seven_days = now + timedelta(days=7)

        if current_user.role == 'admin' and show_all:
            upcoming_tasks = Task.query.filter(
                Task.due_date.between(now, seven_days),
                Task.status != 'completed'
            ).order_by(Task.due_date.asc()).limit(5).all()
        else:
            upcoming_tasks = Task.query.filter(
                Task.assigned_to_id == current_user.id,
                Task.due_date.between(now, seven_days),
                Task.status != 'completed'
            ).order_by(Task.due_date.asc()).limit(5).all()

        return render_template('dashboard.html',
                             show_all=show_all,
                             total_commission=total_commission,
                             total_contacts=total_contacts,
                             avg_commission=avg_commission,
                             group_stats=group_stats,
                             top_contacts=top_contacts,
                             upcoming_tasks=upcoming_tasks,
                             now=now)  # Pass current time for due date calculations

    @app.route('/tasks')
    @login_required
    def tasks():
        # Get the view parameter (default to 'my' for non-admins)
        view = request.args.get('view', 'my')

        # For non-admin users, always show their own tasks
        if current_user.role != 'admin':
            show_all = False
            tasks = Task.query.filter_by(assigned_to_id=current_user.id).all()
        else:
            # For admin users, respect the view parameter
            show_all = view == 'all'
            if show_all:
                tasks = Task.query.all()
            else:
                tasks = Task.query.filter_by(assigned_to_id=current_user.id).all()

        return render_template('tasks.html', tasks=tasks, show_all=show_all)

    @app.route('/task/new', methods=['GET', 'POST'])
    @login_required
    def create_task():
        if request.method == 'POST':
            try:
                contact_id = request.form.get('contact_id')
                contact = Contact.query.get_or_404(contact_id)

                # Create new task
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
                return redirect(url_for('tasks'))

            except Exception as e:
                db.session.rollback()
                flash(f'Error creating task: {str(e)}', 'error')
                return redirect(url_for('tasks'))

        # GET request - show the form
        contacts = Contact.query.filter_by(user_id=current_user.id).all()
        task_types = TaskType.query.all()
        users = User.query.all() if current_user.role == 'admin' else [current_user]

        return render_template('create_task.html',
                             contacts=contacts,
                             task_types=task_types,
                             users=users)

    @app.route('/task/<int:task_id>/edit', methods=['POST'])
    @login_required
    def edit_task(task_id):
        task = Task.query.get_or_404(task_id)

        try:
            # Update basic fields first
            task.subject = request.form.get('subject')
            task.status = request.form.get('status')
            task.priority = request.form.get('priority')
            task.description = request.form.get('description')
            task.property_address = request.form.get('property_address')
            task.due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d')

            # Handle scheduled time
            if request.form.get('scheduled_time'):
                scheduled_time = datetime.strptime(request.form.get('scheduled_time'), '%H:%M').time()
                task.scheduled_time = datetime.combine(task.due_date, scheduled_time)
            else:
                task.scheduled_time = None

            # Handle task type and subtype specifically
            new_type_id = request.form.get('task_type_id')
            new_subtype_id = request.form.get('task_subtype_id')

            if new_type_id:
                task.task_type_id = int(new_type_id)
                # Only update subtype if it belongs to the selected type
                if new_subtype_id:
                    subtype = TaskSubtype.query.get(int(new_subtype_id))
                    if subtype and str(subtype.task_type_id) == new_type_id:
                        task.task_subtype_id = int(new_subtype_id)

            # Handle other relationships
            if request.form.get('contact_id'):
                task.contact_id = int(request.form.get('contact_id'))

            db.session.commit()
            return jsonify({'status': 'success'}), 200

        except Exception as e:
            db.session.rollback()
            print(f"Error updating task: {str(e)}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/task/<int:task_id>/delete', methods=['POST'])
    @login_required
    def delete_task(task_id):
        task = Task.query.get_or_404(task_id)

        # Check permissions
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

    @app.route('/api/task-types/<int:type_id>/subtypes')
    @login_required
    def get_task_subtypes(type_id):
        subtypes = TaskSubtype.query.filter_by(task_type_id=type_id).all()
        return jsonify([{
            'id': subtype.id,
            'name': subtype.name
        } for subtype in subtypes])

    @app.route('/task/<int:task_id>')
    @login_required
    def view_task(task_id):
        # Add .options(joinedload()) to eagerly load relationships
        task = Task.query.options(
            joinedload(Task.contact),
            joinedload(Task.task_type),
            joinedload(Task.task_subtype)
        ).get_or_404(task_id)

        contacts = Contact.query.all()
        task_types = TaskType.query.all()
        task_subtypes = TaskSubtype.query.filter_by(task_type_id=task.task_type.id).all()

        return render_template('view_task.html',
                             task=task,
                             contacts=contacts,
                             task_types=task_types,
                             task_subtypes=task_subtypes)

    return app

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5004, debug=True)