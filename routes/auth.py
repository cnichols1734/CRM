from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from models import User, db
from forms import RegistrationForm, LoginForm

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

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
        return redirect(url_for('auth.login'))

    return render_template('register.html', form=form)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(
            (User.username == form.username.data) |
            (User.email == form.username.data)
        ).first()

        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('main.index'))
        else:
            flash('Invalid username/email or password', 'error')

    return render_template('login.html', form=form)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('auth.login'))

@auth_bp.route('/profile')
@login_required
def view_user_profile():
    return render_template('user_profile.html', user=current_user)

@auth_bp.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    if request.method == 'POST':
        try:
            # Verify current password
            if not current_user.check_password(request.form.get('current_password')):
                flash('Current password is incorrect', 'error')
                return redirect(url_for('auth.view_user_profile'))

            # Update user information
            current_user.email = request.form.get('email')
            current_user.first_name = request.form.get('first_name')
            current_user.last_name = request.form.get('last_name')
            current_user.phone = request.form.get('phone')

            # Update password if provided
            new_password = request.form.get('new_password')
            if new_password:
                if new_password != request.form.get('confirm_password'):
                    flash('New passwords do not match', 'error')
                    return redirect(url_for('auth.view_user_profile'))
                current_user.set_password(new_password)

            db.session.commit()
            flash('Profile updated successfully', 'success')
            return redirect(url_for('auth.view_user_profile'))

        except Exception as e:
            db.session.rollback()
            flash(f'Error updating profile: {str(e)}', 'error')
            return redirect(url_for('auth.view_user_profile'))

    return redirect(url_for('auth.view_user_profile'))

# Debug routes - should be removed in production
@auth_bp.route('/debug_users')
def debug_users():
    users = User.query.all()
    output = []
    for user in users:
        output.append(f"Username: {user.username}, Email: {user.email}")
    return "<br>".join(output)

@auth_bp.route('/test_password/<username>/<password>')
def test_password(username, password):
    user = User.query.filter_by(username=username).first()
    if user:
        return f"Password check result: {user.check_password(password)}"
    return "User not found"
