#!/usr/bin/env python3
"""Emit one-time privacy-safe retention baselines for existing customer users.

Idempotent: users who already have retention_baseline_snapshot are skipped.

Usage:
    python scripts/retention_baseline.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from models import ActivationEvent, Contact, Organization, Task, User, db
from services.activation_service import (
    count_bucket,
    is_customer_user,
    is_user_activated,
    maybe_transition_retention_stage,
    record_event,
)


def _days_bucket(days):
    if days is None:
        return 'unknown'
    if days <= 1:
        return '0_1'
    if days <= 7:
        return '2_7'
    if days <= 30:
        return '8_30'
    if days <= 90:
        return '31_90'
    return '90_plus'


def run_baseline():
    now = datetime.utcnow()
    created = 0
    org_ids = [
        row.id for row in Organization.query.filter(
            Organization.status == 'active',
            Organization.is_platform_admin.is_(False),
        ).all()
    ]
    for org_id in org_ids:
        users = User.query.filter_by(organization_id=org_id).all()
        for user in users:
            if not is_customer_user(user):
                continue
            existing = ActivationEvent.query.filter_by(
                user_id=user.id,
                event=ActivationEvent.RETENTION_BASELINE_SNAPSHOT,
            ).first()
            if existing:
                continue

            contact_count = Contact.query.filter_by(user_id=user.id).count()
            pending = Task.query.filter_by(
                assigned_to_id=user.id, status='pending',
            ).count()
            completed = Task.query.filter_by(
                assigned_to_id=user.id, status='completed',
            ).count()
            overdue = Task.query.filter(
                Task.assigned_to_id == user.id,
                Task.status == 'pending',
                Task.due_date.isnot(None),
                Task.due_date < now,
            ).count()
            age_days = (
                (now - user.created_at).days if user.created_at else None
            )
            last_login_days = (
                (now - user.last_login).days if user.last_login else None
            )
            stage_row = maybe_transition_retention_stage(
                user, now=now, reason='baseline',
            )
            stage = None
            if stage_row and isinstance(stage_row.event_data, dict):
                stage = stage_row.event_data.get('current')

            record_event(
                ActivationEvent.RETENTION_BASELINE_SNAPSHOT,
                user=user,
                data={
                    'account_age_bucket': _days_bucket(age_days),
                    'activated_now': is_user_activated(user),
                    'contact_count_bucket': count_bucket(contact_count),
                    'pending_task_bucket': count_bucket(pending),
                    'overdue_task_bucket': count_bucket(overdue),
                    'completed_task_bucket': count_bucket(completed),
                    'days_since_last_login_bucket': _days_bucket(last_login_days),
                    'inbox_used': bool(getattr(user, 'inbox_address', None)),
                    'retention_stage': stage,
                    'snapshot_at': now.isoformat(),
                },
                once=True,
            )
            created += 1
        db.session.commit()
    return created


def main():
    app = create_app()
    with app.app_context():
        created = run_baseline()
        print(f'Retention baseline: wrote {created} snapshot(s)')


if __name__ == '__main__':
    main()
