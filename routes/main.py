from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user
from models import db, Contact, ContactGroup, Task, User, CompanyUpdate, Transaction, TransactionParticipant, contact_groups as contact_groups_table
from feature_flags import is_enabled, can_access_transactions, org_has_feature, feature_required
from services.tenant_service import org_query, can_view_all_org_data
from datetime import datetime, timedelta, timezone, date
import pytz
import os
import psutil
import time
import subprocess
import requests
from sqlalchemy import func, extract, text
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import or_, case

main_bp = Blueprint('main', __name__)

# Track when the app started for uptime calculation
_app_start_time = datetime.now(timezone.utc)

# Cache the git commit SHA (only read once at startup)
_git_commit_sha = None


def _get_git_commit():
    """Get the current git commit SHA. Cached after first call."""
    global _git_commit_sha
    if _git_commit_sha is not None:
        return _git_commit_sha
    
    # Try environment variable first (set by Railway/CI)
    _git_commit_sha = os.environ.get('RAILWAY_GIT_COMMIT_SHA', 
                      os.environ.get('GIT_COMMIT_SHA', 
                      os.environ.get('COMMIT_SHA', '')))
    
    # Fall back to git command if not set
    if not _git_commit_sha:
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--short', 'HEAD'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                _git_commit_sha = result.stdout.strip()
        except Exception:
            _git_commit_sha = 'unknown'
    
    # Truncate to short hash if it's a full SHA
    if len(_git_commit_sha) > 12:
        _git_commit_sha = _git_commit_sha[:7]
    
    return _git_commit_sha


# =============================================================================
# HEALTH CHECK ENDPOINT (Public - for Railway monitoring)
# =============================================================================

# Cache external dependency checks (expensive API calls) - refresh every 60s
_external_cache = {"data": {}, "warnings": [], "last_check": None}
_EXTERNAL_CHECK_INTERVAL = 60  # seconds

