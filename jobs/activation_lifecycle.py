"""Capped self-serve activation reminders.

Run hourly with ``python jobs/activation_lifecycle.py``. The job sends at most
one message per stage, at most three total, and stops as soon as a user is
activated (contact + dated follow-up subtype).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta


APP_BASE_URL = os.environ.get(
    'APP_BASE_URL', 'https://www.origentechnolog.com'
).rstrip('/')


def send_activation_lifecycle_messages(now=None):
    from flask import current_app
    from jobs.base import set_job_org_context
    from models import ActivationEvent, Contact, Organization, User, db
    from services.activation_service import is_user_activated, record_event
    from services.retention_tokens import make_email_click_token
    from services.sendgrid_outbound import send_activation_nudge

    now = now or datetime.utcnow()
    org_ids = [
        row.id for row in Organization.query.filter(
            Organization.status == 'active',
            Organization.is_platform_admin.is_(False),
        ).all()
    ]
    sent = 0

    for org_id in org_ids:
        set_job_org_context(org_id)
        users = User.query.filter_by(organization_id=org_id).all()
        for user in users:
            if not user.created_at:
                continue
            if is_user_activated(user):
                continue

            age = now - user.created_at
            contact_count = Contact.query.filter_by(user_id=user.id).count()

            prior = ActivationEvent.query.filter_by(
                user_id=user.id,
                event=ActivationEvent.LIFECYCLE_MESSAGE_SENT,
            ).all()
            if len(prior) >= 3:
                continue
            sent_stages = {
                (event.event_data or {}).get('stage') for event in prior
                if isinstance(event.event_data, dict)
            }

            stage = None
            if age >= timedelta(days=3):
                stage = 'stalled_3d'
            elif contact_count and age >= timedelta(hours=24):
                stage = 'no_follow_up_24h'
            elif not contact_count and age >= timedelta(hours=2):
                stage = 'no_contact_2h'

            if not stage or stage in sent_stages:
                continue

            action_url = f'{APP_BASE_URL}/dashboard?lifecycle={stage}'
            if stage == 'stalled_3d':
                action_url += '&friction=1'
            try:
                token = make_email_click_token(
                    current_app,
                    user_id=user.id,
                    campaign='lifecycle',
                    stage=stage,
                )
                action_url += f'&ect={token}'
            except Exception:
                pass

            if send_activation_nudge(user, stage=stage, action_url=action_url):
                record_event(
                    ActivationEvent.LIFECYCLE_MESSAGE_SENT,
                    user=user,
                    data={'stage': stage, 'sequence_number': len(prior) + 1},
                )
                sent += 1

        db.session.commit()
        db.session.remove()

    return sent


def run_activation_lifecycle():
    from app import create_app

    app = create_app()
    with app.app_context():
        sent = send_activation_lifecycle_messages()
        print(f'Activation lifecycle: sent {sent} message(s)')


if __name__ == '__main__':
    run_activation_lifecycle()
