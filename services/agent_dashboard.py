"""Agent iPhone dashboard payload. Reuses the web dashboard queries."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytz
from sqlalchemy import case, func, or_
from sqlalchemy.orm import joinedload

from feature_flags import can_access_transactions, get_org_features, org_has_feature
from models import Contact, Task, Transaction, TransactionParticipant
from services.agent_auth import serialize_org, serialize_user
from services.contact_group_service import aggregate_group_stats
from services.tenant_service import org_query_for_id

_CHICAGO = pytz.timezone('America/Chicago')


def build_dashboard(user):
    org = user.organization
    org_id = user.organization_id
    contact_query = org_query_for_id(Contact, org_id).filter_by(user_id=user.id)

    stats = contact_query.with_entities(
        func.count(Contact.id).label('total'),
        func.coalesce(func.sum(Contact.potential_commission), 0).label('total_commission'),
        func.avg(Contact.potential_commission).label('avg_commission'),
    ).first()

    group_stats = aggregate_group_stats(
        org_id, owner_user_id=user.id, show_all=False,
    )

    today_tasks = _today_tasks(user, org_id)
    pipeline_by_status = []
    pipeline_value = 0
    ytd_closed_value = 0
    if can_access_transactions(user):
        pipeline_by_status, pipeline_value, ytd_closed_value = _pipeline(user, org_id)

    return {
        'user': serialize_user(user),
        'kpis': {
            'total_contacts': int(stats.total or 0),
            'total_commission': float(stats.total_commission or 0),
            'avg_commission': float(stats.avg_commission or 0),
            'pipeline_value': float(pipeline_value or 0),
            'ytd_closed_value': float(ytd_closed_value or 0),
            'transactions_enabled': org_has_feature('TRANSACTIONS', org),
        },
        'today_tasks': today_tasks,
        'pipeline_by_status': pipeline_by_status,
        'group_stats': group_stats,
        'features': get_org_features(org),
    }


def me_payload(user):
    return {
        'user': serialize_user(user),
        'org': serialize_org(user.organization),
        'features': get_org_features(user.organization),
    }


def _today_tasks(user, org_id):
    today = datetime.now(_CHICAGO).date()
    start_local = _CHICAGO.localize(datetime.combine(today, datetime.min.time()))
    end_local = _CHICAGO.localize(
        datetime.combine(today + timedelta(days=1), datetime.min.time()),
    )
    start = start_local.astimezone(pytz.UTC).replace(tzinfo=None)
    end = end_local.astimezone(pytz.UTC).replace(tzinfo=None)
    now_utc = datetime.now(_CHICAGO).astimezone(pytz.UTC).replace(tzinfo=None)
    rows = (
        org_query_for_id(Task, org_id)
        .options(joinedload(Task.contact), joinedload(Task.task_type))
        .filter(
            Task.assigned_to_id == user.id,
            Task.status != 'completed',
            Task.due_date.isnot(None),
            or_(Task.due_date < start, Task.due_date.between(start, end)),
        )
        .order_by(
            case((Task.due_date < now_utc, 0), else_=1),
            Task.due_date.asc(),
        )
        .limit(20)
        .all()
    )
    out = []
    for task in rows:
        contact = task.contact
        out.append({
            'id': task.id,
            'subject': task.subject,
            'status': task.status,
            'priority': task.priority,
            'due_date': task.due_date.isoformat() if task.due_date else None,
            'contact_id': task.contact_id,
            'contact_name': (
                f'{contact.first_name} {contact.last_name}'.strip()
                if contact else None
            ),
        })
    return out


def _pipeline(user, org_id):
    now = datetime.utcnow()
    current_year = now.year
    tx_query = org_query_for_id(Transaction, org_id).filter_by(created_by_id=user.id)
    all_transactions = tx_query.options(
        joinedload(Transaction.transaction_type)
    ).order_by(Transaction.created_at.desc()).all()

    tx_ids = [tx.id for tx in all_transactions]
    if tx_ids:
        participants_query = TransactionParticipant.query.options(
            joinedload(TransactionParticipant.contact)
        ).filter(
            TransactionParticipant.transaction_id.in_(tx_ids),
            TransactionParticipant.is_primary == True,  # noqa: E712
            TransactionParticipant.role.in_(['seller', 'buyer', 'landlord', 'tenant']),
        ).all()
        participants_by_tx = {p.transaction_id: p for p in participants_query}
    else:
        participants_by_tx = {}

    status_config = {
        'early': {
            'label': 'Early Stage',
            'statuses': ['preparing_to_list', 'showing'],
        },
        'active': {
            'label': 'Active / Listed',
            'statuses': ['active'],
        },
        'pending': {
            'label': 'Pending',
            'statuses': ['under_contract'],
        },
        'closed': {
            'label': 'Closed YTD',
            'statuses': ['closed'],
        },
    }
    columns = {
        key: {
            'key': key,
            'label': cfg['label'],
            'count': 0,
            'transactions': [],
        }
        for key, cfg in status_config.items()
    }

    pipeline_value = 0
    ytd_closed_value = 0
    for tx in all_transactions:
        if tx.status == 'closed':
            if tx.actual_close_date and tx.actual_close_date.year == current_year:
                pass
            elif tx.created_at and tx.created_at.year == current_year:
                pass
            else:
                continue

        primary_client = participants_by_tx.get(tx.id)
        commission = 0
        client_name = 'No client'
        if primary_client and primary_client.contact:
            commission = float(primary_client.contact.potential_commission or 0)
            client_name = (
                f'{primary_client.contact.first_name} '
                f'{primary_client.contact.last_name}'
            )
        elif primary_client:
            client_name = primary_client.display_name

        tx_data = {
            'id': tx.id,
            'address': tx.street_address,
            'city': tx.city,
            'client_name': client_name,
            'expected_close_date': (
                tx.expected_close_date.isoformat() if tx.expected_close_date else None
            ),
            'actual_close_date': (
                tx.actual_close_date.isoformat() if tx.actual_close_date else None
            ),
            'commission': commission,
            'status': tx.status,
            'type': (
                tx.transaction_type.display_name if tx.transaction_type else 'Unknown'
            ),
        }
        for column_key, column_config in status_config.items():
            if tx.status in column_config['statuses']:
                columns[column_key]['transactions'].append(tx_data)
                columns[column_key]['count'] += 1
                break
        if tx.status != 'closed':
            pipeline_value += commission
        else:
            ytd_closed_value += commission

    return list(columns.values()), pipeline_value, ytd_closed_value
