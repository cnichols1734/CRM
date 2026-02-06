from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import User, db, Contact, ActionPlan, Organization, OrganizationInvite
from forms import RegistrationForm, LoginForm, RequestResetForm, ResetPasswordForm
from services.email_service import get_email_service
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
    """Send password reset email via SendGrid."""
    token = user.get_reset_token()
    reset_url = url_for('auth.reset_password', token=token, _external=True)
    
    email_service = get_email_service()
    return email_service.send_password_reset(user, reset_url)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('main.contacts'))
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/terms-privacy')
def terms_privacy():
    """Display Terms of Service and Privacy Policy."""
    return render_template('auth/terms_privacy.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """
    Multi-tenant registration.
    Creates a new organization in pending_approval status and the owner user.
    """
    if current_user.is_authenticated:
        return redirect(url_for('main.contacts'))

    form = RegistrationForm()
    if form.validate_on_submit():
        # Honeypot check - bots fill this hidden field, humans don't see it
        honeypot = request.form.get('website', '')
        if honeypot:
            # Bot detected! Return fake success to not tip them off
            current_app.logger.warning(f"Bot registration blocked (honeypot): {form.email.data}")
            flash(
                'Registration submitted! You will receive an email once approved '
                '(typically within 24 hours).', 
                'success'
            )
            return redirect(url_for('auth.login'))
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
            max_contacts=10000,
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
        else:
            # Prevent email addresses from being used as usernames
            if '@' in username:
                flash('Username cannot be an email address. Please choose a different username.', 'error')
                return render_template('auth/register.html', form=form)
        
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
        # Prioritize email matches over username matches to prevent conflicts
        # when usernames are set to email addresses
        login_input = form.username.data.strip()

        # First try exact email match
        user = User.query.filter(User.email == login_input).first()

        # If no email match, try username match
        if not user:
            user = User.query.filter(User.username == login_input).first()

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

    # Prevent email addresses from being used as usernames
    if '@' in username:
        flash('Username cannot be an email address. Please choose a different username.', 'error')
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
    user.last_login = datetime.utcnow()
    
    # Mark invite as used
    invite.used_at = datetime.utcnow()
    
    db.session.add(user)
    db.session.commit()
    
    login_user(user)
    flash(f'Welcome to {invite.organization.name}!', 'success')
    return redirect(url_for('main.dashboard'))

@auth_bp.route('/profile')
@login_required
def view_user_profile():
    from models import UserEmailIntegration
    gmail_integration = UserEmailIntegration.query.filter_by(user_id=current_user.id).first()
    return render_template('auth/user_profile.html', user=current_user, gmail_integration=gmail_integration)

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


@auth_bp.route('/profile/update-broker', methods=['POST'])
@login_required
def update_broker_info():
    """Update organization broker information (admin/owner only)."""
    # Check if user has permission
    if current_user.org_role not in ('admin', 'owner'):
        flash('You do not have permission to update brokerage information.', 'error')
        return redirect(url_for('auth.view_user_profile'))
    
    if not current_user.organization:
        flash('No organization found.', 'error')
        return redirect(url_for('auth.view_user_profile'))
    
    try:
        org = current_user.organization
        
        # Update broker fields
        broker_name = request.form.get('broker_name', '').strip()
        org.broker_name = broker_name if broker_name else None
        
        broker_license = request.form.get('broker_license_number', '').strip()
        org.broker_license_number = broker_license if broker_license else None
        
        broker_address = request.form.get('broker_address', '').strip()
        org.broker_address = broker_address if broker_address else None
        
        db.session.commit()
        flash('Brokerage information updated successfully.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating brokerage info: {str(e)}', 'error')
    
    return redirect(url_for('auth.view_user_profile'))


@auth_bp.route('/reset_password', methods=['GET', 'POST'])
def reset_request():
    if current_user.is_authenticated:
        return redirect(url_for('main.contacts'))
    form = RequestResetForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            if send_reset_email(user):
                flash('An email has been sent with instructions to reset your password.', 'info')
                return redirect(url_for('auth.login'))
            else:
                flash('Unable to send reset email. Please try again later or contact support.', 'error')
        else:
            flash('There is no account with that email.', 'error')
    return render_template('auth/reset_request.html', form=form)

@auth_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.contacts'))
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
    
    # Get action plan status for all users in one query (avoid N+1)
    user_ids = [u.id for u in users]
    plans_with_content = ActionPlan.query.filter(
        ActionPlan.user_id.in_(user_ids),
        ActionPlan.ai_generated_plan.isnot(None)
    ).with_entities(ActionPlan.user_id).all()
    action_plan_status = {user_id: True for (user_id,) in plans_with_content}
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
