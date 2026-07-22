"""
Pre-warm Daily Briefings for recently active users.

Run via cron (e.g. 5am America/Chicago):
    python jobs/daily_briefing.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


def prewarm_daily_briefings(active_within_days: int = 7):
    """Generate today's briefing for users active in the last N days."""
    from models import db, User, DailyTodoList
    from services.daily_briefing import today_local, generate_briefing_content
    from jobs.base import set_job_org_context

    plan_date = today_local()
    cutoff = datetime.utcnow() - timedelta(days=active_within_days)

    users = (
        User.query
        .filter(User.last_login >= cutoff)
        .filter(User.organization_id.isnot(None))
        .all()
    )

    # Fallback if last_login doesn't exist / is unused
    if not users:
        users = (
            User.query
            .filter(User.organization_id.isnot(None))
            .order_by(User.id.desc())
            .limit(50)
            .all()
        )

    from feature_flags import org_has_feature

    generated = 0
    skipped = 0
    failed = 0

    for user in users:
        org = user.organization
        if not org or not org_has_feature('AI_DAILY_TODO', org):
            skipped += 1
            continue

        existing = DailyTodoList.get_for_user_date(user.id, plan_date)
        if existing and existing.status == 'ready':
            skipped += 1
            continue

        if org.id:
            set_job_org_context(org.id)

        try:
            content, model_used = generate_briefing_content(
                user.id, user.organization_id
            )
            if existing:
                row = existing
                row.todo_content = content
                row.status = 'ready'
                row.model_used = model_used
                row.error = None
                row.generated_at = datetime.utcnow()
            else:
                row = DailyTodoList(
                    user_id=user.id,
                    organization_id=user.organization_id,
                    plan_date=plan_date,
                    status='ready',
                    todo_content=content,
                    item_states={},
                    model_used=model_used,
                    generated_at=datetime.utcnow(),
                )
                db.session.add(row)
            db.session.commit()
            generated += 1
        except Exception as e:
            failed += 1
            logger.exception(f"Prewarm failed for user {user.id}: {e}")
            db.session.rollback()
            if existing:
                try:
                    existing.status = 'failed'
                    existing.error = str(e)[:1000]
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        finally:
            db.session.remove()

    logger.info(
        f"Daily briefing prewarm done: generated={generated} "
        f"skipped={skipped} failed={failed}"
    )
    return {"generated": generated, "skipped": skipped, "failed": failed}


def run_daily_briefing_prewarm():
    """Cron entry point."""
    from app import create_app

    app = create_app()
    with app.app_context():
        prewarm_daily_briefings()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_daily_briefing_prewarm()
