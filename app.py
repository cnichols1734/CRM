from flask import Flask, render_template, redirect, url_for, flash, request, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from urllib.parse import urlparse
from datetime import datetime
from models import db, User, ContactGroup, Contact, Interaction
from forms import RegistrationForm, LoginForm, ContactForm


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
        contacts = Contact.query.filter_by(user_id=current_user.id).all() if not current_user.role == 'admin' \
            else Contact.query.all()
        return render_template('index.html', contacts=contacts)

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
        # Update choices to work with multiple select
        form.group_ids.choices = [(g.id, g.name) for g in ContactGroup.query.order_by('sort_order')]

        if form.validate_on_submit():
            contact = Contact(
                user_id=current_user.id,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                email=form.email.data,
                phone=form.phone.data,
                address=form.address.data,
                notes=form.notes.data
            )
            # Add selected groups
            selected_groups = ContactGroup.query.filter(ContactGroup.id.in_(form.group_ids.data)).all()
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
        
        # Update contact information from form data
        contact.email = request.form.get('email')
        contact.phone = request.form.get('phone')
        contact.address = request.form.get('address')
        contact.notes = request.form.get('notes')
        
        # Handle groups
        selected_group_ids = request.form.getlist('group_ids')
        contact.groups = ContactGroup.query.filter(ContactGroup.id.in_(selected_group_ids)).all()
        
        try:
            db.session.commit()
            flash('Contact updated successfully!', 'success')
            return {'status': 'success'}, 200
        except Exception as e:
            db.session.rollback()
            print(f"Error updating contact: {str(e)}")
            return {'status': 'error', 'message': 'Error updating contact'}, 500

    return app


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5003, debug=True)