"""Uncached aggregates of saved activity and contact source records."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask_login import current_user
from sqlalchemy import case, func, or_

from models import Contact, Interaction, Transaction, TransactionAssignment, TransactionParticipant, db
from services.tenant_service import is_org_admin


CLIENT_ROLES = ('buyer', 'co_buyer', 'seller', 'co_seller', 'landlord', 'tenant')
ACTIVITY_LABELS = {'call': 'Calls', 'email': 'Emails', 'text': 'Texts', 'meeting': 'Meetings', 'other': 'Other'}


def get_activity_overview(days=7, team=False, today=None):
    """Scope activity by recorder and contact ownership; sources by contact owner."""
    if not current_user.is_authenticated or not current_user.organization_id:
        raise PermissionError('An organization account is required.')
    org_id = current_user.organization_id
    team = bool(team and is_org_admin())
    days = 30 if days == 30 else 7
    today = today or datetime.now(ZoneInfo('America/Chicago')).date()
    start = today - timedelta(days=days - 1)
    contacts = db.session.query(Contact.id).filter(Contact.organization_id == org_id)
    if not team:
        contacts = contacts.filter(Contact.user_id == current_user.id)
    contact_ids = contacts.scalar_subquery()

    activity = db.session.query(Interaction).filter(
        Interaction.organization_id == org_id,
        Interaction.contact_id.in_(contact_ids),
        Interaction.date >= datetime.combine(start, time.min),
        Interaction.date < datetime.combine(today + timedelta(days=1), time.min),
    )
    if not team:
        activity = activity.filter(Interaction.user_id == current_user.id)
    total, reached = activity.with_entities(
        func.count(Interaction.id), func.count(func.distinct(Interaction.contact_id)),
    ).one()
    breakdown = activity.with_entities(Interaction.type, func.count(Interaction.id)).group_by(Interaction.type).all()

    # EXISTS avoids multiplying a contact with several roles or closed deals.
    closed = db.session.query(TransactionParticipant.id).join(
        Transaction, Transaction.id == TransactionParticipant.transaction_id,
    ).filter(
        TransactionParticipant.organization_id == org_id,
        Transaction.organization_id == org_id,
        TransactionParticipant.contact_id == Contact.id,
        TransactionParticipant.role.in_(CLIENT_ROLES),
        Transaction.status == 'closed',
    )
    if not is_org_admin():
        assigned = db.session.query(TransactionAssignment.id).filter(
            TransactionAssignment.organization_id == org_id,
            TransactionAssignment.transaction_id == Transaction.id,
            TransactionAssignment.user_id == current_user.id,
        ).exists()
        closed = closed.filter(or_(Transaction.created_by_id == current_user.id, assigned))
    source = func.coalesce(func.nullif(func.trim(Contact.lead_source), ''), 'Not recorded')
    sources = db.session.query(
        source.label('source'), func.count(Contact.id).label('contacts'),
        func.sum(case((closed.exists(), 1), else_=0)).label('closed_contacts'),
    ).filter(Contact.organization_id == org_id, Contact.id.in_(contact_ids)).group_by(source).order_by(
        func.count(Contact.id).desc(), source,
    ).all()
    return {
        'start': start, 'end': today, 'activities': total, 'contacts_with_activity': reached,
        'activity_types': [{'label': ACTIVITY_LABELS.get(kind, kind.replace('_', ' ').capitalize()), 'count': count}
                           for kind, count in sorted(breakdown)],
        'sources': [{'source': row.source, 'contacts': row.contacts, 'closed_contacts': row.closed_contacts,
                     'closed_share': round(100 * row.closed_contacts / row.contacts, 1)} for row in sources],
        'total_contacts': sum(row.contacts for row in sources),
    }