@main_bp.route('/health')
def health_check():
    """
    Health check endpoint for Railway monitoring.
    Returns comprehensive system telemetry: database, memory, CPU, process info, and version.
    External dependency checks are cached for 60s to avoid excessive API calls.
    """
    import sys
    import platform

    status = "healthy"
    warnings = []
    checks = {}

    # Get git commit SHA for version verification
    commit_sha = _get_git_commit()

    # Check database connectivity with latency measurement
    try:
        start_time = time.time()
        db.session.execute(text('SELECT 1'))
        latency_ms = round((time.time() - start_time) * 1000, 2)

        checks['database'] = {
            "status": "connected",
            "latency_ms": latency_ms
        }

        # Warn if latency is high
        if latency_ms > 500:
            warnings.append(f"Database latency high: {latency_ms}ms")
    except Exception as e:
        checks['database'] = {"status": "error", "message": str(e)}
        status = "unhealthy"

    # Process-level telemetry
    try:
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        rss_mb = round(memory_info.rss / 1024 / 1024, 2)

        # CPU percent (non-blocking, interval=None uses cached value)
        cpu_percent = process.cpu_percent(interval=None)

        checks['memory'] = {
            "rss_mb": rss_mb,
            "vms_mb": round(memory_info.vms / 1024 / 1024, 2),
        }

        checks['cpu'] = {
            "process_percent": cpu_percent,
            "system_percent": psutil.cpu_percent(interval=None),
            "cpu_count": psutil.cpu_count(),
        }

        checks['process'] = {
            "pid": os.getpid(),
            "threads": process.num_threads(),
            "open_files": len(process.open_files()),
        }

        # Net connections (method name varies by psutil version)
        try:
            checks['process']['connections'] = len(process.net_connections())
        except AttributeError:
            try:
                checks['process']['connections'] = len(process.connections())
            except Exception:
                checks['process']['connections'] = 0

        # Open FD count (unix only)
        try:
            checks['process']['open_fds'] = process.num_fds()
        except (AttributeError, psutil.Error):
            pass

        # Warn if memory usage is high (over 400MB for a Python app)
        if rss_mb > 400:
            warnings.append(f"Memory usage high: {rss_mb}MB")
    except Exception as e:
        checks['memory'] = {"status": "error", "message": str(e)}
        checks['cpu'] = {"status": "error"}
        checks['process'] = {"pid": os.getpid()}

    # System-level memory
    try:
        sys_mem = psutil.virtual_memory()
        checks['system_memory'] = {
            "total_gb": round(sys_mem.total / 1024 / 1024 / 1024, 2),
            "available_gb": round(sys_mem.available / 1024 / 1024 / 1024, 2),
            "used_percent": sys_mem.percent,
        }
    except Exception:
        pass

    # Uptime
    uptime = datetime.now(timezone.utc) - _app_start_time
    uptime_seconds = int(uptime.total_seconds())
    checks['uptime'] = {
        "started_at": _app_start_time.isoformat(),
        "uptime_seconds": uptime_seconds,
        "uptime_human": str(timedelta(seconds=uptime_seconds))
    }

    # Runtime info
    checks['runtime'] = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "flask_env": os.environ.get('FLASK_ENV', 'production'),
    }

    # External dependencies (cached - only refresh every 60s to avoid excessive API calls)
    now = time.time()
    if (_external_cache["last_check"] is None or
            now - _external_cache["last_check"] >= _EXTERNAL_CHECK_INTERVAL):
        external = {}
        ext_warnings = []

        # Check DocuSeal API (if configured) - uses same env var logic as docuseal_client.py
        docuseal_mode = os.environ.get('DOCUSEAL_MODE', 'test').lower()
        if docuseal_mode == 'prod':
            docuseal_key = os.environ.get('DOCUSEAL_API_KEY_PROD', '')
        else:
            docuseal_key = os.environ.get('DOCUSEAL_API_KEY_TEST', '')
        if docuseal_key:
            try:
                start_time = time.time()
                resp = requests.get(
                    'https://api.docuseal.com/templates',
                    headers={'X-Auth-Token': docuseal_key},
                    timeout=5
                )
                latency_ms = round((time.time() - start_time) * 1000, 2)
                external['docuseal'] = {
                    "status": "connected" if resp.status_code == 200 else "error",
                    "latency_ms": latency_ms
                }
                if resp.status_code != 200:
                    ext_warnings.append(f"DocuSeal API returned {resp.status_code}")
            except Exception as e:
                external['docuseal'] = {"status": "timeout", "message": str(e)}
                ext_warnings.append("DocuSeal API unreachable")

        # Check SendGrid API (if configured)
        sendgrid_key = os.environ.get('SENDGRID_API_KEY', '')
        if sendgrid_key:
            try:
                start_time = time.time()
                resp = requests.get(
                    'https://api.sendgrid.com/v3/scopes',
                    headers={'Authorization': f'Bearer {sendgrid_key}'},
                    timeout=5
                )
                latency_ms = round((time.time() - start_time) * 1000, 2)
                external['sendgrid'] = {
                    "status": "connected" if resp.status_code == 200 else "error",
                    "latency_ms": latency_ms
                }
                if resp.status_code != 200:
                    ext_warnings.append(f"SendGrid API returned {resp.status_code}")
            except Exception as e:
                external['sendgrid'] = {"status": "timeout", "message": str(e)}
                ext_warnings.append("SendGrid API unreachable")

        # Check Google OAuth (if configured) - lightweight tokeninfo endpoint
        google_client_id = os.environ.get('GOOGLE_CLIENT_ID', '')
        if google_client_id:
            try:
                start_time = time.time()
                resp = requests.get(
                    'https://oauth2.googleapis.com/tokeninfo',
                    params={'access_token': 'health_check'},
                    timeout=5
                )
                latency_ms = round((time.time() - start_time) * 1000, 2)
                # A 400 means Google's OAuth server is reachable (invalid token is expected)
                reachable = resp.status_code in (200, 400, 401)
                external['google_oauth'] = {
                    "status": "connected" if reachable else "error",
                    "latency_ms": latency_ms
                }
                if not reachable:
                    ext_warnings.append(f"Google OAuth returned {resp.status_code}")
            except Exception as e:
                external['google_oauth'] = {"status": "timeout", "message": str(e)}
                ext_warnings.append("Google OAuth unreachable")

        # Update cache
        _external_cache["data"] = external
        _external_cache["warnings"] = ext_warnings
        _external_cache["last_check"] = now

    # Use cached external data
    if _external_cache["data"]:
        checks['external'] = _external_cache["data"]
    warnings.extend(_external_cache["warnings"])

    response = {
        "status": status,
        "version": commit_sha,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks
    }

    if warnings:
        response["warnings"] = warnings

    return jsonify(response), 200 if status == "healthy" else 503


