# jobs/org_cleanup.py
"""
Hard delete organizations past their 30-day grace period.
Run daily via scheduler.
"""

from datetime import datetime
from sqlalchemy import text


def cleanup_pending_deletions():
    """Find and hard-delete orgs past grace period."""
    from models import db, Organization
    
    cutoff = datetime.utcnow()
    
    # Get org IDs and names first, then release the ORM objects
    orgs_to_delete = [(org.id, org.name) for org in Organization.query.filter(
        Organization.status == 'pending_deletion',
        Organization.deletion_scheduled_at <= cutoff
    ).all()]
    
    # Clean up session after initial query
    db.session.remove()
    
    deleted_count = 0
    for org_id, org_name in orgs_to_delete:
        try:
            hard_delete_organization(org_id, org_name)
            deleted_count += 1
        except Exception as e:
            print(f"[ERROR] Failed to delete org {org_id} ({org_name}): {e}")
            db.session.rollback()
        finally:
            # CRITICAL: Clean up session after each org to prevent connection leaks
            db.session.remove()
    
    print(f"[{datetime.utcnow()}] Cleaned up {deleted_count} organizations past deletion date")
    return deleted_count


def hard_delete_organization(org_id: int, org_name: str):
    """
    Permanently delete an organization and ALL its data.
    Order matters due to foreign key constraints (ON DELETE RESTRICT).
    
    Args:
        org_id: The organization ID to delete
        org_name: The organization name (for logging)
    """
    from models import db
    
    print(f"[{datetime.utcnow()}] Hard deleting organization: {org_name} (ID: {org_id})")
    
    # Delete in dependency order (children before parents)
    # Use raw SQL to bypass RLS for deletion
    
    # 1. Document signatures
    db.session.execute(text("""
        DELETE FROM document_signatures 
        WHERE document_id IN (
            SELECT td.id FROM transaction_documents td
            JOIN transactions t ON td.transaction_id = t.id
            WHERE t.organization_id = :oid
        )
    """), {'oid': org_id})
    
    # 2. Transaction documents
    db.session.execute(text("""
        DELETE FROM transaction_documents 
        WHERE transaction_id IN (
            SELECT id FROM transactions WHERE organization_id = :oid
        )
    """), {'oid': org_id})
    
    # 3. Transaction participants
    db.session.execute(text("""
        DELETE FROM transaction_participants 
        WHERE transaction_id IN (
            SELECT id FROM transactions WHERE organization_id = :oid
        )
    """), {'oid': org_id})
    
    # 4. Audit events for transactions
    db.session.execute(text("""
        DELETE FROM audit_events 
        WHERE transaction_id IN (
            SELECT id FROM transactions WHERE organization_id = :oid
        )
    """), {'oid': org_id})
    
    # 5. Transactions
    db.session.execute(text(
        "DELETE FROM transactions WHERE organization_id = :oid"
    ), {'oid': org_id})
    
    # 6. Tasks (depends on contacts)
    db.session.execute(text(
        "DELETE FROM task WHERE organization_id = :oid"
    ), {'oid': org_id})
    
    # 7. Contact files
    db.session.execute(text("""
        DELETE FROM contact_files 
        WHERE contact_id IN (
            SELECT id FROM contact WHERE organization_id = :oid
        )
    """), {'oid': org_id})
    
    # 8. Contacts
    db.session.execute(text(
        "DELETE FROM contact WHERE organization_id = :oid"
    ), {'oid': org_id})
    
    # 9. Contact groups
    db.session.execute(text(
        "DELETE FROM contact_group WHERE organization_id = :oid"
    ), {'oid': org_id})
    
    # 10. Other org-scoped tables
    org_tables = [
        'action_plan', 'daily_todo_lists', 'user_todos',
        'company_updates', 'sendgrid_template', 'organization_invites'
    ]
    for table in org_tables:
        try:
            db.session.execute(text(
                f'DELETE FROM "{table}" WHERE organization_id = :oid'
            ), {'oid': org_id})
        except Exception as e:
            print(f"[WARN] Could not delete from {table}: {e}")
    
    # 11. Organization metrics
    db.session.execute(text(
        "DELETE FROM organization_metrics WHERE organization_id = :oid"
    ), {'oid': org_id})
    
    # 12. Platform audit logs for this org
    db.session.execute(text(
        "DELETE FROM platform_audit_log WHERE target_org_id = :oid"
    ), {'oid': org_id})
    
    # 13. Users (finally)
    db.session.execute(text(
        'DELETE FROM "user" WHERE organization_id = :oid'
    ), {'oid': org_id})
    
    # 14. Organization itself
    db.session.execute(text(
        "DELETE FROM organizations WHERE id = :oid"
    ), {'oid': org_id})
    
    db.session.commit()
    
    print(f"[{datetime.utcnow()}] Successfully deleted organization: {org_name}")


def run_cleanup():
    """Entry point for scheduler/cron."""
    from app import create_app
    app = create_app()
    with app.app_context():
        cleanup_pending_deletions()


if __name__ == '__main__':
    run_cleanup()
