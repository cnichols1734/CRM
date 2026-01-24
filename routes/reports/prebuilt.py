# routes/reports/prebuilt.py
"""
Pre-built report configurations for real estate professionals.
Each report has a unique ID, display info, and query configuration.
"""

# Report categories for organization
REPORT_CATEGORIES = {
    'pipeline': {
        'name': 'Pipeline Reports',
        'description': 'Track your transaction pipeline and deals',
        'icon': 'fa-funnel-dollar',
        'color': 'blue'
    },
    'performance': {
        'name': 'Performance Reports',
        'description': 'Measure agent productivity and success metrics',
        'icon': 'fa-trophy',
        'color': 'amber'
    },
    'contacts': {
        'name': 'Contact Reports',
        'description': 'Analyze your contact database and engagement',
        'icon': 'fa-users',
        'color': 'emerald'
    },
    'activity': {
        'name': 'Activity Reports',
        'description': 'Track tasks, calls, and interactions',
        'icon': 'fa-chart-line',
        'color': 'purple'
    },
    'documents': {
        'name': 'Document Reports',
        'description': 'Monitor document and signing status',
        'icon': 'fa-file-signature',
        'color': 'rose'
    }
}

# All pre-built report definitions
PREBUILT_REPORTS = {
    # =========================================================================
    # PIPELINE REPORTS
    # =========================================================================
    'pipeline_overview': {
        'id': 'pipeline_overview',
        'name': 'Transaction Pipeline Overview',
        'description': 'See all your deals grouped by status with commission values',
        'category': 'pipeline',
        'icon': 'fa-layer-group',
        'data_source': 'transactions',
        'chart_type': 'bar',
        'group_by': 'status',
        'aggregate': 'count',
        'fields': ['street_address', 'city', 'status', 'transaction_type', 'expected_close_date', 'client_name'],
        'filters': [],
        'sort': {'field': 'status', 'dir': 'asc'},
        'show_totals': True
    },

    'deals_closing_soon': {
        'id': 'deals_closing_soon',
        'name': 'Deals Closing This Month',
        'description': 'Upcoming closings sorted by expected close date',
        'category': 'pipeline',
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
        'show_totals': False
    },

    'stale_deals': {
        'id': 'stale_deals',
        'name': 'Stale Deals (No Activity 14+ Days)',
        'description': 'Active deals that may need attention',
        'category': 'pipeline',
        'icon': 'fa-exclamation-triangle',
        'data_source': 'transactions',
        'chart_type': None,
        'group_by': None,
        'aggregate': None,
        'fields': ['street_address', 'city', 'client_name', 'status', 'days_since_update', 'updated_at'],
        'filters': [
            {'field': 'status', 'op': 'in', 'value': ['preparing_to_list', 'active', 'under_contract']},
            {'field': 'days_since_update', 'op': 'gte', 'value': 14}
        ],
        'sort': {'field': 'days_since_update', 'dir': 'desc'},
        'show_totals': False,
        'highlight_rule': {'field': 'days_since_update', 'threshold': 30, 'color': 'red'}
    },

    # =========================================================================
    # PERFORMANCE REPORTS
    # =========================================================================
    'agent_scorecard': {
        'id': 'agent_scorecard',
        'name': 'Agent Scorecard',
        'description': 'Key performance metrics: closed deals, revenue, task completion',
        'category': 'performance',
        'icon': 'fa-chart-bar',
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
            {'key': 'pipeline_value', 'label': 'Pipeline Value', 'icon': 'fa-dollar-sign', 'color': 'blue', 'format': 'currency'},
            {'key': 'active_deals', 'label': 'Active Deals', 'icon': 'fa-home', 'color': 'amber'},
            {'key': 'tasks_completed_month', 'label': 'Tasks This Month', 'icon': 'fa-check-circle', 'color': 'purple'}
        ]
    },

    'close_rate_trend': {
        'id': 'close_rate_trend',
        'name': 'Monthly Close Rate Trend',
        'description': '12-month trend of closed vs cancelled transactions',
        'category': 'performance',
        'icon': 'fa-chart-line',
        'data_source': 'transactions',
        'chart_type': 'line',
        'group_by': 'month',
        'aggregate': 'count',
        'fields': ['month', 'closed_count', 'cancelled_count', 'close_rate'],
        'filters': [
            {'field': 'created_at', 'op': 'last_12_months'}
        ],
        'sort': {'field': 'month', 'dir': 'asc'},
        'show_totals': False
    },

    'transaction_type_distribution': {
        'id': 'transaction_type_distribution',
        'name': 'Transaction Type Distribution',
        'description': 'Breakdown of transactions by type (Seller, Buyer, etc.)',
        'category': 'performance',
        'icon': 'fa-pie-chart',
        'data_source': 'transactions',
        'chart_type': 'donut',
        'group_by': 'transaction_type',
        'aggregate': 'count',
        'fields': ['transaction_type', 'count', 'percentage'],
        'filters': [],
        'sort': {'field': 'count', 'dir': 'desc'},
        'show_totals': True
    },

    # =========================================================================
    # CONTACT REPORTS
    # =========================================================================
    'contact_engagement': {
        'id': 'contact_engagement',
        'name': 'Contact Engagement Health',
        'description': 'See which contacts are hot, warm, or cold based on last contact',
        'category': 'contacts',
        'icon': 'fa-heartbeat',
        'data_source': 'contacts',
        'chart_type': 'donut',
        'group_by': 'engagement_status',
        'aggregate': 'count',
        'fields': ['full_name', 'email', 'phone', 'last_contact_date', 'days_since_contact', 'engagement_status', 'potential_commission'],
        'filters': [],
        'sort': {'field': 'days_since_contact', 'dir': 'desc'},
        'show_totals': True,
        'color_rules': {
            'engagement_status': {
                'hot': 'emerald',
                'warm': 'amber',
                'cold': 'red'
            }
        }
    },

    'high_value_stale': {
        'id': 'high_value_stale',
        'name': 'High-Value Stale Contacts',
        'description': 'Contacts with $10k+ potential commission not contacted in 30+ days',
        'category': 'contacts',
        'icon': 'fa-gem',
        'data_source': 'contacts',
        'chart_type': None,
        'group_by': None,
        'aggregate': None,
        'fields': ['full_name', 'email', 'phone', 'potential_commission', 'last_contact_date', 'days_since_contact', 'groups'],
        'filters': [
            {'field': 'potential_commission', 'op': 'gte', 'value': 10000},
            {'field': 'days_since_contact', 'op': 'gte', 'value': 30}
        ],
        'sort': {'field': 'potential_commission', 'dir': 'desc'},
        'show_totals': True
    },

    'contact_group_distribution': {
        'id': 'contact_group_distribution',
        'name': 'Contact Group Distribution',
        'description': 'See how your contacts are distributed across groups',
        'category': 'contacts',
        'icon': 'fa-users-cog',
        'data_source': 'contact_groups',
        'chart_type': 'donut',
        'group_by': 'group_name',
        'aggregate': 'count',
        'fields': ['group_name', 'category', 'count'],
        'filters': [],
        'sort': {'field': 'count', 'dir': 'desc'},
        'show_totals': True
    },

    'new_contacts': {
        'id': 'new_contacts',
        'name': 'New Contacts This Period',
        'description': 'Recently added contacts with their source',
        'category': 'contacts',
        'icon': 'fa-user-plus',
        'data_source': 'contacts',
        'chart_type': 'bar',
        'group_by': 'created_date',
        'aggregate': 'count',
        'fields': ['full_name', 'email', 'phone', 'created_at', 'groups', 'created_by'],
        'filters': [
            {'field': 'created_at', 'op': 'this_month'}
        ],
        'sort': {'field': 'created_at', 'dir': 'desc'},
        'show_totals': True
    },

    # =========================================================================
    # ACTIVITY REPORTS
    # =========================================================================
    'task_completion': {
        'id': 'task_completion',
        'name': 'Task Completion Report',
        'description': 'Weekly breakdown of completed, pending, and overdue tasks',
        'category': 'activity',
        'icon': 'fa-tasks',
        'data_source': 'tasks',
        'chart_type': 'stacked_bar',
        'group_by': 'week',
        'aggregate': 'count',
        'fields': ['week', 'completed', 'pending', 'overdue'],
        'filters': [
            {'field': 'created_at', 'op': 'last_8_weeks'}
        ],
        'sort': {'field': 'week', 'dir': 'asc'},
        'show_totals': True
    },

    'interaction_log': {
        'id': 'interaction_log',
        'name': 'Interaction Log',
        'description': 'Recent calls, emails, and meetings with contacts',
        'category': 'activity',
        'icon': 'fa-history',
        'data_source': 'interactions',
        'chart_type': None,
        'group_by': None,
        'aggregate': None,
        'fields': ['contact_name', 'type', 'date', 'notes', 'follow_up_date'],
        'filters': [
            {'field': 'date', 'op': 'last_30_days'}
        ],
        'sort': {'field': 'date', 'dir': 'desc'},
        'show_totals': False
    },

    'overdue_tasks': {
        'id': 'overdue_tasks',
        'name': 'Overdue Tasks',
        'description': 'Tasks past their due date that need attention',
        'category': 'activity',
        'icon': 'fa-clock',
        'data_source': 'tasks',
        'chart_type': None,
        'group_by': None,
        'aggregate': None,
        'fields': ['subject', 'contact_name', 'type', 'due_date', 'days_overdue', 'priority'],
        'filters': [
            {'field': 'status', 'op': 'eq', 'value': 'pending'},
            {'field': 'due_date', 'op': 'lt', 'value': 'today'}
        ],
        'sort': {'field': 'priority', 'dir': 'desc'},
        'show_totals': False,
        'highlight_rule': {'field': 'days_overdue', 'threshold': 7, 'color': 'red'}
    },

    # =========================================================================
    # DOCUMENT REPORTS
    # =========================================================================
    'document_status': {
        'id': 'document_status',
        'name': 'Document Signing Status',
        'description': 'Track document completion across all transactions',
        'category': 'documents',
        'icon': 'fa-file-contract',
        'data_source': 'documents',
        'chart_type': 'stacked_bar',
        'group_by': 'status',
        'aggregate': 'count',
        'fields': ['document_name', 'transaction_address', 'status', 'sent_at', 'signers_progress'],
        'filters': [],
        'sort': {'field': 'status', 'dir': 'asc'},
        'show_totals': True
    },

    'pending_signatures': {
        'id': 'pending_signatures',
        'name': 'Pending Signatures',
        'description': 'Documents awaiting signer action',
        'category': 'documents',
        'icon': 'fa-signature',
        'data_source': 'signatures',
        'chart_type': None,
        'group_by': None,
        'aggregate': None,
        'fields': ['document_name', 'transaction_address', 'signer_name', 'signer_email', 'sent_at', 'days_waiting'],
        'filters': [
            {'field': 'status', 'op': 'in', 'value': ['pending', 'sent', 'viewed']}
        ],
        'sort': {'field': 'sent_at', 'dir': 'asc'},
        'show_totals': False,
        'highlight_rule': {'field': 'days_waiting', 'threshold': 3, 'color': 'amber'}
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