@main_bp.route('/health/ui')
def health_check_ui():
    """
    Health check UI - New Relic-style observability dashboard.
    """
    return render_template('health.html')


# =============================================================================
# LANDING PAGE (Public)
# =============================================================================

@main_bp.route('/')
def landing():
    """
    Public landing page for non-authenticated visitors.
    Logged-in users are redirected to their dashboard.
    """
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('landing.html', current_year=datetime.now().year)


# =============================================================================
# CONTACTS LIST (Authenticated)
# =============================================================================

@main_bp.route('/contacts')
@login_required
def contacts():
    # Multi-tenant: Use org_query and check org_role instead of legacy role
    show_all = request.args.get('view') == 'all' and can_view_all_org_data()
    sort_by = request.args.get('sort', 'name')
    sort_dir = request.args.get('dir', 'asc')
    search_query = request.args.get('q', '').strip()
    
    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    if show_all:
        # Show all contacts in this organization (for org admins/owners)
        query = org_query(Contact)
    else:
        # Show only current user's contacts
        query = org_query(Contact).filter_by(user_id=current_user.id)

    if search_query:
        search_filter = (
                (Contact.first_name.ilike(f'%{search_query}%')) |
                (Contact.last_name.ilike(f'%{search_query}%')) |
                (Contact.email.ilike(f'%{search_query}%')) |
                (Contact.phone.ilike(f'%{search_query}%'))
        )
        query = query.filter(search_filter)

    owners = request.args.get('owners')
    if show_all and owners:
        owner_ids = [int(id) for id in owners.split(',')]
        query = query.filter(Contact.user_id.in_(owner_ids))

    groups = request.args.get('groups')
    if groups:
        group_ids = [int(id) for id in groups.split(',')]
        query = query.join(Contact.groups).filter(ContactGroup.id.in_(group_ids))

    zips = request.args.get('zips')
    if zips:
        zip_list = [z.strip() for z in zips.split(',')]
        query = query.filter(Contact.zip_code.in_(zip_list))

    commission_range = request.args.get('commission')
    if commission_range:
        if commission_range == '0-5000':
            query = query.filter(Contact.potential_commission.between(0, 5000))
        elif commission_range == '5000-15000':
            query = query.filter(Contact.potential_commission.between(5000, 15000))
        elif commission_range == '15000-up':
            query = query.filter(Contact.potential_commission >= 15000)

    if sort_by == 'owner':
        query = query.join(User, Contact.user_id == User.id)
        if sort_dir == 'asc':
            query = query.order_by(User.first_name.asc(), User.last_name.asc())
        else:
            query = query.order_by(User.first_name.desc(), User.last_name.desc())
    elif sort_by == 'potential_commission':
        if sort_dir == 'asc':
            query = query.filter(Contact.potential_commission.isnot(None)).order_by(Contact.potential_commission.asc())
        else:
            query = query.filter(Contact.potential_commission.isnot(None)).order_by(Contact.potential_commission.desc())
    else:
        sort_map = {
            'name': [Contact.first_name, Contact.last_name],
            'email': [Contact.email],
            'phone': [Contact.phone],
            'address': [Contact.street_address],
            'notes': [Contact.notes],
            'created_at': [Contact.created_at],
            'last_contact_date': [Contact.last_contact_date]
        }

        if sort_by in sort_map:
            sort_attrs = sort_map[sort_by]
            if sort_dir == 'asc':
                query = query.order_by(*[attr.asc() for attr in sort_attrs])
            else:
                query = query.order_by(*[attr.desc() for attr in sort_attrs])

    # Add eager loading for groups to avoid N+1 queries in template
    query = query.options(selectinload(Contact.groups))
    
    # Apply pagination
    total_contacts = query.count()  # Get total count before pagination
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    contacts_list = pagination.items

    # Multi-tenant: Get contact groups (cached)
    from services.cache_helpers import get_org_contact_groups
    all_groups = get_org_contact_groups(current_user.organization_id)

    # Get all users in this organization (for admin filters)
    all_owners = []
    if can_view_all_org_data():
        all_owners = User.query.filter_by(
            organization_id=current_user.organization_id
        ).order_by(User.first_name, User.last_name).all()

    return render_template('contacts/list.html',
                         contacts=contacts_list,
                         total_contacts=total_contacts,
                         show_all=show_all,
                         current_sort=sort_by,
                         current_dir=sort_dir,
                         all_groups=all_groups,
                         all_owners=all_owners,
                         pagination=pagination,
                         per_page=per_page)

