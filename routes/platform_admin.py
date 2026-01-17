# routes/platform_admin.py
"""
Platform admin routes for Origen super admins.
Queries ONLY organization_metrics table for aggregate data - NO direct tenant table queries.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from datetime import datetime
from models import db, Organization, OrganizationMetrics, PlatformAuditLog, User
from services.tenant_service import platform_admin_required, is_platform_admin
from sqlalchemy import func
from tier_config.tier_limits import TIER_DEFAULTS, apply_tier_defaults

platform_bp = Blueprint('platform', __name__, url_prefix='/platform')


def log_platform_action(action: str, target_org_id: int = None, details: dict = None):
    """Log a platform admin action."""
    log = PlatformAuditLog(
        admin_user_id=current_user.id,
        target_org_id=target_org_id,
        action=action,
        details=details or {},
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string[:500] if request.user_agent else None
    )
    db.session.add(log)


# =============================================================================
# DASHBOARD (Aggregates Only)
# =============================================================================

@platform_bp.route('/dashboard')
@login_required
@platform_admin_required
def dashboard():
    """
    Platform dashboard - aggregates ONLY.
    NEVER query tenant tables (contact, task, transaction) directly.
    """
    
    # Organization counts (from organizations table, not tenant data)
    total_orgs = Organization.query.filter(
        Organization.is_platform_admin == False
    ).count()
    
    pending_approval = Organization.query.filter_by(
        status='pending_approval'
    ).count()
    
    active_orgs = Organization.query.filter_by(
        status='active',
        is_platform_admin=False
    ).count()
    
    pending_deletion = Organization.query.filter_by(
        status='pending_deletion'
    ).count()
    
    # Get aggregates from organization_metrics table ONLY
    totals = db.session.query(
        func.sum(OrganizationMetrics.user_count).label('users'),
        func.sum(OrganizationMetrics.contact_count).label('contacts'),
        func.sum(OrganizationMetrics.transaction_count).label('transactions')
    ).first()
    
    # Org list - metadata only, NO PII
    orgs = db.session.query(
        Organization.id,
        Organization.name,
        Organization.slug,
        Organization.subscription_tier,
        Organization.status,
        Organization.created_at,
        Organization.max_users,
        OrganizationMetrics.user_count,
        OrganizationMetrics.contact_count,
        OrganizationMetrics.last_user_login_at
    ).outerjoin(
        OrganizationMetrics,
        Organization.id == OrganizationMetrics.organization_id
    ).filter(
        Organization.is_platform_admin == False
    ).order_by(Organization.created_at.desc()).limit(50).all()
    
    return render_template('platform_admin/dashboard.html',
        total_orgs=total_orgs,
        pending_approval=pending_approval,
        active_orgs=active_orgs,
        pending_deletion=pending_deletion,
        total_users=totals.users or 0,
        total_contacts=totals.contacts or 0,
        total_transactions=totals.transactions or 0,
        orgs=orgs
    )


# =============================================================================
# ORGANIZATION APPROVAL
# =============================================================================

@platform_bp.route('/pending')
@login_required
@platform_admin_required
def pending_orgs():
    """View organizations pending approval."""
    orgs = Organization.query.filter_by(
        status='pending_approval'
    ).order_by(Organization.created_at.asc()).all()
    
    # Get owner email for each org (just email, minimal PII)
    org_owners = {}
    for org in orgs:
        owner = org.users.filter_by(org_role='owner').first()
        if owner:
            org_owners[org.id] = owner.email
    
    return render_template('platform_admin/pending.html',
                          orgs=orgs,
                          org_owners=org_owners)


@platform_bp.route('/orgs/<int:org_id>/approve', methods=['POST'])
@login_required
@platform_admin_required
def approve_org(org_id):
    """Approve a pending organization."""
    org = Organization.query.get_or_404(org_id)
    
    if org.status != 'pending_approval':
        flash('Organization is not pending approval.', 'error')
        return redirect(url_for('platform.pending_orgs'))
    
    org.status = 'active'
    org.approved_at = datetime.utcnow()
    org.approved_by_id = current_user.id
    
    # Log action
    log_platform_action('org_approved', org.id, {'org_name': org.name})
    
    db.session.commit()
    
    # Notify org owner via email
    from services.org_notifications import send_org_approved_email
    owner = org.users.filter_by(org_role='owner').first()
    if owner:
        email_sent = send_org_approved_email(org, owner.email)
        if email_sent:
            flash(f'Organization "{org.name}" approved. Approval email sent to {owner.email}.', 'success')
        else:
            flash(f'Organization "{org.name}" approved, but email notification failed.', 'warning')
    else:
        flash(f'Organization "{org.name}" approved.', 'success')
    return redirect(url_for('platform.pending_orgs'))


@platform_bp.route('/orgs/<int:org_id>/reject', methods=['POST'])
@login_required
@platform_admin_required
def reject_org(org_id):
    """Reject a pending organization."""
    org = Organization.query.get_or_404(org_id)
    
    if org.status != 'pending_approval':
        flash('Organization is not pending approval.', 'error')
        return redirect(url_for('platform.pending_orgs'))
    
    reason = request.form.get('reason', 'No reason provided')
    
    # Mark as rejected (we'll soft delete)
    org.status = 'rejected'
    
    # Log action
    log_platform_action('org_rejected', org.id, {'org_name': org.name, 'reason': reason})
    
    db.session.commit()
    
    # Notify org owner via email with rejection reason
    from services.org_notifications import send_org_rejected_email
    owner = org.users.filter_by(org_role='owner').first()
    reason = request.form.get('reason', '')
    if owner:
        send_org_rejected_email(org, owner.email, reason)
    
    flash(f'Organization "{org.name}" rejected.', 'info')
    return redirect(url_for('platform.pending_orgs'))


# =============================================================================
# ORGANIZATION MANAGEMENT
# =============================================================================

@platform_bp.route('/orgs/<int:org_id>')
@login_required
@platform_admin_required
def view_org(org_id):
    """View organization details (metadata only, no PII)."""
    org = Organization.query.get_or_404(org_id)
    
    # Get metrics
    metrics = OrganizationMetrics.query.filter_by(organization_id=org_id).first()
    
    # Get recent audit logs for this org
    audit_logs = PlatformAuditLog.query.filter_by(
        target_org_id=org_id
    ).order_by(PlatformAuditLog.created_at.desc()).limit(20).all()
    
    return render_template('platform_admin/org_detail.html',
                          org=org,
                          metrics=metrics,
                          audit_logs=audit_logs,
                          tier_defaults=TIER_DEFAULTS)


@platform_bp.route('/orgs/<int:org_id>/suspend', methods=['POST'])
@login_required
@platform_admin_required
def suspend_org(org_id):
    """Suspend an organization."""
    org = Organization.query.get_or_404(org_id)
    
    if org.is_platform_admin:
        flash('Cannot suspend platform admin organization.', 'error')
        return redirect(url_for('platform.view_org', org_id=org_id))
    
    reason = request.form.get('reason', 'No reason provided')
    
    org.status = 'suspended'
    org.session_invalidated_at = datetime.utcnow()  # Force logout all users
    
    log_platform_action('org_suspended', org.id, {
        'org_name': org.name,
        'reason': reason
    })
    
    db.session.commit()
    
    flash(f'Organization "{org.name}" suspended.', 'warning')
    return redirect(url_for('platform.view_org', org_id=org_id))


@platform_bp.route('/orgs/<int:org_id>/reactivate', methods=['POST'])
@login_required
@platform_admin_required
def reactivate_org(org_id):
    """Reactivate a suspended organization."""
    org = Organization.query.get_or_404(org_id)
    
    if org.status not in ('suspended', 'pending_deletion'):
        flash('Organization is not suspended.', 'error')
        return redirect(url_for('platform.view_org', org_id=org_id))
    
    org.status = 'active'
    org.deletion_scheduled_at = None
    
    log_platform_action('org_reactivated', org.id, {'org_name': org.name})
    
    db.session.commit()
    
    flash(f'Organization "{org.name}" reactivated.', 'success')
    return redirect(url_for('platform.view_org', org_id=org_id))


@platform_bp.route('/orgs/<int:org_id>/update-tier', methods=['POST'])
@login_required
@platform_admin_required
def update_org_tier(org_id):
    """Update organization subscription tier."""
    org = Organization.query.get_or_404(org_id)
    
    new_tier = request.form.get('tier')
    if new_tier not in TIER_DEFAULTS:
        flash('Invalid tier.', 'error')
        return redirect(url_for('platform.view_org', org_id=org_id))
    
    old_tier = org.subscription_tier
    
    # Apply tier defaults
    apply_tier_defaults(org, new_tier)
    
    # Allow custom overrides
    custom_max_users = request.form.get('max_users')
    if custom_max_users:
        try:
            org.max_users = int(custom_max_users) if custom_max_users != 'unlimited' else None
        except ValueError:
            pass
    
    log_platform_action('tier_changed', org.id, {
        'org_name': org.name,
        'old_tier': old_tier,
        'new_tier': new_tier
    })
    
    db.session.commit()
    
    flash(f'Organization "{org.name}" upgraded to {new_tier}.', 'success')
    return redirect(url_for('platform.view_org', org_id=org_id))


@platform_bp.route('/orgs/<int:org_id>/update-limits', methods=['POST'])
@login_required
@platform_admin_required
def update_org_limits(org_id):
    """Update organization limits (custom overrides)."""
    org = Organization.query.get_or_404(org_id)
    
    old_limits = {
        'max_users': org.max_users,
        'max_contacts': org.max_contacts,
        'can_invite_users': org.can_invite_users
    }
    
    # Update limits
    max_users = request.form.get('max_users')
    if max_users:
        org.max_users = int(max_users) if max_users != 'unlimited' else None
    
    max_contacts = request.form.get('max_contacts')
    if max_contacts:
        org.max_contacts = int(max_contacts) if max_contacts != 'unlimited' else None
    
    can_invite = request.form.get('can_invite_users')
    if can_invite is not None:
        org.can_invite_users = can_invite == 'true'
    
    log_platform_action('limits_changed', org.id, {
        'org_name': org.name,
        'old_limits': old_limits,
        'new_limits': {
            'max_users': org.max_users,
            'max_contacts': org.max_contacts,
            'can_invite_users': org.can_invite_users
        }
    })
    
    db.session.commit()
    
    flash(f'Limits updated for "{org.name}".', 'success')
    return redirect(url_for('platform.view_org', org_id=org_id))


# =============================================================================
# AUDIT LOG
# =============================================================================

@platform_bp.route('/audit-log')
@login_required
@platform_admin_required
def audit_log():
    """View platform audit log."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    logs = PlatformAuditLog.query.order_by(
        PlatformAuditLog.created_at.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('platform_admin/audit_log.html', logs=logs)


# =============================================================================
# METRICS REFRESH
# =============================================================================

@platform_bp.route('/refresh-metrics', methods=['POST'])
@login_required
@platform_admin_required
def refresh_metrics():
    """Manually trigger metrics refresh for all orgs."""
    from jobs.metrics_aggregator import update_all_org_metrics
    
    try:
        update_all_org_metrics()
        flash('Metrics refreshed successfully.', 'success')
    except Exception as e:
        flash(f'Error refreshing metrics: {str(e)}', 'error')
    
    return redirect(url_for('platform.dashboard'))
