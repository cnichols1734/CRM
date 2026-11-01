"""Delete expired unsent uploads in each organization's RLS context."""
from models import Organization, db
from jobs.base import set_job_org_context
from services.mobile_attachments import cleanup_expired


def cleanup_staged_attachments():
    org_ids = [row.id for row in Organization.query.all()]
    db.session.remove()
    removed = 0
    for org_id in org_ids:
        try:
            set_job_org_context(org_id)
            removed += cleanup_expired(org_id, limit=1000)
            db.session.commit()
        except Exception:
            from flask import current_app
            current_app.logger.exception('Expired message attachment cleanup failed for organization %s', org_id)
            db.session.rollback()
        finally:
            db.session.remove()
    return removed
