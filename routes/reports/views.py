# routes/reports/views.py
"""
Report page routes - landing page and report viewer.
"""

from flask import render_template, request, abort
from flask_login import login_required, current_user

from . import reports_bp
from .prebuilt import get_reports_by_category, get_report_by_id, REPORT_CATEGORIES
from services.report_service import report_service
from feature_flags import can_access_reports
from services.tenant_service import is_org_admin


def reports_access_required(f):
    """Decorator to check if user can access reports."""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not can_access_reports(current_user):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


@reports_bp.route('/')
@login_required
@reports_access_required
def landing():
    """Reports landing page with all report categories and live preview metrics."""
    categories = get_reports_by_category()
    
    # Get view mode for metrics
    can_view_all = is_org_admin()
    view_mode = request.args.get('view', 'my')
    if not can_view_all:
        view_mode = 'my'
    user_id = None if (can_view_all and view_mode == 'all') else current_user.id
    
    # Get live preview metrics for badges
    preview_metrics = report_service.get_report_preview_metrics(user_id=user_id)
    
    return render_template(
        'reports/landing.html',
        categories=categories,
        metrics=preview_metrics,
        can_view_all=can_view_all,
        view_mode=view_mode
    )


@reports_bp.route('/view/<report_id>')
@login_required
@reports_access_required
def view_report(report_id):
    """View a specific pre-built report."""
    report_config = get_report_by_id(report_id)
    if not report_config:
        abort(404)

    # Get date range from query params
    date_range = request.args.get('date_range', 'this_month')
    
    # Get view mode (all or my) - only admins/owners can see all records
    can_view_all = is_org_admin()
    view_mode = request.args.get('view', 'my')
    
    # Non-admins can only see their own data
    if not can_view_all:
        view_mode = 'my'
    
    # Determine user_id filter
    user_id = None if (can_view_all and view_mode == 'all') else current_user.id

    # Execute the report based on its ID
    report_data = execute_report(report_id, date_range, user_id=user_id)

    return render_template(
        'reports/viewer.html',
        report=report_config,
        report_data=report_data,
        date_range=date_range,
        categories=REPORT_CATEGORIES,
        can_view_all=can_view_all,
        view_mode=view_mode
    )


def execute_report(report_id, date_range='this_month', user_id=None):
    """Execute a pre-built report and return its data.
    
    Args:
        report_id: The report identifier
        date_range: Date range filter (e.g., 'this_month', 'this_year')
        user_id: If provided, filter to only this user's records. If None, show all org records.
    """
    # Map report IDs to service methods (streamlined 8 reports)
    report_methods = {
        # Revenue & Pipeline
        'pipeline_overview': lambda: report_service.get_pipeline_overview(date_range, user_id=user_id),
        'deals_closing_soon': lambda: report_service.get_deals_closing_soon(date_range, user_id=user_id),
        'at_risk_deals': lambda: report_service.get_at_risk_deals(user_id=user_id),
        
        # Follow-Up Priority
        'hot_leads_scorecard': lambda: report_service.get_hot_leads_scorecard(user_id=user_id),
        'overdue_tasks': lambda: report_service.get_overdue_tasks(user_id=user_id),
        'weekly_activity_summary': lambda: report_service.get_weekly_activity_summary(user_id=user_id),
        
        # Transaction Management
        'document_status': lambda: report_service.get_document_status(user_id=user_id),
        'agent_scorecard': lambda: report_service.get_agent_scorecard(user_id=user_id),
        
        # Legacy reports (kept for backwards compatibility during transition)
        'stale_deals': lambda: report_service.get_stale_deals(user_id=user_id),
        'contact_engagement': lambda: report_service.get_contact_engagement(date_range, user_id=user_id),
        'high_value_stale': lambda: report_service.get_high_value_stale_contacts(user_id=user_id),
    }

    if report_id in report_methods:
        return report_methods[report_id]()

    return {'rows': [], 'totals': {}}
