"""
Seller Check-in Task Service

Auto-creates recurring weekly check-in tasks for active seller listings.
Tasks are created when a listing goes active and re-created every 7 days
when the previous check-in is completed, until the transaction leaves
active status.
"""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def should_auto_create_next(transaction):
    """Return True if a new auto-checkin task should be created for this transaction."""
    from models import Task

    if transaction.status != 'active':
        return False

    pending_checkin = Task.query.filter_by(
        transaction_id=transaction.id,
        is_auto_checkin=True,
        status='pending',
    ).first()

    return pending_checkin is None


def create_seller_checkin_task(transaction, created_by_user):
    """
    Create a weekly seller check-in task linked to the transaction.

    Finds the primary seller contact, assigns the task to the listing agent
    (transaction creator), and sets the due date 7 days out.

    Returns the created Task or None if prerequisites are missing.
    """
    from models import db, Task, TaskType, TaskSubtype, TransactionParticipant

    org_id = transaction.organization_id

    seller_participant = TransactionParticipant.query.filter(
        TransactionParticipant.transaction_id == transaction.id,
        TransactionParticipant.role.in_(['seller', 'co_seller']),
        TransactionParticipant.contact_id.isnot(None),
    ).order_by(
        TransactionParticipant.is_primary.desc()
    ).first()

    if not seller_participant:
        logger.info(
            "No seller contact linked to transaction %s — skipping auto-checkin",
            transaction.id,
        )
        return None

    contact = seller_participant.contact
    seller_name = f"{contact.first_name} {contact.last_name}".strip() or "Seller"

    task_type = TaskType.query.filter_by(
        organization_id=org_id, name='Call',
    ).first()
    if not task_type:
        task_type = TaskType.query.filter_by(
            organization_id=org_id,
        ).order_by(TaskType.sort_order.asc()).first()
    if not task_type:
        logger.warning("No task types for org %s — cannot create auto-checkin", org_id)
        return None

    task_subtype = TaskSubtype.query.filter_by(
        task_type_id=task_type.id,
        organization_id=org_id,
        name='Check-in',
    ).first()
    if not task_subtype:
        task_subtype = TaskSubtype.query.filter_by(
            task_type_id=task_type.id,
            organization_id=org_id,
        ).order_by(TaskSubtype.sort_order.asc()).first()
    if not task_subtype:
        logger.warning("No subtypes for task type %s — cannot create auto-checkin", task_type.id)
        return None

    address = transaction.street_address or "listing"
    due_date = datetime.now(timezone.utc) + timedelta(days=7)

    task = Task(
        organization_id=org_id,
        contact_id=contact.id,
        transaction_id=transaction.id,
        assigned_to_id=transaction.created_by_id,
        created_by_id=created_by_user.id,
        type_id=task_type.id,
        subtype_id=task_subtype.id,
        subject=f"Check in with {seller_name} — {address}",
        description=(
            f"Weekly seller check-in for {address}. "
            f"Touch base with {seller_name}: discuss current market conditions, "
            f"review any recent showing feedback, and share updated comps if available."
        ),
        priority='medium',
        status='pending',
        due_date=due_date,
        property_address=transaction.street_address,
        is_auto_checkin=True,
    )
    db.session.add(task)
    db.session.flush()

    logger.info(
        "Created auto-checkin task %s for transaction %s, due %s",
        task.id, transaction.id, due_date.strftime('%Y-%m-%d'),
    )
    return task
