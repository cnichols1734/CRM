# routes/organization.py
"""
Organization management routes.
Settings, member management, deletion/suspension.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user, logout_user
from datetime import datetime, timedelta
from models import db, User, Organization, OrganizationInvite, ActionPlan
from services.tenant_service import (
    org_owner_required, org_admin_required, is_org_owner, is_org_admin,
    can_modify_user, validate_last_owner, can_assign_role, ROLE_HIERARCHY
)

org_bp = Blueprint('org', __name__, url_prefix='/org')


# =============================================================================
# ORGANIZATION SETTINGS
# =============================================================================

@org_bp.route('/settings')
@login_required
@org_admin_required
def settings():
    """View organization settings."""
    org = current_user.organization
    return render_template('organization/settings.html', org=org)


@org_bp.route('/settings/update', methods=['POST'])
@login_required
@org_owner_required
def update_settings():
    """Update organization settings (owner only)."""
    org = current_user.organization
    
    # Only allow updating name and logo
    new_name = request.form.get('name', '').strip()
    if new_name and len(new_name) >= 2:
        org.name = new_name
    
    logo_url = request.form.get('logo_url', '').strip()
    if logo_url:
        org.logo_url = logo_url
    elif request.form.get('remove_logo'):
        org.logo_url = None
    
    db.session.commit()
    flash('Organization settings updated.', 'success')
    return redirect(url_for('org.settings'))


# =============================================================================
# MEMBER MANAGEMENT
# =============================================================================

@org_bp.route('/members')
@login_required
@org_admin_required
def members():
    """View organization members."""
    org = current_user.organization
    members = org.users.order_by(User.org_role.desc(), User.first_name).all()
    
    # Check if org can invite more users
    can_invite = org.can_invite_users and not org.is_at_user_limit
    
    # Get pending invites
    pending_invites = org.invites.filter(
        OrganizationInvite.used_at.is_(None),
        OrganizationInvite.expires_at > datetime.utcnow()
    ).all()
    
    # Get action plan status for each member (for Pro/Enterprise tiers)
    action_plan_status = {}
    for member in members:
        plan = ActionPlan.get_for_user(member.id)
        action_plan_status[member.id] = plan is not None and plan.ai_generated_plan is not None
    
    return render_template('organization/members.html',
                          org=org,
                          members=members,
                          can_invite=can_invite,
                          pending_invites=pending_invites,
                          action_plan_status=action_plan_status,
                          role_hierarchy=ROLE_HIERARCHY)


@org_bp.route('/members/<int:user_id>/update-role', methods=['POST'])
@login_required
@org_admin_required
def update_member_role(user_id):
    """Update a member's role."""
    org = current_user.organization
    target_user = User.query.filter_by(
        id=user_id,
        organization_id=org.id
    ).first_or_404()
    
    # Check permission
    if not can_modify_user(current_user, target_user):
        return jsonify({'success': False, 'error': 'You cannot modify this user.'}), 403
    
    new_role = request.form.get('role')
    if new_role not in ROLE_HIERARCHY:
        return jsonify({'success': False, 'error': 'Invalid role.'}), 400
    
    # Can't assign a role higher than or equal to your own
    if not can_assign_role(current_user.org_role, new_role):
        return jsonify({'success': False, 'error': 'You cannot assign this role.'}), 403
    
    # Check if demoting last owner
    try:
        validate_last_owner(org, target_user, new_role=new_role)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    
    target_user.org_role = new_role
    db.session.commit()
    
    return jsonify({'success': True, 'new_role': new_role})


@org_bp.route('/members/invite', methods=['POST'])
@login_required
@org_admin_required
def send_invite():
    """Send an invitation to a new member."""
    org = current_user.organization
    
    # Check if org can invite
    if not org.can_invite_users:
        flash('Your plan does not allow inviting users. Upgrade to Pro.', 'error')
        return redirect(url_for('org.members'))
    
    if org.is_at_user_limit:
        flash('User limit reached. Cannot send more invites.', 'error')
        return redirect(url_for('org.members'))
    
    email = request.form.get('email', '').strip().lower()
    role = request.form.get('role', 'agent')
    
    if not email:
        flash('Email is required.', 'error')
        return redirect(url_for('org.members'))
    
    # Check email not already a user
    if User.query.filter_by(email=email).first():
        flash('A user with this email already exists.', 'error')
        return redirect(url_for('org.members'))
    
    # Check not already invited
    existing_invite = org.invites.filter(
        OrganizationInvite.email == email,
        OrganizationInvite.used_at.is_(None),
        OrganizationInvite.expires_at > datetime.utcnow()
    ).first()
    
    if existing_invite:
        flash('An invitation has already been sent to this email.', 'error')
        return redirect(url_for('org.members'))
    
    # Validate role
    if role not in ('agent', 'admin'):
        role = 'agent'
    
    # Only owners can invite admins
    if role == 'admin' and current_user.org_role != 'owner':
        role = 'agent'
    
    # Create invite
    invite = OrganizationInvite(
        organization_id=org.id,
        email=email,
        invited_by_id=current_user.id,
        role=role,
        token=OrganizationInvite.generate_token(),
        expires_at=datetime.utcnow() + timedelta(hours=72)
    )
    db.session.add(invite)
    db.session.commit()
    
    # Send invite email
    from services.org_notifications import send_invite_email
    email_sent = send_invite_email(org, current_user, email, invite.token)
    
    if email_sent:
        flash(f'Invitation sent to {email}.', 'success')
    else:
        # Provide manual invite link as fallback
        invite_url = url_for('auth.accept_invite', token=invite.token, _external=True)
        flash(f'Email failed to send. Share this link manually: {invite_url}', 'warning')
    return redirect(url_for('org.members'))


