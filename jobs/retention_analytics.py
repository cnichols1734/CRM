"""Daily retention-stage classification for customer users.

Run daily with ``python jobs/retention_analytics.py``. Milestones update state
immediately elsewhere; this job handles transitions caused by time passing.
"""
from __future__ import annotations


def classify_customer_retention_stages(now=None):
    from jobs.base import set_job_org_context
    from models import Organization, User, db
    from services.activation_service import (
        is_customer_user, maybe_transition_retention_stage,
    )

    org_ids = [
        row.id for row in Organization.query.filter(
            Organization.status == 'active',
            Organization.is_platform_admin.is_(False),
        ).all()
    ]
    changed = 0
    for org_id in org_ids:
        set_job_org_context(org_id)
        for user in User.query.filter_by(organization_id=org_id).all():
            if not is_customer_user(user):
                continue
            previous = maybe_transition_retention_stage(
                user, now=now, reason='scheduled',
            )
            if previous is not None and (
                not previous.event_data
                or previous.event_data.get('reason') == 'scheduled'
            ):
                # Count only when a new transition row was written in this pass.
                # maybe_transition returns existing row when unchanged; detect
                # freshness by created_at within a few seconds.
                from datetime import datetime, timedelta
                if previous.created_at and (
                    datetime.utcnow() - previous.created_at
                ) < timedelta(seconds=30):
                    changed += 1
        db.session.commit()
        db.session.remove()
    return changed


def run_retention_analytics():
    from app import create_app

    app = create_app()
    with app.app_context():
        changed = classify_customer_retention_stages()
        print(f'Retention analytics: {changed} stage transition(s)')


if __name__ == '__main__':
    run_retention_analytics()
