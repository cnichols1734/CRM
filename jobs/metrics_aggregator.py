# jobs/metrics_aggregator.py
"""
Updates organization_metrics table with aggregate counts.
Run via scheduler every 15-60 minutes.
Platform admin dashboard reads ONLY from this table.
"""

from datetime import datetime
from sqlalchemy import text


def update_all_org_metrics():
    """
    Update metrics for all active organizations.
    Uses raw SQL to bypass RLS (this is intentional and safe - only counts, no PII).
    """
    from models import db, Organization
    
    orgs = Organization.query.filter(
        Organization.status == 'active',
        Organization.is_platform_admin == False
    ).all()
    
    for org in orgs:
        update_single_org_metrics(org.id)
    
    db.session.commit()
    print(f"[{datetime.utcnow()}] Updated metrics for {len(orgs)} organizations")


def update_single_org_metrics(org_id: int):
    """
    Update metrics for a single organization.
    Uses raw SQL to get counts (bypasses RLS, but only gets counts - no PII).
    """
    from models import db, OrganizationMetrics
    
    # Get or create metrics record
    metrics = OrganizationMetrics.query.filter_by(organization_id=org_id).first()
    if not metrics:
        metrics = OrganizationMetrics(organization_id=org_id)
        db.session.add(metrics)
    
    # Use raw SQL to get counts (bypasses RLS, but only gets counts - no PII)
    # This is the ONLY place we bypass RLS, and it's for aggregates only
    
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