@org_bp.route('/members/<int:user_id>/remove', methods=['POST'])
@login_required
@org_admin_required
def remove_member(user_id):
    """Remove a member from the organization."""
    from models import Contact, DailyTodoList, UserTodo, ActionPlan, Task
    
    org = current_user.organization
    target_user = User.query.filter_by(
        id=user_id,
        organization_id=org.id
    ).first_or_404()
    
    # Cannot remove yourself
    if target_user.id == current_user.id:
        flash('You cannot remove yourself from the organization.', 'error')
        return redirect(url_for('org.members'))
    
    # Check permission
    if not can_modify_user(current_user, target_user):
        flash('You do not have permission to remove this user.', 'error')
        return redirect(url_for('org.members'))
    
    # Check if removing last owner
    try:
        validate_last_owner(org, target_user, removing=True)
    except ValueError as e:
        flash(str(e), 'error')
        return redirect(url_for('org.members'))
    
    # Delete the user and associated data
    try:
        # Delete all related records that don't have CASCADE on their foreign keys
        # or have NOT NULL constraints
        
        # Delete daily todo lists
        DailyTodoList.query.filter_by(user_id=target_user.id).delete()
        
        # Delete user todos (has CASCADE but delete explicitly for clarity)
        UserTodo.query.filter_by(user_id=target_user.id).delete()
        
        # Delete action plans (has CASCADE but delete explicitly for clarity)
        ActionPlan.query.filter_by(user_id=target_user.id).delete()
        
        # Delete tasks (assigned_to_id and created_by_id)
        Task.query.filter(
            (Task.assigned_to_id == target_user.id) | 
            (Task.created_by_id == target_user.id)
        ).delete(synchronize_session=False)
        
        # Delete all contacts associated with the user
        Contact.query.filter_by(user_id=target_user.id).delete()
        
        # Note: Other relationships like CompanyUpdateReaction, CompanyUpdateComment, 
        # CompanyUpdateView, and TransactionDocument have CASCADE DELETE so they'll
        # be automatically deleted when the user is deleted
        
        # Delete the user
        username = target_user.username
        db.session.delete(target_user)
        db.session.commit()
        
        flash(f'User {username} has been deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting user: {str(e)}', 'error')
    
    return redirect(url_for('org.members'))


# =============================================================================
# ORGANIZATION DELETION
# =============================================================================

@org_bp.route('/delete', methods=['GET', 'POST'])
@login_required
@org_owner_required
def request_deletion():
    """Org owner requests deletion - starts 30-day countdown."""
    org = current_user.organization
    
    if org.is_platform_admin:
        flash('Platform admin organization cannot be deleted.', 'error')
        return redirect(url_for('org.settings'))
    
    if request.method == 'GET':
        return render_template('organization/delete_confirm.html', org=org)
    
    # Verify confirmation
    confirm_text = request.form.get('confirm', '')
    if confirm_text.lower() != org.name.lower():
        flash('Please type the organization name exactly to confirm deletion.', 'error')
        return render_template('organization/delete_confirm.html', org=org)
    
    # Store owner email before invalidating sessions (for confirmation emails)
    owner_email = current_user.email
    
    # Set pending deletion status
    org.status = 'pending_deletion'
    org.deletion_scheduled_at = datetime.utcnow() + timedelta(days=30)
    
    # Invalidate ALL sessions for this org immediately
    org.session_invalidated_at = datetime.utcnow()
    
    db.session.commit()
    
    # TODO: Send confirmation email with cancel link
    # send_deletion_confirmation_email(org, owner_email)
    
    # Log out current user (their session is now invalid anyway)
    logout_user()
    
    flash('Deletion scheduled for 30 days from now. Check your email for details.', 'info')
    return redirect(url_for('auth.login'))


@org_bp.route('/cancel-deletion', methods=['POST'])
@login_required
@org_owner_required
def cancel_deletion():
    """Cancel pending deletion."""
    org = current_user.organization
    
    if org.status != 'pending_deletion':
        flash('Organization is not pending deletion.', 'error')
        return redirect(url_for('org.settings'))
    
    org.status = 'active'
    org.deletion_scheduled_at = None
    # Don't clear session_invalidated_at - sessions remain valid from when they were created
    
    db.session.commit()
    
    flash('Deletion cancelled. Your organization is active again.', 'success')
    return redirect(url_for('org.settings'))


# =============================================================================
# UPGRADE/BILLING (Placeholder)
# =============================================================================

@org_bp.route('/upgrade')
@login_required
@org_admin_required
def upgrade():
    """Show upgrade options."""
    org = current_user.organization
    
    if org.subscription_tier != 'free':
        flash('You are already on a paid plan.', 'info')
        return redirect(url_for('org.settings'))
    
    return render_template('organization/upgrade.html', org=org)


@org_bp.route('/usage')
@login_required
def usage():
    """View organization usage and limits."""
    org = current_user.organization
    
    return render_template('organization/usage.html', org=org)
