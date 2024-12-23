from flask import Flask, render_template, redirect, url_for, flash, request, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from urllib.parse import urlparse
from datetime import datetime
from models import db, User, ContactGroup, Contact, Interaction
from forms import RegistrationForm, LoginForm, ContactForm
import csv
from io import StringIO
from werkzeug.utils import secure_filename
import os


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
                notes=form.notes.data
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

    return app


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5003, debug=True)