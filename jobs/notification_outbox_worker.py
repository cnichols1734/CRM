"""
Notification Outbox Worker - Phase 1C (E1C-5)

Processes queued NotificationDelivery rows for in_app and telegram channels.
Respects scheduled_for / not_before / snooze (via list_pending_deliveries).

Usage:
    python jobs/notification_outbox_worker.py
    python jobs/notification_outbox_worker.py --org-id 1
    python jobs/notification_outbox_worker.py --limit 50
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# Map outbox categories onto Notification preference categories when needed.
_CATEGORY_FALLBACK = {
    'deadline': 'task_reminder',
    'document_review': 'document_review',
    'proposal': 'bob_action',
}


def run_notification_outbox_worker(
    org_id: Optional[int] = None,
    *,
    limit: int = 100,
) -> Dict[str, int]:
    """
    Deliver pending outbox rows.

    in_app → Notification bell row
    telegram → messaging.outbound.notify (quiet hours already handled by outbox)
    """
    from jobs.base import set_job_org_context
    from models import NotificationDelivery, Organization, db
    from services.notification_outbox import NotificationOutboxService

    if org_id is not None:
        org_ids = [org_id]
    else:
        org_ids = [
            row.id
            for row in Organization.query.filter_by(status='active').all()
        ]

    totals = {
        'orgs': 0,
        'processed': 0,
        'delivered': 0,
        'failed': 0,
        'skipped': 0,
        'errors': 0,
    }

    db.session.remove()

    for current_org_id in org_ids:
        try:
            set_job_org_context(current_org_id)
            deliveries = NotificationOutboxService.list_pending_deliveries()
            if org_id is None:
                deliveries = [
                    d for d in deliveries if d.organization_id == current_org_id
                ]
            deliveries = deliveries[:limit]

            for delivery in deliveries:
                totals['processed'] += 1
                try:
                    ok = _deliver_one(delivery)
                    if ok:
                        NotificationOutboxService.mark_delivered(delivery.id)
                        totals['delivered'] += 1
                    else:
                        delivery.status = 'failed'
                        if not delivery.error:
                            delivery.error = 'delivery_returned_false'
                        totals['failed'] += 1
                    db.session.commit()
                    # SET LOCAL is transaction-scoped; restore after every commit.
                    set_job_org_context(current_org_id)
                except Exception as exc:
                    totals['errors'] += 1
                    logger.exception(
                        'Outbox delivery failed id=%s method=%s',
                        delivery.id, delivery.delivery_method,
                    )
                    try:
                        db.session.rollback()
                        set_job_org_context(current_org_id)
                        row = NotificationDelivery.query.get(delivery.id)
                        if row and row.status == 'queued':
                            row.status = 'failed'
                            row.error = str(exc)[:500]
                            db.session.commit()
                            totals['failed'] += 1
                            set_job_org_context(current_org_id)
                    except Exception:
                        db.session.rollback()
                        set_job_org_context(current_org_id)

            totals['orgs'] += 1
        except Exception:
            totals['errors'] += 1
            logger.exception('Outbox worker error for org %s', current_org_id)
            db.session.rollback()
            set_job_org_context(current_org_id)
        finally:
            db.session.remove()

    logger.info(
        'Notification outbox worker complete: orgs=%s processed=%s '
        'delivered=%s failed=%s errors=%s',
        totals['orgs'],
        totals['processed'],
        totals['delivered'],
        totals['failed'],
        totals['errors'],
    )
    return totals


def _deliver_one(delivery) -> bool:
    """Send a single delivery. Returns True on success."""
    from models import Notification, User, db
    from services.messaging.outbound import notify as telegram_notify
    from services.notification_service import is_channel_enabled

    event = delivery.event
    if event is None:
        delivery.error = 'missing_event'
        return False

    if event.status == 'cancelled':
        delivery.error = 'event_cancelled'
        return False

    method = (delivery.delivery_method or '').strip().lower()
    payload = event.payload or {}
    category = event.category or _CATEGORY_FALLBACK.get(
        (event.event_type or '').split('_')[0], 'task_reminder',
    )
    if category not in Notification.CATEGORIES:
        category = _CATEGORY_FALLBACK.get(category, 'task_reminder')

    title = (
        payload.get('title')
        or _title_for_event(event.event_type, payload)
    )
    body = payload.get('body') or payload.get('summary') or _body_for_event(payload)
    action_url = payload.get('action_url') or payload.get('review_url')
    if not action_url and payload.get('transaction_id'):
        action_url = f"/transactions/{payload['transaction_id']}"

    if method == 'in_app':
        if not is_channel_enabled(event.user_id, category, 'in_app'):
            delivery.error = 'preference_disabled'
            # Preference skip is not a hard failure for the event — mark sent/skipped.
            delivery.status = 'failed'
            return False

        # Insert without notification_service.create_notification (it commits).
        notif = Notification(
            user_id=event.user_id,
            organization_id=event.organization_id,
            category=category,
            title=title[:200],
            body=(body or '')[:2000] or None,
            icon=payload.get('icon') or 'fa-bell',
            action_url=action_url,
        )
        db.session.add(notif)
        db.session.flush()
        return True

    if method == 'telegram':
        # Phase 3 privacy: never push sensitive document reviews to Telegram.
        if payload.get('telegram_allowed') is False:
            delivery.error = 'telegram_blocked_privacy'
            return False
        doc_id = payload.get('document_id')
        if doc_id:
            try:
                from models import TransactionDocument
                from services.document_privacy import may_send_to_telegram
                doc = TransactionDocument.query.get(int(doc_id))
                if doc is not None and not may_send_to_telegram(doc):
                    delivery.error = 'telegram_blocked_privacy'
                    return False
            except Exception:
                delivery.error = 'telegram_privacy_check_failed'
                return False

        user = User.query.get(event.user_id)
        if user is None:
            delivery.error = 'user_not_found'
            return False
        lines = [title]
        if body:
            lines.extend(['', body[:1500]])
        if action_url:
            lines.extend(['', f'Open: {action_url}'])
        lines.extend(['', '--BOB'])
        # Quiet hours already applied via scheduled_for/not_before.
        sent = telegram_notify(
            user,
            category,
            '\n'.join(lines),
            respect_quiet_hours=False,
        )
        if not sent:
            delivery.error = 'telegram_not_sent'
            return False
        return True

    delivery.error = f'unsupported_method:{method}'
    return False


def _title_for_event(event_type: Optional[str], payload: dict) -> str:
    if event_type == 'closing_readiness_alert':
        return 'Closing readiness alert'
    if event_type and event_type.startswith('requirement_reminder_'):
        window = payload.get('window') or event_type.replace(
            'requirement_reminder_', '',
        )
        return f'Deadline reminder ({window})'
    if event_type == 'document_review_ready':
        return 'Document review ready'
    return (event_type or 'Notification').replace('_', ' ').title()


def _body_for_event(payload: dict) -> str:
    parts = []
    if payload.get('title'):
        parts.append(str(payload['title']))
    elif payload.get('requirement_key'):
        parts.append(str(payload['requirement_key']))
    if payload.get('due_at'):
        parts.append(f"Due: {payload['due_at']}")
    if payload.get('blocker_count') is not None:
        parts.append(f"{payload['blocker_count']} open blocker(s)")
    return ' · '.join(parts) if parts else ''


def main():
    parser = argparse.ArgumentParser(description='Process notification outbox deliveries')
    parser.add_argument('--org-id', type=int, default=None)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    with app.app_context():
        run_notification_outbox_worker(org_id=args.org_id, limit=args.limit)


if __name__ == '__main__':
    main()
