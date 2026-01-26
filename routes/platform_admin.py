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
    Platform dashboard with clear separation of Origen vs Customer data.
    Includes filtering and comprehensive metrics.
    """
    
    # Get filter from query params
    status_filter = request.args.get('status', 'all')
    tier_filter = request.args.get('tier', 'all')
    include_origen = request.args.get('include_origen', 'false') == 'true'
    
    # ==========================================================================
    # ORIGEN (Platform Admin) Stats - Always shown separately
    # ==========================================================================
    origen = Organization.query.filter_by(is_platform_admin=True).first()
    origen_metrics = OrganizationMetrics.query.filter_by(
        organization_id=origen.id
    ).first() if origen else None
    
    origen_stats = {
        'name': origen.name if origen else 'N/A',
        'users': origen_metrics.user_count if origen_metrics else 0,
        'contacts': origen_metrics.contact_count if origen_metrics else 0,
        'transactions': origen_metrics.transaction_count if origen_metrics else 0,
    }
    
    # ==========================================================================
    # CUSTOMER Organizations Stats (excluding Origen)
    # ==========================================================================
    
    # Base query for customer orgs
    customer_base = Organization.query.filter(Organization.is_platform_admin == False)
    
    # Counts by status
    total_customer_orgs = customer_base.count()
    active_customer_orgs = customer_base.filter_by(status='active').count()
    pending_approval_orgs = customer_base.filter_by(status='pending_approval').count()
    suspended_orgs = customer_base.filter_by(status='suspended').count()
    pending_deletion_orgs = customer_base.filter_by(status='pending_deletion').count()
    
    # Counts by tier
    free_tier_orgs = customer_base.filter_by(subscription_tier='free').count()
    pro_tier_orgs = customer_base.filter_by(subscription_tier='pro').count()
    enterprise_tier_orgs = customer_base.filter_by(subscription_tier='enterprise').count()
    
    # Customer metrics (excluding Origen)
    customer_metrics = db.session.query(
        func.sum(OrganizationMetrics.user_count).label('users'),
        func.sum(OrganizationMetrics.contact_count).label('contacts'),
        func.sum(OrganizationMetrics.transaction_count).label('transactions')
    ).join(
        Organization,
        OrganizationMetrics.organization_id == Organization.id
    ).filter(
        Organization.is_platform_admin == False
    ).first()
    
    customer_stats = {
        'users': customer_metrics.users or 0,
        'contacts': customer_metrics.contacts or 0,
        'transactions': customer_metrics.transactions or 0,
    }
    
    # ==========================================================================
    # COMBINED Stats (Platform Total)
    # ==========================================================================
    platform_stats = {
        'total_orgs': total_customer_orgs + (1 if origen else 0),
        'users': (origen_stats['users'] or 0) + (customer_stats['users'] or 0),
        'contacts': (origen_stats['contacts'] or 0) + (customer_stats['contacts'] or 0),
        'transactions': (origen_stats['transactions'] or 0) + (customer_stats['transactions'] or 0),
    }
    
    # ==========================================================================
    # Organization List with Filters
    # ==========================================================================
    org_query = db.session.query(
        Organization.id,
        Organization.name,
        Organization.slug,
        Organization.subscription_tier,
        Organization.status,
        Organization.created_at,
        Organization.max_users,
        Organization.max_contacts,
        Organization.is_platform_admin,
        OrganizationMetrics.user_count,
        OrganizationMetrics.contact_count,
        OrganizationMetrics.transaction_count,
        OrganizationMetrics.last_user_login_at
    ).outerjoin(
        OrganizationMetrics,
        Organization.id == OrganizationMetrics.organization_id
    )
    
    # Apply filters
    if not include_origen:
        org_query = org_query.filter(Organization.is_platform_admin == False)
    
    if status_filter != 'all':
        org_query = org_query.filter(Organization.status == status_filter)
    
    if tier_filter != 'all':
        org_query = org_query.filter(Organization.subscription_tier == tier_filter)
    
    orgs = org_query.order_by(Organization.created_at.desc()).all()
    
    # Get owner email for each org (the admin who created it)
    org_owners = {}
    org_ids = [org.id for org in orgs]
    if org_ids:
        owners = User.query.filter(
            User.organization_id.in_(org_ids),
            User.org_role == 'owner'
        ).all()
        for owner in owners:
            org_owners[owner.organization_id] = owner.email
    
    return render_template('platform_admin/dashboard.html',
        # Origen stats
        origen_stats=origen_stats,
        # Customer stats
        total_customer_orgs=total_customer_orgs,
        active_customer_orgs=active_customer_orgs,
        pending_approval_orgs=pending_approval_orgs,
        suspended_orgs=suspended_orgs,
        pending_deletion_orgs=pending_deletion_orgs,
        free_tier_orgs=free_tier_orgs,
        pro_tier_orgs=pro_tier_orgs,
        enterprise_tier_orgs=enterprise_tier_orgs,
        customer_stats=customer_stats,
        # Platform totals
        platform_stats=platform_stats,
        # Filters
        status_filter=status_filter,
        tier_filter=tier_filter,
        include_origen=include_origen,
        # Org list
        orgs=orgs,
        org_owners=org_owners
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
    
    # Import early to avoid issues after potential rollback
    from services.tenant_service import (
        create_default_groups_for_org,
        create_default_task_types_for_org,
        create_default_transaction_types_for_org
    )
    from services.org_notifications import send_org_approved_email
    import logging
    
    # Store org info before potential session issues
    org_name = org.name
    owner = org.users.filter_by(org_role='owner').first()
    owner_email = owner.email if owner else None
    
    try:
        # Update org status
        org.status = 'active'
        org.approved_at = datetime.utcnow()
        org.approved_by_id = current_user.id
        
        # Log action
        log_platform_action('org_approved', org.id, {'org_name': org_name})
        
        db.session.commit()
        
        # Create default data for the new org (these functions are now idempotent)
        # Create default contact groups
        try:
            created_groups = create_default_groups_for_org(org.id)
            log_platform_action('org_groups_created', org.id, {'groups_count': len(created_groups)})
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.error(f"Failed to create default groups for org {org.id}: {e}", exc_info=True)
        
        # Create default task types and subtypes
        try:
            created_task_types = create_default_task_types_for_org(org.id)
            log_platform_action('org_task_types_created', org.id, {'task_types_count': len(created_task_types)})
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.error(f"Failed to create default task types for org {org.id}: {e}", exc_info=True)
        
        # Create default transaction types
        try:
            created_tx_types = create_default_transaction_types_for_org(org.id)
            log_platform_action('org_transaction_types_created', org.id, {'transaction_types_count': len(created_tx_types)})
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.error(f"Failed to create default transaction types for org {org.id}: {e}", exc_info=True)
        
        # Notify org owner via email
        if owner_email:
            email_sent = send_org_approved_email(org, owner_email)
            if email_sent:
                flash(f'Organization "{org_name}" approved. Approval email sent to {owner_email}.', 'success')
            else:
                flash(f'Organization "{org_name}" approved, but email notification failed.', 'warning')
        else:
            flash(f'Organization "{org_name}" approved.', 'success')
            
    except Exception as e:
        db.session.rollback()
        logging.error(f"Failed to approve organization {org_id}: {e}", exc_info=True)
        flash(f'Error approving organization: {str(e)}', 'error')
    
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
    
    # Get owner email (admin who created the org)
    owner = org.users.filter_by(org_role='owner').first()
    owner_email = owner.email if owner else None
    
    # Get recent audit logs for this org
    audit_logs = PlatformAuditLog.query.filter_by(
        target_org_id=org_id
    ).order_by(PlatformAuditLog.created_at.desc()).limit(20).all()
    
    return render_template('platform_admin/org_detail.html',
                          org=org,
                          metrics=metrics,
                          owner_email=owner_email,
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
# ORGANIZATION REPAIR
# =============================================================================

@platform_bp.route('/orgs/<int:org_id>/repair', methods=['POST'])
@login_required
@platform_admin_required
def repair_org(org_id):
    """
    Repair an organization by ensuring all default data is created.
    Useful for orgs that failed during the approval process.
    All creation functions are idempotent - safe to run multiple times.
    Also sends the approval email if it wasn't sent before.
    """
    org = Organization.query.get_or_404(org_id)
    
    from services.tenant_service import (
        create_default_groups_for_org,
        create_default_task_types_for_org,
        create_default_transaction_types_for_org
    )
    from services.org_notifications import send_org_approved_email
    import logging
    
    results = []
    errors = []
    
    # Create default contact groups
    try:
        created_groups = create_default_groups_for_org(org.id)
        results.append(f"Contact groups: {len(created_groups)}")
        log_platform_action('org_repair_groups', org.id, {'groups_count': len(created_groups)})
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        errors.append(f"Groups: {str(e)}")
        logging.error(f"Failed to repair groups for org {org.id}: {e}", exc_info=True)
    
    # Create default task types and subtypes
    try:
        created_task_types = create_default_task_types_for_org(org.id)
        results.append(f"Task types: {len(created_task_types)}")
        log_platform_action('org_repair_task_types', org.id, {'task_types_count': len(created_task_types)})
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        errors.append(f"Task types: {str(e)}")
        logging.error(f"Failed to repair task types for org {org.id}: {e}", exc_info=True)
    
    # Create default transaction types
    try:
        created_tx_types = create_default_transaction_types_for_org(org.id)
        results.append(f"Transaction types: {len(created_tx_types)}")
        log_platform_action('org_repair_transaction_types', org.id, {'transaction_types_count': len(created_tx_types)})
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        errors.append(f"Transaction types: {str(e)}")
        logging.error(f"Failed to repair transaction types for org {org.id}: {e}", exc_info=True)
    
    # Send approval email to org owner (in case it didn't send before)
    owner = org.users.filter_by(org_role='owner').first()
    email_sent = False
    if owner:
        try:
            email_sent = send_org_approved_email(org, owner.email)
            if email_sent:
                results.append(f"Approval email sent to {owner.email}")
                log_platform_action('org_repair_email_sent', org.id, {'owner_email': owner.email})
            else:
                errors.append("Approval email failed to send")
        except Exception as e:
            errors.append(f"Email: {str(e)}")
            logging.error(f"Failed to send approval email for org {org.id}: {e}", exc_info=True)
    else:
        errors.append("No owner found - could not send approval email")
    
    if errors:
        flash(f'Repair completed with issues. {", ".join(results)}. Issues: {", ".join(errors)}', 'warning')
    else:
        flash(f'Organization "{org.name}" repaired successfully. {", ".join(results)}.', 'success')
    
    return redirect(url_for('platform.view_org', org_id=org_id))


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
