from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from models import Contact, ContactGroup, Task, User
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import joinedload

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
@login_required
def index():
    show_all = request.args.get('view') == 'all' and current_user.role == 'admin'
    sort_by = request.args.get('sort', 'created_at')
    sort_dir = request.args.get('dir', 'desc')
    search_query = request.args.get('q', '').strip()

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
            'created_at': [Contact.created_at]
        }

        if sort_by in sort_map:
            sort_attrs = sort_map[sort_by]
            if sort_dir == 'asc':
                query = query.order_by(*[attr.asc() for attr in sort_attrs])
            else:
                query = query.order_by(*[attr.desc() for attr in sort_attrs])

    all_groups = ContactGroup.query.order_by(ContactGroup.name).all()

    all_owners = []
    if show_all:
        all_owners = User.query.order_by(User.first_name, User.last_name).all()

    contacts = query.all()
    return render_template('index.html',
                         contacts=contacts,
                         show_all=show_all,
                         current_sort=sort_by,
                         current_dir=sort_dir,
                         all_groups=all_groups,
                         all_owners=all_owners)

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

    now = datetime.now()
    seven_days = now + timedelta(days=7)

    if current_user.role == 'admin' and show_all:
        upcoming_tasks = Task.query.filter(
            Task.due_date.between(now, seven_days),
            Task.status != 'completed'
        ).order_by(Task.due_date.asc()).limit(5).all()
    else:
        upcoming_tasks = Task.query.filter(
            Task.assigned_to_id == current_user.id,
            Task.due_date.between(now, seven_days),
            Task.status != 'completed'
        ).order_by(Task.due_date.asc()).limit(5).all()

    return render_template('dashboard.html',
                         show_all=show_all,
                         total_commission=total_commission,
                         total_contacts=total_contacts,
                         avg_commission=avg_commission,
                         group_stats=group_stats,
                         top_contacts=top_contacts,
                         upcoming_tasks=upcoming_tasks,
                         now=now)

@main_bp.route('/marketing')
@login_required
def marketing():
    return render_template('marketing.html')
