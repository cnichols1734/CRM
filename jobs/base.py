# jobs/base.py
"""
Pattern for all background jobs that need org context.
"""

from functools import wraps
from sqlalchemy import text


def with_org_context(org_id: int):
    """
    Decorator for background jobs that need org context.
    Sets RLS context for the duration of the job.
    
    Usage:
        @with_org_context(org_id)
        def my_job():
            # RLS is now active for org_id
            contacts = Contact.query.all()  # Only returns org's contacts
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            from models import db
            db.session.execute(
                text("SET LOCAL app.current_org_id = :org_id"),
                {'org_id': org_id}
            )
            return func(*args, **kwargs)
        return wrapper
    return decorator


def set_job_org_context(org_id: int):
    """
    Imperative version - set RLS context for current db session.
    Use this when you can't use the decorator.
    
    Usage:
        set_job_org_context(org_id)
        contacts = Contact.query.all()  # Scoped to org_id
    """
    from models import db
    db.session.execute(
        text("SET LOCAL app.current_org_id = :org_id"),
        {'org_id': org_id}
    )


# Example usage in a task queue (e.g., Celery, RQ)
def example_background_job(org_id: int, user_id: int):
    """
    Example background job demonstrating org context pattern.
    MUST receive org_id explicitly - no current_user in background jobs.
    """
    from models import db, User, Task
    
    # Set RLS context
    db.session.execute(
        text("SET LOCAL app.current_org_id = :org_id"),
        {'org_id': org_id}
    )
    
    # Now queries are scoped correctly
    user = User.query.get(user_id)
    pending_tasks = Task.query.filter_by(
        assigned_to_id=user_id,
        status='pending'
    ).all()
    
    # ... do work ...
    return len(pending_tasks)


# When queuing the job (from request context):
# queue.enqueue(example_background_job,
#               org_id=current_user.organization_id,
#               user_id=current_user.id)
