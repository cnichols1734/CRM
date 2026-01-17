from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import User, db, Contact, ActionPlan, Organization, OrganizationInvite
from forms import RegistrationForm, LoginForm, RequestResetForm, ResetPasswordForm
from flask_mail import Message
from functools import wraps
from datetime import datetime, timedelta
from utils import generate_unique_slug
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
    msg = Message('Reset Your TechnolOG Password',
                  sender=('TechnolOG', current_app.config['MAIL_USERNAME']),
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
                <h1 class="header-logo">TechnolOG</h1>
            </div>
            
            <div class="card">
                <h2 class="title">Reset Your Password</h2>
                <p class="text">Hello {user.first_name},</p>
                <p class="text">We received a request to reset your password for your TechnolOG account. Click the button below to reset it:</p>
                
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
                <p class="footer">&copy; {datetime.now().year} TechnolOG. All rights reserved.</p>
                <p class="footer" style="margin-top: 8px;">This is an automated message, please do not reply to this email.</p>
            </div>
        </div>
    </body>
    </html>
    '''
    
    # Plain text version as fallback
    msg.body = f'''Hello {user.first_name},

We received a request to reset your password for your TechnolOG account.

To reset your password, visit the following link:
{url_for('auth.reset_password', token=token, _external=True)}

If you did not make this request, you can safely ignore this email.
The link will expire in 30 minutes.

Best regards,
TechnolOG Team
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
    """
    Multi-tenant registration.
    Creates a new organization in pending_approval status and the owner user.
    """
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = RegistrationForm()
    if form.validate_on_submit():
        # Get or use company name (add to form if not present)
        company_name = request.form.get('company_name', '').strip()
        if not company_name:
            # Use user's name as company name if not provided
            company_name = f"{form.first_name.data} {form.last_name.data} Realty"
        
        # Generate unique slug with collision handling
        slug = generate_unique_slug(company_name)
        
        # Create org in pending_approval status (free tier)
        org = Organization(
            name=company_name,
            slug=slug,
            subscription_tier='free',
            status='pending_approval',
            max_users=1,
            max_contacts=200,
            can_invite_users=False
        )
        db.session.add(org)
        db.session.flush()  # Get org.id
        
        # Auto-generate username from email if not provided
        username = form.username.data
        if not username:
            # Use email prefix as username, ensure uniqueness
            base_username = form.email.data.split('@')[0]
            username = base_username
            counter = 1
            while User.query.filter_by(username=username).first():
                username = f"{base_username}{counter}"
                counter += 1
        
        # Create user as owner of the new org
        user = User(
            organization_id=org.id,
            org_role='owner',
            username=username,
            email=form.email.data,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            phone=form.phone.data if form.phone.data else None,
            license_number=form.license_number.data if form.license_number.data else None,
            licensed_supervisor=form.licensed_supervisor.data if form.licensed_supervisor.data else None,
            licensed_supervisor_license=form.licensed_supervisor_license.data if form.licensed_supervisor_license.data else None,
            licensed_supervisor_email=form.licensed_supervisor_email.data if form.licensed_supervisor_email.data else None,
            licensed_supervisor_phone=form.licensed_supervisor_phone.data if form.licensed_supervisor_phone.data else None,
            role='admin',  # Org owners get admin role for legacy compatibility
            is_super_admin=False
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        
        # TODO: Notify platform admins for approval
        # notify_platform_admins_new_org_registration(org, user)
        
        flash(
            'Registration submitted! You will receive an email once approved '
            '(typically within 24 hours).', 
            'success'
        )
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
            # Multi-tenant: Check organization status before login
            if user.organization:
                org = user.organization
                if org.status == 'pending_approval':
                    return render_template('auth/login.html', form=form, 
                                         pending_status='pending_approval',
                                         org_name=org.name,
                                         user_email=user.email)
                elif org.status == 'suspended':
                    return render_template('auth/login.html', form=form,
                                         pending_status='suspended',
                                         org_name=org.name)
                elif org.status == 'pending_deletion':
                    return render_template('auth/login.html', form=form,
                                         pending_status='pending_deletion',
                                         org_name=org.name)
                elif org.status != 'active':
                    return render_template('auth/login.html', form=form,
                                         pending_status='inactive',
                                         org_name=org.name)
            
            login_user(user)
            # Update last_login timestamp at the moment of login
            user.last_login = datetime.utcnow()
            db.session.commit()
            # Check for return URL in query args (Flask-Login) or form data (session expiry)
            next_page = request.args.get('next') or request.form.get('next')
            # Basic security check - only allow relative URLs
            if next_page and (next_page.startswith('/') and not next_page.startswith('//')):
                return redirect(next_page)
            return redirect(url_for('main.dashboard'))
        else:
            flash('Invalid username/email or password', 'error')

    return render_template('auth/login.html', form=form)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('auth.login'))


# =============================================================================
# MULTI-TENANT: Registration Status & Invite Acceptance
# =============================================================================

@auth_bp.route('/registration-status')
def registration_status():
    """Check registration status without logging in."""
    email = request.args.get('email', '').strip()
    
    if not email:
        return render_template('auth/registration_status.html', org=None, message=None)
    
    user = User.query.filter_by(email=email).first()
    if not user:
        return render_template('auth/registration_status.html', 
                             org=None, 
                             message="No registration found for this email.")
    
    org = user.organization
    status_messages = {
        'pending_approval': 'Your registration is pending approval. We typically review within 24 hours.',
        'active': 'Your organization is approved! You can log in now.',
        'suspended': 'Your organization has been suspended. Please contact support.',
        'pending_deletion': 'Your organization is scheduled for deletion.',
        'rejected': 'Your registration was not approved. Please contact support for details.'
    }
    
    return render_template('auth/registration_status.html',
                         org=org,
                         message=status_messages.get(org.status, 'Unknown status') if org else 'No organization found')


@auth_bp.route('/invite/<token>')
def accept_invite(token):
    """Accept an organization invite."""
    if current_user.is_authenticated:
        logout_user()
    
    invite = OrganizationInvite.query.filter_by(token=token).first()
    
    if not invite:
        flash('Invalid invitation link.', 'error')
        return redirect(url_for('auth.login'))
    
    if not invite.is_valid:
        if invite.used_at:
            flash('This invitation has already been used.', 'error')
        else:
            flash('This invitation has expired.', 'error')
        return redirect(url_for('auth.login'))
    
    # Store invite token in session for the registration form
    from flask import session
    session['invite_token'] = token
    
    return render_template('auth/accept_invite.html', invite=invite)


@auth_bp.route('/invite/<token>/complete', methods=['POST'])
def complete_invite(token):
    """Complete registration via invite."""
    invite = OrganizationInvite.query.filter_by(token=token).first()
    
    if not invite or not invite.is_valid:
        flash('Invalid or expired invitation.', 'error')
        return redirect(url_for('auth.login'))
    
    # Validate form data
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    
    if not all([username, password, first_name, last_name]):
        flash('All fields are required.', 'error')
        return render_template('auth/accept_invite.html', invite=invite)
    
    # Check username not taken
    if User.query.filter_by(username=username).first():
        flash('Username already taken.', 'error')
        return render_template('auth/accept_invite.html', invite=invite)
    
    # Check email not already registered
    if User.query.filter_by(email=invite.email).first():
        flash('An account with this email already exists.', 'error')
        return redirect(url_for('auth.login'))
    
    # Create user
    user = User(
        organization_id=invite.organization_id,
        org_role=invite.role,
        username=username,
        email=invite.email,
        first_name=first_name,
        last_name=last_name,
        phone=request.form.get('phone', ''),
        license_number=request.form.get('license_number', ''),
        role='agent',  # Legacy field
        is_super_admin=False
    )
    user.set_password(password)
    
    # Mark invite as used
    invite.used_at = datetime.utcnow()
    
    db.session.add(user)
    db.session.commit()
    
    flash('Account created! You can now log in.', 'success')
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

# Debug route removed for multi-tenant security

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
    # CRITICAL: Filter users by current user's organization only!
    users = User.query.filter_by(
        organization_id=current_user.organization_id
    ).order_by(User.created_at.desc()).all()
    
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
    # CRITICAL: Only allow viewing users from same organization
    user = User.query.filter_by(id=user_id, organization_id=current_user.organization_id).first_or_404()
    plan = ActionPlan.get_for_user(user_id)
    
    if not plan or not plan.ai_generated_plan:
        flash('This user has not completed an action plan.', 'error')
        return redirect(url_for('auth.manage_users'))
    
    return render_template('admin/view_action_plan.html', user=user, plan=plan)


@auth_bp.route('/user/<int:user_id>/role', methods=['POST'])
@login_required
@admin_required
def update_user_role(user_id):
    # CRITICAL: Only allow modifying users from same organization
    user = User.query.filter_by(id=user_id, organization_id=current_user.organization_id).first_or_404()
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
    # CRITICAL: Only allow editing users from same organization
    user = User.query.filter_by(id=user_id, organization_id=current_user.organization_id).first_or_404()
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
    
    # CRITICAL: Only allow deleting users from same organization
    user = User.query.filter_by(id=user_id, organization_id=current_user.organization_id).first_or_404()
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
