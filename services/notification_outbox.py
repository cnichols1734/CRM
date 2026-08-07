"""
Notification Outbox Service - Phase 1C

Manages NotificationEvent and NotificationDelivery for in-app/push notifications.
Implements dedupe, quiet-hours rescheduling (never discard), and snooze.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import List, Optional

from sqlalchemy.exc import IntegrityError

from models import (
    NotificationDelivery,
    NotificationEvent,
    db,
)


class NotificationOutboxService:
    """
    Service for managing notification events and deliveries.

    Implements outbox pattern with quiet hours rescheduling.
    """

    # Quiet hours: 10 PM to 8 AM (UTC wall clock for scheduling)
    QUIET_HOURS_START = time(22, 0)  # 10 PM
    QUIET_HOURS_END = time(8, 0)     # 8 AM

    @staticmethod
    def create_event(
        user_id: int,
        organization_id: int,
        event_type: str,
        payload: Optional[dict] = None,
        priority: str = 'normal',
        *,
        dedupe_key: Optional[str] = None,
        dedupe_bucket: Optional[str] = None,
        not_before: Optional[datetime] = None,
        related_transaction_id: Optional[int] = None,
        related_requirement_id: Optional[int] = None,
        category: Optional[str] = None,
        escalation_level: int = 0,
    ) -> NotificationEvent:
        """
        Create a notification event (idempotent when dedupe_key+bucket set).

        Quiet hours set ``not_before`` to the next allowed window — never discard.

        If ``(user_id, dedupe_key, dedupe_bucket)`` already exists, returns the
        existing event without creating a duplicate.
        """
        if dedupe_key is not None:
            existing = NotificationEvent.query.filter_by(
                user_id=user_id,
                dedupe_key=dedupe_key,
                dedupe_bucket=dedupe_bucket,
            ).first()
            if existing:
                return existing

        effective_not_before = not_before or datetime.utcnow()
        quiet_until = NotificationOutboxService._reschedule_if_quiet_hours(
            effective_not_before,
            user_id=user_id,
        )
        if quiet_until and (effective_not_before is None or quiet_until > effective_not_before):
            effective_not_before = quiet_until

        # If not_before landed on "now" outside quiet hours, leave unset so
        # workers can deliver immediately.
        if effective_not_before and effective_not_before <= datetime.utcnow() and not quiet_until:
            effective_not_before = None

        event = NotificationEvent(
            user_id=user_id,
            organization_id=organization_id,
            event_type=event_type,
            payload=payload or {},
            priority=priority,
            status='pending',
            dedupe_key=dedupe_key,
            dedupe_bucket=dedupe_bucket,
            not_before=effective_not_before,
            related_transaction_id=related_transaction_id,
            related_requirement_id=related_requirement_id,
            category=category,
            escalation_level=escalation_level,
        )
        try:
            # Nested savepoint so a race on the dedupe unique index does not
            # roll back unrelated work already staged in the outer session.
            with db.session.begin_nested():
                db.session.add(event)
                db.session.flush()
        except IntegrityError:
            existing = NotificationEvent.query.filter_by(
                user_id=user_id,
                dedupe_key=dedupe_key,
                dedupe_bucket=dedupe_bucket,
            ).first()
            if existing:
                return existing
            raise
        return event

    @staticmethod
    def create_delivery(
        event_id: int,
        organization_id: int,
        delivery_method: str,
    ) -> NotificationDelivery:
        """
        Create a notification delivery.

        Respects event.not_before, snoozed_until, and quiet hours by setting
        ``scheduled_for`` (reschedule, never discard).
        """
        event = NotificationEvent.query.get(event_id)
        if not event:
            raise ValueError(f'NotificationEvent {event_id} not found')

        now = datetime.utcnow()
        scheduled_for = None

        # Honor event-level hold times
        hold_until = None
        if event.not_before and event.not_before > now:
            hold_until = event.not_before
        if event.snoozed_until and event.snoozed_until > now:
            if hold_until is None or event.snoozed_until > hold_until:
                hold_until = event.snoozed_until

        candidate = hold_until or now
        quiet_until = NotificationOutboxService._reschedule_if_quiet_hours(
            candidate, user_id=event.user_id,
        )
        if quiet_until and (hold_until is None or quiet_until > hold_until):
            scheduled_for = quiet_until
        elif hold_until:
            scheduled_for = hold_until

        delivery = NotificationDelivery(
            event_id=event_id,
            organization_id=organization_id,
            delivery_method=delivery_method,
            status='queued',
            scheduled_for=scheduled_for,
        )
        db.session.add(delivery)
        db.session.flush()
        return delivery

    @staticmethod
    def snooze_event(event_id: int, until: datetime) -> NotificationEvent:
        """
        Snooze an event until ``until``. Delivery workers must not send before then.
        """
        event = NotificationEvent.query.get(event_id)
        if not event:
            raise ValueError(f'NotificationEvent {event_id} not found')

        event.snoozed_until = until
        # Keep not_before at least as late as snooze so reprocessing respects it.
        if event.not_before is None or event.not_before < until:
            event.not_before = until

        # Push any queued deliveries out as well (reschedule, never discard).
        for delivery in event.deliveries.filter_by(status='queued').all():
            if delivery.scheduled_for is None or delivery.scheduled_for < until:
                delivery.scheduled_for = until

        db.session.flush()
        return event

    @staticmethod
    def cancel_events_for_requirement(requirement_id: int) -> int:
        """
        Cancel pending/processing events tied to a requirement.

        Used when due dates change (amendment) or the requirement is completed/
        waived/cancelled. History is preserved (status flip, not delete).

        Returns:
            Number of events cancelled.
        """
        events = NotificationEvent.query.filter(
            NotificationEvent.related_requirement_id == requirement_id,
            NotificationEvent.status.in_(('pending', 'processing')),
        ).all()

        cancelled = 0
        for event in events:
            event.status = 'cancelled'
            cancelled += 1
            for delivery in event.deliveries.filter(
                NotificationDelivery.status.in_(('queued', 'pending'))
            ).all():
                delivery.status = 'failed'
                delivery.error = 'cancelled_with_event'

        db.session.flush()
        return cancelled

    @staticmethod
    def _quiet_hours_for_user(user_id: Optional[int]) -> tuple[time, time]:
        """Return (start, end) quiet-hour times; honor User.notification_prefs when set."""
        start = NotificationOutboxService.QUIET_HOURS_START
        end = NotificationOutboxService.QUIET_HOURS_END
        if not user_id:
            return start, end
        try:
            from models import User
            user = User.query.get(user_id)
            prefs = getattr(user, 'notification_prefs', None) if user else None
            quiet = (prefs or {}).get('quiet_hours') if isinstance(prefs, dict) else None
            if isinstance(quiet, dict):
                parsed_start = NotificationOutboxService._parse_hhmm(quiet.get('start'))
                parsed_end = NotificationOutboxService._parse_hhmm(quiet.get('end'))
                if parsed_start is not None:
                    start = parsed_start
                if parsed_end is not None:
                    end = parsed_end
        except Exception:
            pass
        return start, end

    @staticmethod
    def _parse_hhmm(value) -> Optional[time]:
        if value is None:
            return None
        text = str(value).strip()
        parts = text.split(':')
        if len(parts) < 2:
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except (TypeError, ValueError):
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return time(hour, minute)

    @staticmethod
    def _reschedule_if_quiet_hours(
        now: datetime,
        *,
        user_id: Optional[int] = None,
    ) -> Optional[datetime]:
        """
        If ``now`` falls in quiet hours, return next allowed send time.
        Otherwise return None. Never discards — only reschedules.
        """
        quiet_start, quiet_end = NotificationOutboxService._quiet_hours_for_user(user_id)
        current_time = now.time()

        in_quiet = (
            quiet_start <= current_time or current_time < quiet_end
            if quiet_start > quiet_end  # wraps midnight (typical)
            else quiet_start <= current_time < quiet_end
        )
        if not in_quiet:
            return None

        next_allowed = now.replace(
            hour=quiet_end.hour,
            minute=quiet_end.minute,
            second=0,
            microsecond=0,
        )
        if quiet_start > quiet_end and current_time >= quiet_start:
            next_allowed += timedelta(days=1)
        elif quiet_start <= quiet_end and current_time >= quiet_start:
            # Same-day quiet window; end may already be past — bump to next day.
            if next_allowed <= now:
                next_allowed += timedelta(days=1)
        return next_allowed

    @staticmethod
    def mark_delivered(delivery_id: int) -> NotificationDelivery:
        """Mark a delivery as delivered."""
        delivery = NotificationDelivery.query.get(delivery_id)
        if not delivery:
            raise ValueError(f'NotificationDelivery {delivery_id} not found')

        delivery.status = 'sent'
        delivery.delivered_at = datetime.utcnow()
        db.session.flush()

        event = delivery.event
        all_sent = all(d.status == 'sent' for d in event.deliveries)
        if all_sent:
            event.status = 'delivered'
            db.session.flush()

        return delivery

    @staticmethod
    def mark_read(delivery_id: int) -> NotificationDelivery:
        """Mark a delivery as read."""
        delivery = NotificationDelivery.query.get(delivery_id)
        if not delivery:
            raise ValueError(f'NotificationDelivery {delivery_id} not found')

        delivery.status = 'read'
        delivery.read_at = datetime.utcnow()
        db.session.flush()
        return delivery

    @staticmethod
    def list_pending_deliveries(
        delivery_method: Optional[str] = None,
    ) -> List[NotificationDelivery]:
        """
        List pending deliveries ready to send.

        Skips deliveries whose parent event is cancelled, snoozed, or
        ``not_before`` is still in the future.
        """
        now = datetime.utcnow()
        query = (
            NotificationDelivery.query
            .join(NotificationEvent, NotificationDelivery.event_id == NotificationEvent.id)
            .filter(NotificationDelivery.status == 'queued')
            .filter(NotificationEvent.status.in_(('pending', 'processing', 'delivered')))
            .filter(
                (NotificationEvent.not_before.is_(None)) |
                (NotificationEvent.not_before <= now)
            )
            .filter(
                (NotificationEvent.snoozed_until.is_(None)) |
                (NotificationEvent.snoozed_until <= now)
            )
            .filter(
                (NotificationDelivery.scheduled_for.is_(None)) |
                (NotificationDelivery.scheduled_for <= now)
            )
        )

        if delivery_method:
            query = query.filter(NotificationDelivery.delivery_method == delivery_method)

        return query.order_by(NotificationDelivery.created_at.asc()).all()
