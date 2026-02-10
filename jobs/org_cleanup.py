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


def _safe_delete(db, sql, params):
    """Execute a DELETE inside a SAVEPOINT so a single failure doesn't poison the transaction."""
    try:
        nested = db.session.begin_nested()  # SAVEPOINT
        db.session.execute(text(sql), params)
        nested.commit()
    except Exception as e:
        nested.rollback()  # rolls back to SAVEPOINT, outer transaction continues
        print(f"[WARN] Skipped: {sql.strip().splitlines()[0]} — {e}")


def hard_delete_organization(org_id: int, org_name: str):
    """
    Permanently delete an organization and ALL its data.
    Order matters due to foreign key constraints (ON DELETE RESTRICT).
    Each delete uses a SAVEPOINT so one missing table won't abort the whole transaction.
    
    Args:
        org_id: The organization ID to delete
        org_name: The organization name (for logging)
    """
    from models import db
    
    print(f"[{datetime.utcnow()}] Hard deleting organization: {org_name} (ID: {org_id})")
    
    p = {'oid': org_id}
    
    # Delete in dependency order (children before parents)
    # Use raw SQL to bypass RLS for deletion
    # Each step uses a SAVEPOINT so a missing/renamed table won't break the rest
    
    # 1. Document signatures (depends on transaction_documents)
    _safe_delete(db, """
        DELETE FROM document_signatures 
        WHERE document_id IN (
            SELECT td.id FROM transaction_documents td
            JOIN transactions t ON td.transaction_id = t.id
            WHERE t.organization_id = :oid
        )
    """, p)
    
    # 2. Transaction documents (depends on transactions)
    _safe_delete(db, """
        DELETE FROM transaction_documents 
        WHERE transaction_id IN (
            SELECT id FROM transactions WHERE organization_id = :oid
        )
    """, p)
    
    # 3. Transaction participants (depends on transactions, contacts)
    _safe_delete(db, """
        DELETE FROM transaction_participants 
        WHERE transaction_id IN (
            SELECT id FROM transactions WHERE organization_id = :oid
        )
    """, p)
    
    # 4. Audit events (depends on transactions)
    _safe_delete(db, """
        DELETE FROM audit_events 
        WHERE transaction_id IN (
            SELECT id FROM transactions WHERE organization_id = :oid
        )
    """, p)
    
    # 5. Transactions
    _safe_delete(db, "DELETE FROM transactions WHERE organization_id = :oid", p)
    
    # 6. Transaction types (referenced by transactions - now safe)
    _safe_delete(db, "DELETE FROM transaction_types WHERE organization_id = :oid", p)
    
    # 7. Tasks (depends on contacts, task_type, task_subtype, users)
    _safe_delete(db, "DELETE FROM task WHERE organization_id = :oid", p)
    
    # 8. Task subtypes (referenced by tasks - now safe)
    _safe_delete(db, "DELETE FROM task_subtype WHERE organization_id = :oid", p)
    
    # 9. Task types (referenced by tasks/subtypes - now safe)
    _safe_delete(db, "DELETE FROM task_type WHERE organization_id = :oid", p)
    
    # 10. Interactions (depends on contacts, users)
    _safe_delete(db, "DELETE FROM interaction WHERE organization_id = :oid", p)
    
    # 11. Contact files (depends on contacts)
    _safe_delete(db, """
        DELETE FROM contact_files 
        WHERE contact_id IN (
            SELECT id FROM contact WHERE organization_id = :oid
        )
    """, p)
    
    # 12. Contact voice memos (depends on contacts)
    _safe_delete(db, """
        DELETE FROM contact_voice_memo 
        WHERE contact_id IN (
            SELECT id FROM contact WHERE organization_id = :oid
        )
    """, p)
    
    # 13. Contact emails (depends on contacts)
    _safe_delete(db, """
        DELETE FROM contact_emails 
        WHERE contact_id IN (
            SELECT id FROM contact WHERE organization_id = :oid
        )
    """, p)
    
    # 14. Contacts
    _safe_delete(db, "DELETE FROM contact WHERE organization_id = :oid", p)
    
    # 15. Contact groups
    _safe_delete(db, "DELETE FROM contact_group WHERE organization_id = :oid", p)
    
    # 16. Company update reactions/comments/views (RESTRICT on org_id)
    _safe_delete(db, "DELETE FROM company_update_reactions WHERE organization_id = :oid", p)
    _safe_delete(db, "DELETE FROM company_update_comments WHERE organization_id = :oid", p)
    _safe_delete(db, "DELETE FROM company_update_views WHERE organization_id = :oid", p)
    
    # 17. Other org-scoped tables
    for table in ['action_plan', 'daily_todo_lists', 'user_todos',
                  'company_updates', 'sendgrid_template', 'organization_invites',
                  'agent_resource', 'chat_conversation', 'user_email_integration']:
        _safe_delete(db, f'DELETE FROM "{table}" WHERE organization_id = :oid', p)
    
    # 18. Organization metrics
    _safe_delete(db, "DELETE FROM organization_metrics WHERE organization_id = :oid", p)
    
    # 19. Platform audit logs for this org
    _safe_delete(db, "DELETE FROM platform_audit_log WHERE target_org_id = :oid", p)
    
    # 20. Users (finally)
    _safe_delete(db, 'DELETE FROM "user" WHERE organization_id = :oid', p)
    
    # 21. Organization itself — this one must succeed, so run directly
    db.session.execute(text("DELETE FROM organizations WHERE id = :oid"), p)
    
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
