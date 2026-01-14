from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from models import Contact, ContactGroup, Task, User, CompanyUpdate, Transaction, TransactionParticipant
from feature_flags import is_enabled, can_access_transactions
from datetime import datetime, timedelta, timezone, date
import pytz
from sqlalchemy import func, extract
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, case

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
@login_required
def index():
    show_all = request.args.get('view') == 'all' and current_user.role == 'admin'
    sort_by = request.args.get('sort', 'name')
    sort_dir = request.args.get('dir', 'asc')
    search_query = request.args.get('q', '').strip()
    
    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    if show_all:
        query = Contact.query
    else:
        query = Contact.query.filter_by(user_id=current_user.id)

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

    # Apply pagination
    total_contacts = query.count()  # Get total count before pagination
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    contacts = pagination.items

    all_groups = ContactGroup.query.order_by(ContactGroup.name).all()

    all_owners = []
    if current_user.role == 'admin':
        all_owners = User.query.order_by(User.first_name, User.last_name).all()

    return render_template('index.html',
                         contacts=contacts,
                         total_contacts=total_contacts,  # Pass total count to template
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

    if current_user.role != 'admin':
        show_all = False
        contacts = Contact.query.filter_by(user_id=current_user.id).all()
    else:
        show_all = view == 'all'
        if show_all:
            contacts = Contact.query.all()
        else:
            contacts = Contact.query.filter_by(user_id=current_user.id).all()

    total_contacts = len(contacts)
    total_commission = sum(c.potential_commission or 0 for c in contacts)
    avg_commission = total_commission / total_contacts if total_contacts > 0 else 0

    top_contacts = sorted(
        contacts,
        key=lambda x: x.potential_commission or 0,
        reverse=True
    )[:5]

    groups = ContactGroup.query.all()
    group_stats = []
    for group in groups:
        contact_count = len([c for c in contacts if group in c.groups])
        if contact_count > 0:
            group_stats.append({
                'name': group.name,
                'count': contact_count
            })

    # Get user's timezone (default to 'America/Chicago' if not set)
    user_tz = pytz.timezone('America/Chicago')
    
    # Get current time in user's timezone
    now = datetime.now(user_tz)
    seven_days = now + timedelta(days=7)
    
    # Convert to UTC for database query
    utc_now = now.astimezone(timezone.utc)
    utc_seven_days = seven_days.astimezone(timezone.utc)

    if current_user.role == 'admin' and show_all:
        upcoming_tasks = Task.query.filter(
            or_(
                # Past due tasks
                Task.due_date < utc_now,
                # Upcoming tasks within 7 days
                Task.due_date.between(utc_now, utc_seven_days)
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
        upcoming_tasks = Task.query.filter(
            Task.assigned_to_id == current_user.id,
            or_(
                # Past due tasks
                Task.due_date < utc_now,
                # Upcoming tasks within 7 days
                Task.due_date.between(utc_now, utc_seven_days)
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

    # Get latest company update for dashboard teaser
    latest_update = CompanyUpdate.query.order_by(CompanyUpdate.created_at.desc()).first()

    # Transaction Pipeline Data (only for users with access)
    show_transactions = can_access_transactions(current_user)
    transactions_by_status = {}
    pipeline_value = 0
    ytd_closed_value = 0
    
    if show_transactions:
        # Get current year for YTD filter
        current_year = now.year
        
        # Query all transactions for the user (or all for admin viewing all)
        if current_user.role == 'admin' and show_all:
            tx_query = Transaction.query
        else:
            tx_query = Transaction.query.filter_by(created_by_id=current_user.id)
        
        all_transactions = tx_query.order_by(Transaction.created_at.desc()).all()
        
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
            
            # Get primary client participant
            primary_client = tx.participants.filter(
                TransactionParticipant.is_primary == True,
                TransactionParticipant.role.in_(['seller', 'buyer', 'landlord', 'tenant'])
            ).first()
            
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
                         latest_update=latest_update,
                         now=now,
                         show_dashboard_joke=is_enabled('SHOW_DASHBOARD_JOKE'),
                         show_transactions=show_transactions,
                         transactions_by_status=transactions_by_status,
                         pipeline_value=pipeline_value,
                         ytd_closed_value=ytd_closed_value)

@main_bp.route('/marketing')
@login_required
def marketing():
    return render_template('marketing.html')
