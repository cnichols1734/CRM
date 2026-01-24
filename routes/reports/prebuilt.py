# routes/reports/prebuilt.py
"""
Pre-built report configurations for real estate professionals.
Each report has a unique ID, display info, and query configuration.
Streamlined to 8 high-impact reports across 3 categories.
"""

# Report categories for organization (3 focused categories)
REPORT_CATEGORIES = {
    'revenue': {
        'name': 'Revenue & Pipeline',
        'description': 'Track your deals and commission pipeline',
        'icon': 'fa-dollar-sign',
        'color': 'emerald'
    },
    'followup': {
        'name': 'Follow-Up Priority',
        'description': 'Who to contact and what needs attention',
        'icon': 'fa-bullseye',
        'color': 'amber'
    },
    'management': {
        'name': 'Transaction Management',
        'description': 'Documents, signatures, and performance',
        'icon': 'fa-clipboard-check',
        'color': 'blue'
    }
}

# All pre-built report definitions (8 focused reports)
PREBUILT_REPORTS = {
    # =========================================================================
    # REVENUE & PIPELINE (3 reports)
    # =========================================================================
    'pipeline_overview': {
        'id': 'pipeline_overview',
        'name': 'Pipeline Dashboard',
        'description': 'All deals by status with commission values',
        'category': 'revenue',
        'icon': 'fa-layer-group',
        'data_source': 'transactions',
        'chart_type': 'bar',
        'group_by': 'status',
        'aggregate': 'count',
        'fields': ['street_address', 'city', 'status', 'transaction_type', 'expected_close_date', 'client_name'],
        'filters': [],
        'sort': {'field': 'status', 'dir': 'asc'},
        'show_totals': True,
        'preview_metric': 'active_deals'
    },

    'deals_closing_soon': {
        'id': 'deals_closing_soon',
        'name': 'Deals Closing Soon',
        'description': 'Upcoming closings in the next 30/60/90 days',
        'category': 'revenue',
        'icon': 'fa-calendar-check',
        'data_source': 'transactions',
        'chart_type': None,
        'group_by': None,
        'aggregate': None,
        'fields': ['street_address', 'city', 'client_name', 'expected_close_date', 'status', 'days_to_close'],
        'filters': [
            {'field': 'expected_close_date', 'op': 'this_month'},
            {'field': 'status', 'op': 'not_in', 'value': ['closed', 'cancelled']}
        ],
        'sort': {'field': 'expected_close_date', 'dir': 'asc'},
        'show_totals': False,
        'preview_metric': 'closing_soon'
    },

    'at_risk_deals': {
        'id': 'at_risk_deals',
        'name': 'At-Risk Deals',
        'description': 'Stale deals, missing docs, or past expected close',
        'category': 'revenue',
        'icon': 'fa-exclamation-triangle',
        'data_source': 'transactions',
        'chart_type': None,
        'group_by': None,
        'aggregate': None,
        'fields': ['street_address', 'city', 'client_name', 'status', 'risk_reason', 'days_since_update', 'expected_close_date'],
        'filters': [],
        'sort': {'field': 'days_since_update', 'dir': 'desc'},
        'show_totals': False,
        'highlight_rule': {'field': 'days_since_update', 'threshold': 30, 'color': 'red'},
        'preview_metric': 'at_risk',
        'alert_if_nonzero': True
    },

    # =========================================================================
    # FOLLOW-UP PRIORITY (3 reports)
    # =========================================================================
    'hot_leads_scorecard': {
        'id': 'hot_leads_scorecard',
        'name': 'Hot Leads Scorecard',
        'description': 'Priority contacts ranked by value × days since contact',
        'category': 'followup',
        'icon': 'fa-fire',
        'data_source': 'contacts',
        'chart_type': 'donut',
        'group_by': 'engagement_status',
        'aggregate': 'count',
        'fields': ['full_name', 'email', 'phone', 'potential_commission', 'last_contact_date', 'days_since_contact', 'priority_score', 'groups'],
        'filters': [],
        'sort': {'field': 'priority_score', 'dir': 'desc'},
        'show_totals': True,
        'color_rules': {
            'engagement_status': {
                'hot': 'emerald',
                'warm': 'amber',
                'cold': 'red'
            }
        },
        'preview_metric': 'priority_contacts'
    },

    'overdue_tasks': {
        'id': 'overdue_tasks',
        'name': 'Action Required',
        'description': 'Overdue tasks sorted by contact value',
        'category': 'followup',
        'icon': 'fa-clock',
        'data_source': 'tasks',
        'chart_type': None,
        'group_by': None,
        'aggregate': None,
        'fields': ['subject', 'contact_name', 'type', 'due_date', 'days_overdue', 'priority', 'potential_commission'],
        'filters': [
            {'field': 'status', 'op': 'eq', 'value': 'pending'},
            {'field': 'due_date', 'op': 'lt', 'value': 'today'}
        ],
        'sort': {'field': 'priority', 'dir': 'desc'},
        'show_totals': False,
        'highlight_rule': {'field': 'days_overdue', 'threshold': 7, 'color': 'red'},
        'preview_metric': 'overdue_tasks',
        'alert_if_nonzero': True
    },

    'weekly_activity_summary': {
        'id': 'weekly_activity_summary',
        'name': 'Weekly Digest',
        'description': 'Activity summary and untouched high-value contacts',
        'category': 'followup',
        'icon': 'fa-chart-bar',
        'data_source': 'interactions',
        'chart_type': 'bar',
        'group_by': 'type',
        'aggregate': 'count',
        'fields': ['total_interactions', 'contacts_touched', 'untouched_high_value', 'interaction_breakdown'],
        'filters': [
            {'field': 'date', 'op': 'last_7_days'}
        ],
        'sort': None,
        'show_totals': True,
        'preview_metric': 'weekly_touches'
    },

    # =========================================================================
    # TRANSACTION MANAGEMENT (2 reports)
    # =========================================================================
    'document_status': {
        'id': 'document_status',
        'name': 'Document Status Center',
        'description': 'All documents and pending signatures in one view',
        'category': 'management',
        'icon': 'fa-file-signature',
        'data_source': 'documents',
        'chart_type': 'stacked_bar',
        'group_by': 'status',
        'aggregate': 'count',
        'fields': ['document_name', 'transaction_address', 'status', 'sent_at', 'signers_progress', 'days_waiting'],
        'filters': [],
        'sort': {'field': 'days_waiting', 'dir': 'desc'},
        'show_totals': True,
        'highlight_rule': {'field': 'days_waiting', 'threshold': 3, 'color': 'amber'},
        'preview_metric': 'pending_docs',
        'alert_if_nonzero': True
    },

    'agent_scorecard': {
        'id': 'agent_scorecard',
        'name': 'Agent Performance',
        'description': 'YTD closings, active deals, and key metrics',
        'category': 'management',
        'icon': 'fa-trophy',
        'data_source': 'scorecard',
        'chart_type': 'kpi',
        'group_by': None,
        'aggregate': None,
        'fields': [],
        'filters': [],
        'sort': None,
        'show_totals': False,
        'kpi_metrics': [
            {'key': 'closed_ytd', 'label': 'Closed YTD', 'icon': 'fa-handshake', 'color': 'emerald'},
            {'key': 'active_deals', 'label': 'Active Deals', 'icon': 'fa-home', 'color': 'blue'},
            {'key': 'total_contacts', 'label': 'Total Contacts', 'icon': 'fa-users', 'color': 'amber'},
            {'key': 'overdue_tasks', 'label': 'Overdue Tasks', 'icon': 'fa-exclamation-circle', 'color': 'red'}
        ],
        'preview_metric': 'closed_ytd'
    }
}


def get_reports_by_category():
    """Return reports organized by category."""
    result = {}
    for cat_id, cat_info in REPORT_CATEGORIES.items():
        result[cat_id] = {
            **cat_info,
            'reports': []
        }

    for report_id, report in PREBUILT_REPORTS.items():
        category = report['category']
        if category in result:
            result[category]['reports'].append(report)

    return result


def get_report_by_id(report_id):
    """Get a specific report configuration by ID."""
    return PREBUILT_REPORTS.get(report_id)
