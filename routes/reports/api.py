# routes/reports/api.py
"""
Report API endpoints for data fetching and export.
"""

import csv
from io import StringIO
from datetime import datetime

from flask import request, jsonify, Response, abort
from flask_login import login_required, current_user

from . import reports_bp
from .views import execute_report
from .prebuilt import get_report_by_id
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


@reports_bp.route('/api/data/<report_id>')
@login_required
@reports_access_required
def get_report_data(report_id):
    """Get report data as JSON for async loading."""
    date_range = request.args.get('date_range', 'this_month')
    
    # Get view mode - only admins can see all records
    can_view_all = is_org_admin()
    view_mode = request.args.get('view', 'my')
    if not can_view_all:
        view_mode = 'my'
    user_id = None if (can_view_all and view_mode == 'all') else current_user.id

    report_config = get_report_by_id(report_id)
    if not report_config:
        return jsonify({'error': 'Report not found'}), 404

    try:
        data = execute_report(report_id, date_range, user_id=user_id)
        return jsonify({
            'success': True,
            'data': data,
            'report': {
                'id': report_config['id'],
                'name': report_config['name'],
                'chart_type': report_config.get('chart_type')
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@reports_bp.route('/api/export/<report_id>')
@login_required
@reports_access_required
def export_report_csv(report_id):
    """Export report data as CSV."""
    date_range = request.args.get('date_range', 'this_month')
    
    # Get view mode - only admins can see all records
    can_view_all = is_org_admin()
    view_mode = request.args.get('view', 'my')
    if not can_view_all:
        view_mode = 'my'
    user_id = None if (can_view_all and view_mode == 'all') else current_user.id

    report_config = get_report_by_id(report_id)
    if not report_config:
        return jsonify({'error': 'Report not found'}), 404

    try:
        data = execute_report(report_id, date_range, user_id=user_id)
        rows = data.get('rows', [])

        if not rows:
            return jsonify({'error': 'No data to export'}), 400

        # Create CSV
        output = StringIO()
        writer = csv.writer(output)

        # Get headers from first row
        if rows:
            # Filter out internal fields
            headers = [k for k in rows[0].keys() if not k.startswith('_') and k != 'id']
            writer.writerow([h.replace('_', ' ').title() for h in headers])

            # Write data rows
            for row in rows:
                writer.writerow([row.get(h, '') for h in headers])

        output.seek(0)

        # Generate filename
        report_name = report_config['name'].lower().replace(' ', '_')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{report_name}_{timestamp}.csv"

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename={filename}',
                'Content-Type': 'text/csv; charset=utf-8'
            }
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500
