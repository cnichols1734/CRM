# jobs/metrics_aggregator.py
"""
Updates organization_metrics table with aggregate counts.
Run via scheduler every 15-60 minutes.
Platform pages read these snapshots alongside activation milestones.
"""

from datetime import datetime
from sqlalchemy import text


def update_all_org_metrics():
    """
    Update metrics for all active organizations.
    Uses the application database role with explicit organization filters.
    """
    from models import db, Organization
    
    # Get org IDs first, then release the ORM objects
    org_ids = [org.id for org in Organization.query.filter(
        Organization.status == 'active',
        Organization.is_platform_admin == False
    ).all()]
    
    # Clean up session after initial query
    db.session.remove()
    
    for org_id in org_ids:
        try:
            update_single_org_metrics(org_id)
            db.session.commit()
        except Exception as e:
            print(f"[ERROR] Failed to update metrics for org {org_id}: {e}")
            db.session.rollback()
        finally:
            # CRITICAL: Clean up session after each org to prevent connection leaks
            db.session.remove()
    
    print(f"[{datetime.utcnow()}] Updated metrics for {len(org_ids)} organizations")


def update_single_org_metrics(org_id: int):
    """
    Update metrics for a single organization.
    Queries only counts and timestamps, with explicit organization filters.
    """
    from models import db, OrganizationMetrics
    
    # Get or create metrics record
    metrics = OrganizationMetrics.query.filter_by(organization_id=org_id).first()
    if not metrics:
        metrics = OrganizationMetrics(organization_id=org_id)
        db.session.add(metrics)
    
    # The application database role reads aggregates without loading tenant content.
    
    metrics.user_count = db.session.execute(
        text('SELECT COUNT(*) FROM "user" WHERE organization_id = :oid'),
        {'oid': org_id}
    ).scalar() or 0
    
    metrics.contact_count = db.session.execute(
        text("SELECT COUNT(*) FROM contact WHERE organization_id = :oid"),
        {'oid': org_id}
    ).scalar() or 0
    
    metrics.task_count = db.session.execute(
        text("SELECT COUNT(*) FROM task WHERE organization_id = :oid"),
        {'oid': org_id}
    ).scalar() or 0
    
    metrics.transaction_count = db.session.execute(
        text("SELECT COUNT(*) FROM transactions WHERE organization_id = :oid"),
        {'oid': org_id}
    ).scalar() or 0
    
    # Get last activity timestamps (still no PII - just timestamps)
    metrics.last_user_login_at = db.session.execute(
        text('SELECT MAX(last_login) FROM "user" WHERE organization_id = :oid'),
        {'oid': org_id}
    ).scalar()
    
    metrics.last_contact_created_at = db.session.execute(
        text("SELECT MAX(created_at) FROM contact WHERE organization_id = :oid"),
        {'oid': org_id}
    ).scalar()
    
    metrics.last_transaction_created_at = db.session.execute(
        text("SELECT MAX(created_at) FROM transactions WHERE organization_id = :oid"),
        {'oid': org_id}
    ).scalar()
    
    # Raw SQLite MAX timestamps are strings; PostgreSQL returns datetime values.
    for field in ('last_user_login_at', 'last_contact_created_at', 'last_transaction_created_at'):
        value = getattr(metrics, field)
        if isinstance(value, str):
            setattr(metrics, field, datetime.fromisoformat(value))

    from models import ChatConversation, ChatMessage, Interaction, Task, InboundMessage
    from sqlalchemy import func

    # Only counts leave these queries. Message bodies and contact details stay private.
    messages = db.session.query(ChatMessage.role, func.count(ChatMessage.id)).join(
        ChatConversation, ChatMessage.conversation_id == ChatConversation.id,
    ).filter(ChatConversation.organization_id == org_id).group_by(ChatMessage.role).all()
    task_counts = dict(db.session.query(Task.status, func.count(Task.id)).filter(
        Task.organization_id == org_id,
    ).group_by(Task.status).all())
    metrics.usage_summary = {
        'bob_user_messages': dict(messages).get('user', 0),
        'bob_replies': dict(messages).get('assistant', 0),
        'interactions': db.session.query(func.count(Interaction.id)).filter(Interaction.organization_id == org_id).scalar(),
        'inbox_messages': db.session.query(func.count(InboundMessage.id)).filter(InboundMessage.organization_id == org_id).scalar(),
        'tasks_pending': task_counts.get('pending', 0),
        'tasks_completed': task_counts.get('completed', 0),
    }
    metrics.updated_at = datetime.utcnow()


def run_metrics_update():
    """Entry point for scheduler/cron."""
    from app import create_app
    app = create_app()
    with app.app_context():
        update_all_org_metrics()
        print(f"Updated metrics for all organizations at {datetime.utcnow()}")


if __name__ == '__main__':
    run_metrics_update()
