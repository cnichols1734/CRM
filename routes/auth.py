from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import User, db, Contact, ActionPlan
from forms import RegistrationForm, LoginForm, RequestResetForm, ResetPasswordForm
from flask_mail import Message
from functools import wraps
from datetime import datetime
import pytz

auth_bp = Blueprint('auth', __name__)

def format_datetime_cst(utc_dt):
    if not utc_dt:
        return 'Never'
    # Convert UTC to Central time
    central = pytz.timezone('America/Chicago')
    if utc_dt.tzinfo is None:
        utc_dt = pytz.utc.localize(utc_dt)
    central_dt = utc_dt.astimezone(central)
    # Format as MM/DD/YYYY HH:MM AM/PM
    return central_dt.strftime('%m/%d/%Y %I:%M %p')

def send_reset_email(user):
    token = user.get_reset_token()
    msg = Message('Reset Your Origen Connect Password',
                  sender=('Origen Connect', current_app.config['MAIL_USERNAME']),
                  recipients=[user.email])
    
    # HTML version of the email
    msg.html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            /* Base styles */
            :root {{
                color-scheme: light dark;
            }}
            
            body {{
                font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Helvetica, Arial, sans-serif;
                margin: 0;
                padding: 0;
                background-color: #f3f4f6;
                color: #1f2937;
                line-height: 1.5;
            }}

            @media (prefers-color-scheme: dark) {{
                body {{
                    background-color: #1a1a1a;
                    color: #e5e7eb;
                }}
                .card {{
                    background-color: #2d3e50 !important;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3) !important;
                }}
                .text {{
                    color: #d1d5db !important;
                }}
                .title {{
                    color: #ffffff !important;
                }}
                .divider {{
                    background-color: #4a5568 !important;
                }}
                .footer {{
                    color: #9ca3af !important;
                }}
                .small-text {{
                    color: #9ca3af !important;
                }}
            }}

            .container {{
                max-width: 600px;
                margin: 0 auto;
            }}

            .email-header {{
                background-color: #2d3e50;
                padding: 20px;
                text-align: center;
            }}

            .header-logo {{
                font-family: "Outfit", -apple-system, BlinkMacSystemFont, sans-serif;
                font-size: 28px;
                font-weight: 800;
                letter-spacing: 2px;
                text-transform: uppercase;
                color: #ffffff;
                margin: 0;
            }}

            .card {{
                background-color: #ffffff;
                border-radius: 12px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                padding: 32px;
                margin: 24px;
            }}

            .title {{
                font-size: 24px;
                font-weight: 600;
                color: #2d3e50;
                margin-bottom: 24px;
                text-align: center;
            }}

            .text {{
                color: #4b5563;
                margin-bottom: 24px;
                font-size: 16px;
            }}

            .button-container {{
                text-align: center;
                margin: 32px 0;
            }}

            .button {{
                display: inline-block;
                background-color: #f97316;
                color: #ffffff !important;
                padding: 14px 32px;
                border-radius: 8px;
                text-decoration: none;
                font-weight: 500;
                font-size: 16px;
                transition: background-color 0.2s ease;
            }}

            .button:hover {{
                background-color: #ea580c;
            }}

            .divider {{
                height: 1px;
                background-color: #e5e7eb;
                margin: 32px 0;
            }}

            .small-text {{
                font-size: 14px;
                color: #6b7280;
                margin-bottom: 16px;
            }}

            .email-footer {{
                background-color: #2d3e50;
                padding: 32px 20px;
                text-align: center;
            }}

            .footer {{
                color: #e5e7eb;
                font-size: 14px;
                margin: 0;
                line-height: 1.5;
            }}

            .social-links {{
                margin: 20px 0;
            }}

            .social-link {{
                display: inline-block;
                margin: 0 10px;
                color: #ffffff;
                text-decoration: none;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="email-header">
                <h1 class="header-logo">origen connect</h1>
            </div>
            
            <div class="card">
                <h2 class="title">Reset Your Password</h2>
                <p class="text">Hello {user.first_name},</p>
                <p class="text">We received a request to reset your password for your Origen Connect account. Click the button below to reset it:</p>
                
                <div class="button-container">
                    <a href="{url_for('auth.reset_password', token=token, _external=True)}" class="button">Reset Password</a>
                </div>
                
                <div class="divider"></div>
                
                <p class="small-text">If you didn't request this password reset, you can safely ignore this email. The link will expire in 30 minutes.</p>
                <p class="small-text">For security, this request was received from IP address {request.remote_addr}.</p>
            </div>
            
            <div class="email-footer">
                <div class="social-links">
                    <a href="#" class="social-link">Website</a>
                    <a href="#" class="social-link">Contact</a>
                    <a href="#" class="social-link">Support</a>
                </div>
                <p class="footer">&copy; {datetime.now().year} Origen Connect. All rights reserved.</p>
                <p class="footer" style="margin-top: 8px;">This is an automated message, please do not reply to this email.</p>
            </div>
        </div>
    </body>
    </html>
    '''
    
    # Plain text version as fallback
    msg.body = f'''Hello {user.first_name},

We received a request to reset your password for your Origen Connect account.

To reset your password, visit the following link:
{url_for('auth.reset_password', token=token, _external=True)}

If you did not make this request, you can safely ignore this email.
The link will expire in 30 minutes.

Best regards,
Origen Connect Team
'''
    current_app.extensions['mail'].send(msg)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function

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
            phone=form.phone.data,
            license_number=form.license_number.data,
            licensed_supervisor=form.licensed_supervisor.data,
            licensed_supervisor_license=form.licensed_supervisor_license.data,
            licensed_supervisor_email=form.licensed_supervisor_email.data,
            licensed_supervisor_phone=form.licensed_supervisor_phone.data,
            role='agent'
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Registration successful!', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form=form)

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
            # Update last_login timestamp at the moment of login
            user.last_login = datetime.utcnow()
            db.session.commit()
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('main.dashboard'))
        else:
            flash('Invalid username/email or password', 'error')

    return render_template('auth/login.html', form=form)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('auth.login'))

@auth_bp.route('/profile')
@login_required
def view_user_profile():
    return render_template('auth/user_profile.html', user=current_user)

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
            current_user.license_number = request.form.get('license_number')
            current_user.licensed_supervisor = request.form.get('licensed_supervisor')
            current_user.licensed_supervisor_license = request.form.get('licensed_supervisor_license')
            current_user.licensed_supervisor_email = request.form.get('licensed_supervisor_email')
            current_user.licensed_supervisor_phone = request.form.get('licensed_supervisor_phone')

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
    return render_template('auth/reset_request.html', form=form)

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
    return render_template('auth/reset_password.html', form=form)

@auth_bp.route('/manage-users')
@login_required
@admin_required
def manage_users():
    users = User.query.order_by(User.created_at.desc()).all()
    # Get action plan status for each user
    action_plan_status = {}
    for user in users:
        plan = ActionPlan.get_for_user(user.id)
        action_plan_status[user.id] = plan is not None and plan.ai_generated_plan is not None
    return render_template('admin/manage_users.html', users=users, format_datetime=format_datetime_cst, action_plan_status=action_plan_status)

@auth_bp.route('/user/<int:user_id>/action-plan')
@login_required
@admin_required
def view_user_action_plan(user_id):
    """Admin-only route to view a specific user's action plan."""
    user = User.query.get_or_404(user_id)
    plan = ActionPlan.get_for_user(user_id)
    
    if not plan or not plan.ai_generated_plan:
        flash('This user has not completed an action plan.', 'error')
        return redirect(url_for('auth.manage_users'))
    
    return render_template('admin/view_action_plan.html', user=user, plan=plan)


@auth_bp.route('/user/<int:user_id>/role', methods=['POST'])
@login_required
@admin_required
def update_user_role(user_id):
    user = User.query.get_or_404(user_id)
    new_role = request.form.get('role')
    
    if new_role not in ['admin', 'agent']:
        flash('Invalid role specified.', 'error')
        return redirect(url_for('auth.manage_users'))
    
    if user.id == current_user.id and new_role != 'admin':
        flash('You cannot remove your own admin privileges.', 'error')
        return redirect(url_for('auth.manage_users'))
    
    user.role = new_role
    db.session.commit()
    flash(f'Role updated for {user.username}', 'success')
    return redirect(url_for('auth.manage_users'))

@auth_bp.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        try:
            user.first_name = request.form.get('first_name')
            user.last_name = request.form.get('last_name')
            user.email = request.form.get('email')
            user.phone = request.form.get('phone')
            user.license_number = request.form.get('license_number')
            user.licensed_supervisor = request.form.get('licensed_supervisor')
            user.licensed_supervisor_license = request.form.get('licensed_supervisor_license')
            user.licensed_supervisor_email = request.form.get('licensed_supervisor_email')
            user.licensed_supervisor_phone = request.form.get('licensed_supervisor_phone')
            
            # Update password if provided
            new_password = request.form.get('new_password')
            if new_password:
                user.set_password(new_password)
            
            db.session.commit()
            flash('User updated successfully', 'success')
            return redirect(url_for('auth.manage_users'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating user: {str(e)}', 'error')
    
    return render_template('auth/user_profile.html', user=user, is_admin_edit=True)

@auth_bp.route('/user/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    if current_user.id == user_id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('auth.manage_users'))
    
    user = User.query.get_or_404(user_id)
    try:
        # Delete all contacts associated with the user
        Contact.query.filter_by(user_id=user.id).delete()
        # Delete the user
        db.session.delete(user)
        db.session.commit()
        flash(f'User {user.username} has been deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting user: {str(e)}', 'error')
    
    return redirect(url_for('auth.manage_users'))

@auth_bp.before_request
def update_last_login():
    if current_user.is_authenticated:
        current_user.last_login = datetime.utcnow()
        try:
            db.session.commit()
        except:
            db.session.rollback()
