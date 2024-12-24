from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import User, db
from forms import RegistrationForm, LoginForm, RequestResetForm, ResetPasswordForm
from flask_mail import Message

auth_bp = Blueprint('auth', __name__)

def send_reset_email(user):
    token = user.get_reset_token()
    msg = Message('Password Reset Request',
                  sender=('TechnolOG', current_app.config['MAIL_USERNAME']),
                  recipients=[user.email])
    msg.body = f'''To reset your password, visit the following link:
{url_for('auth.reset_password', token=token, _external=True)}

If you did not make this request then simply ignore this email and no changes will be made.
'''
    current_app.extensions['mail'].send(msg)

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
        return redirect(url_for('main.dashboard'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(
            (User.username == form.username.data) |
            (User.email == form.username.data)
        ).first()

        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('main.dashboard'))
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

@auth_bp.route('/reset_password', methods=['GET', 'POST'])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            send_reset_email(user)
            flash('An email has been sent with instructions to reset your password.', 'info')
            return redirect(url_for('auth.login'))
        else:
            flash('There is no account with that email.', 'error')
    return render_template('reset_request.html', form=form)

@auth_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    user = User.verify_reset_token(token)
    if user is None:
        flash('That is an invalid or expired token', 'error')
        return redirect(url_for('auth.reset_request'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been updated! You are now able to log in', 'success')
        return redirect(url_for('auth.login'))
    return render_template('reset_password.html', form=form)