@main_bp.route('/dashboard')
@login_required
def dashboard():
    view = request.args.get('view', 'my')

    # Multi-tenant: Use org_query and check org_role
    if not can_view_all_org_data():
        show_all = False
        base_contact_query = org_query(Contact).filter_by(user_id=current_user.id)
    else:
        show_all = view == 'all'
        if show_all:
            # All contacts in this organization
            base_contact_query = org_query(Contact)
        else:
            base_contact_query = org_query(Contact).filter_by(user_id=current_user.id)

    # Use SQL aggregates instead of loading all contacts into memory
    stats = base_contact_query.with_entities(
        func.count(Contact.id).label('total'),
        func.coalesce(func.sum(Contact.potential_commission), 0).label('total_commission'),
        func.avg(Contact.potential_commission).label('avg_commission')
    ).first()
    
    total_contacts = stats.total
    total_commission = float(stats.total_commission)
    avg_commission = float(stats.avg_commission or 0)

    # Get top 5 contacts by commission using SQL LIMIT (not Python sort)
    top_contacts = base_contact_query.filter(
        Contact.potential_commission.isnot(None)
    ).order_by(
        Contact.potential_commission.desc()
    ).limit(5).all()

    # Multi-tenant: Get group stats with SQL GROUP BY instead of Python loop
    group_stats_query = db.session.query(
        ContactGroup.name,
        func.count(contact_groups_table.c.contact_id).label('count')
    ).join(
        contact_groups_table, ContactGroup.id == contact_groups_table.c.group_id
    ).join(
        Contact, Contact.id == contact_groups_table.c.contact_id
    ).filter(
        ContactGroup.organization_id == current_user.organization_id
    )
    # Apply same user filter for non-admin users
    if not show_all:
        group_stats_query = group_stats_query.filter(Contact.user_id == current_user.id)
    
    group_stats_raw = group_stats_query.group_by(ContactGroup.id, ContactGroup.name).all()
    group_stats = [{'name': name, 'count': count} for name, count in group_stats_raw if count > 0]

    # Get user's timezone (default to 'America/Chicago' if not set)
    user_tz = pytz.timezone('America/Chicago')
    
    # Get current time in user's timezone
    now = datetime.now(user_tz)
    
    # Get user's task window preference (defaults to 30 days)
    task_window_days = getattr(current_user, 'task_window_days', 30)
    window_end = now + timedelta(days=task_window_days)
    
    # Convert to UTC for database query
    utc_now = now.astimezone(timezone.utc)
    utc_window_end = window_end.astimezone(timezone.utc)

    # Multi-tenant: Query tasks within this org with eager loading for contact/task_type
    task_options = [joinedload(Task.contact), joinedload(Task.task_type)]
    
    if can_view_all_org_data() and show_all:
        upcoming_tasks = org_query(Task).options(*task_options).filter(
            or_(
                # Past due tasks
                Task.due_date < utc_now,
                # Upcoming tasks within user's window
                Task.due_date.between(utc_now, utc_window_end)
            ),
            Task.status != 'completed'
        ).order_by(
            # Order past due first, then by due date
            case(
                (Task.due_date < utc_now, 0),
                else_=1
            ),
            Task.due_date.asc()
        ).limit(5).all()
    else:
        upcoming_tasks = org_query(Task).options(*task_options).filter(
            Task.assigned_to_id == current_user.id,
            or_(
                # Past due tasks
                Task.due_date < utc_now,
                # Upcoming tasks within user's window
                Task.due_date.between(utc_now, utc_window_end)
            ),
            Task.status != 'completed'
        ).order_by(
            # Order past due first, then by due date
            case(
                (Task.due_date < utc_now, 0),
                else_=1
            ),
            Task.due_date.asc()
        ).limit(5).all()

    # Convert task due_dates to user's timezone
    for task in upcoming_tasks:
        if isinstance(task.due_date, datetime):
            # Convert UTC to user's timezone
            task.due_date = task.due_date.replace(tzinfo=timezone.utc).astimezone(user_tz)
        if task.scheduled_time:
            task.scheduled_time = task.scheduled_time.replace(tzinfo=timezone.utc).astimezone(user_tz)

    # Get latest company update for dashboard teaser (within this org)
    latest_update = org_query(CompanyUpdate).order_by(CompanyUpdate.created_at.desc()).first()

    # Transaction Pipeline Data (only for users with access)
    show_transactions = can_access_transactions(current_user)
    transactions_by_status = {}
    pipeline_value = 0
    ytd_closed_value = 0
    
    if show_transactions:
        # Get current year for YTD filter
        current_year = now.year
        
        # Multi-tenant: Query transactions within this org with eager loading
        if can_view_all_org_data() and show_all:
            tx_query = org_query(Transaction)
        else:
            tx_query = org_query(Transaction).filter_by(created_by_id=current_user.id)
        
        # Eager load transaction_type only (participants is a dynamic relationship)
        all_transactions = tx_query.options(
            joinedload(Transaction.transaction_type)
        ).order_by(Transaction.created_at.desc()).all()
        
        # Pre-fetch all participants for these transactions in one query to avoid N+1
        tx_ids = [tx.id for tx in all_transactions]
        if tx_ids:
            participants_query = TransactionParticipant.query.options(
                joinedload(TransactionParticipant.contact)
            ).filter(
                TransactionParticipant.transaction_id.in_(tx_ids),
                TransactionParticipant.is_primary == True,
                TransactionParticipant.role.in_(['seller', 'buyer', 'landlord', 'tenant'])
            ).all()
            # Build a lookup dict by transaction_id
            participants_by_tx = {p.transaction_id: p for p in participants_query}
        else:
            participants_by_tx = {}
        
        # Define Kanban columns - 'preparing' combines preparing_to_list and showing
        status_config = {
            'preparing': {'label': 'Preparing', 'order': 1, 'statuses': ['preparing_to_list', 'showing']},
            'active': {'label': 'Active', 'order': 2, 'statuses': ['active']},
            'under_contract': {'label': 'Under Contract', 'order': 3, 'statuses': ['under_contract']},
            'closed': {'label': 'Closed YTD', 'order': 4, 'statuses': ['closed']},
        }
        
        # Status display labels for cards
        status_labels = {
            'preparing_to_list': 'Preparing to List',
            'showing': 'Showing',
            'active': 'Active',
            'under_contract': 'Under Contract',
            'closed': 'Closed'
        }
        
        # Group transactions by Kanban column
        for column_key in status_config.keys():
            transactions_by_status[column_key] = {
                'label': status_config[column_key]['label'],
                'transactions': [],
                'count': 0
            }
        
        for tx in all_transactions:
            # For closed deals, only include YTD
            if tx.status == 'closed':
                if tx.actual_close_date and tx.actual_close_date.year == current_year:
                    pass  # Include it
                elif tx.created_at and tx.created_at.year == current_year:
                    pass  # Fallback to created_at year
                else:
                    continue  # Skip non-YTD closed deals
            
            # Get primary client participant from pre-fetched dict
            primary_client = participants_by_tx.get(tx.id)
            
            # Calculate commission from contact's potential_commission
            commission = 0
            client_name = "No client"
            if primary_client and primary_client.contact:
                commission = float(primary_client.contact.potential_commission or 0)
                client_name = f"{primary_client.contact.first_name} {primary_client.contact.last_name}"
            elif primary_client:
                client_name = primary_client.display_name
            
            # Build transaction data for template
            tx_data = {
                'id': tx.id,
                'address': tx.street_address,
                'city': tx.city,
                'client_name': client_name,
                'expected_close_date': tx.expected_close_date,
                'actual_close_date': tx.actual_close_date,
                'commission': commission,
                'status': tx.status,
                'status_label': status_labels.get(tx.status, tx.status.replace('_', ' ').title()),
                'type': tx.transaction_type.display_name if tx.transaction_type else 'Unknown'
            }
            
            # Find which Kanban column this transaction belongs to
            for column_key, column_config in status_config.items():
                if tx.status in column_config['statuses']:
                    transactions_by_status[column_key]['transactions'].append(tx_data)
                    transactions_by_status[column_key]['count'] += 1
                    break
            
            # Calculate pipeline value (non-closed deals only)
            if tx.status != 'closed':
                pipeline_value += commission
            else:
                ytd_closed_value += commission

    return render_template('dashboard.html',
                         show_all=show_all,
                         total_commission=total_commission,
                         total_contacts=total_contacts,
                         avg_commission=avg_commission,
                         group_stats=group_stats,
                         top_contacts=top_contacts,
                         upcoming_tasks=upcoming_tasks,
                         task_window_days=task_window_days,
                         latest_update=latest_update,
                         now=now,
                         show_dashboard_joke=is_enabled('SHOW_DASHBOARD_JOKE'),
                         show_transactions=show_transactions,
                         transactions_by_status=transactions_by_status,
                         pipeline_value=pipeline_value,
                         ytd_closed_value=ytd_closed_value)

@main_bp.route('/marketing')
@login_required
@feature_required('MARKETING')
def marketing():
    return render_template('marketing.html')


@main_bp.route('/api/update-task-window', methods=['POST'])
@login_required
def update_task_window():
    """API endpoint to update user's task window preference."""
    from models import db
    from flask import jsonify
    
    data = request.get_json()
    days = data.get('days')
    
    # Validate input
    if days not in [7, 30, 60, 90]:
        return jsonify({'success': False, 'error': 'Invalid days value. Must be 7, 30, 60, or 90.'}), 400
    
    # Update user preference
    current_user.task_window_days = days
    db.session.commit()
    
    return jsonify({'success': True, 'days': days})


@main_bp.route('/dashboard/dismiss-onboarding', methods=['POST'])
@login_required
def dismiss_dashboard_onboarding():
    """Mark dashboard onboarding as seen for current user."""
    from flask import jsonify
    
    current_user.has_seen_dashboard_onboarding = True
    db.session.commit()
    
    return jsonify({'success': True})
