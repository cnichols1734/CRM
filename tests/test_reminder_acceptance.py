"""§34A Date reminders acceptance (DR2, DR4, DR5, DR10)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from models import (
    NotificationEvent,
    Transaction,
    TransactionAssignment,
    TransactionRequirement,
    db,
)
from services.notification_outbox import NotificationOutboxService
from services.reminder_scheduler import ReminderScheduler


def _ensure_lead_assignment(org_id, tx_id, user_id):
    existing = TransactionAssignment.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
        user_id=user_id,
    ).first()
    if existing:
        existing.role = 'lead_agent'
        return existing
    row = TransactionAssignment(
        organization_id=org_id,
        transaction_id=tx_id,
        user_id=user_id,
        role='lead_agent',
    )
    db.session.add(row)
    db.session.flush()
    return row


def _make_open_requirement(org_id, tx_id, *, due_at, key='option_period_end'):
    req = TransactionRequirement(
        organization_id=org_id,
        transaction_id=tx_id,
        package_key='seller_ctc',
        phase_key='option_period',
        requirement_key=key,
        title='Option period ends',
        work_status='pending',
        due_at=due_at,
    )
    db.session.add(req)
    db.session.flush()
    return req


def _cleanup_requirement(req_id):
    if not req_id:
        return
    NotificationEvent.query.filter_by(
        related_requirement_id=req_id,
    ).delete(synchronize_session=False)
    TransactionRequirement.query.filter_by(id=req_id).delete(
        synchronize_session=False,
    )
    db.session.commit()


def test_dr2_quiet_hours_sets_not_before_not_discarded(app, seed):
    """
    DR2: Given a reminder is due during quiet hours,
    When the outbox creates the event / delivery,
    Then not_before / scheduled_for are set after quiet hours (not discarded).
    """
    with app.app_context():
        quiet_now = datetime(2026, 8, 4, 23, 30, 0)
        with patch(
            'services.notification_outbox.datetime',
            wraps=datetime,
        ) as mock_dt:
            mock_dt.utcnow.return_value = quiet_now

            event = NotificationOutboxService.create_event(
                user_id=seed['owner_a'],
                organization_id=seed['org_a'],
                event_type='requirement_reminder_t1',
                payload={'window': 't1'},
                priority='normal',
                dedupe_key='req:dr2-quiet:t1',
                dedupe_bucket='2026-08-04',
                category='deadline',
            )
            delivery = NotificationOutboxService.create_delivery(
                event.id, seed['org_a'], 'in_app',
            )

        assert event.status == 'pending'
        assert event.not_before is not None
        assert event.not_before == datetime(2026, 8, 5, 8, 0, 0)
        assert delivery.scheduled_for is not None
        assert delivery.scheduled_for == datetime(2026, 8, 5, 8, 0, 0)
        assert delivery.status == 'queued'


def test_dr4_scan_twice_same_day_single_event(app, seed):
    """
    DR4: Given cron fires twice for the same reminder bucket,
    When ReminderScheduler.scan_organization runs twice the same day,
    Then a single NotificationEvent remains for that dedupe key.
    """
    req_id = None
    with app.app_context():
        try:
            org_id = seed['org_a']
            tx = Transaction.query.get(seed['tx_a'])
            _ensure_lead_assignment(org_id, tx.id, seed['owner_a'])

            now = datetime(2026, 8, 4, 14, 0, 0)
            due_at = datetime(2026, 8, 11, 17, 0, 0)  # T-7 window
            req = _make_open_requirement(
                org_id, tx.id, due_at=due_at, key='earnest_money_dr4',
            )
            req_id = req.id
            dedupe_key = f'req:{req.id}:t7'
            bucket = now.date().isoformat()

            stats1 = ReminderScheduler.scan_organization(org_id, now=now)
            stats2 = ReminderScheduler.scan_organization(org_id, now=now)
            db.session.flush()

            events = NotificationEvent.query.filter_by(
                user_id=seed['owner_a'],
                dedupe_key=dedupe_key,
                dedupe_bucket=bucket,
            ).all()
            assert len(events) == 1
            assert stats1['created'] >= 1
            assert stats2['existing'] >= 1 or stats2['created'] == 0
        finally:
            _cleanup_requirement(req_id)


def test_dr5_cancel_events_for_requirement_stops_pending(app, seed):
    """
    DR5 / cancel: Given pending reminder events for a requirement,
    When cancel_events_for_requirement runs (completed/waived path),
    Then pending events are cancelled and further scans do not revive them
    once the requirement is completed.
    """
    req_id = None
    with app.app_context():
        try:
            org_id = seed['org_a']
            tx = Transaction.query.get(seed['tx_a'])
            _ensure_lead_assignment(org_id, tx.id, seed['owner_a'])

            now = datetime(2026, 8, 4, 14, 0, 0)
            due_at = now + timedelta(days=3)
            req = _make_open_requirement(
                org_id, tx.id, due_at=due_at, key='option_fee_dr5',
            )
            req_id = req.id

            ReminderScheduler.scan_organization(org_id, now=now)
            db.session.flush()

            pending = NotificationEvent.query.filter_by(
                related_requirement_id=req.id,
                status='pending',
            ).all()
            assert pending

            cancelled = NotificationOutboxService.cancel_events_for_requirement(req.id)
            assert cancelled >= 1
            assert NotificationEvent.query.filter_by(
                related_requirement_id=req.id,
                status='pending',
            ).count() == 0
            assert NotificationEvent.query.filter_by(
                related_requirement_id=req.id,
                status='cancelled',
            ).count() >= 1

            req.work_status = 'completed'
            db.session.flush()

            before = NotificationEvent.query.filter_by(
                related_requirement_id=req.id,
            ).count()
            ReminderScheduler.scan_organization(org_id, now=now)
            db.session.flush()
            after = NotificationEvent.query.filter_by(
                related_requirement_id=req.id,
            ).count()
            assert after == before
            assert NotificationEvent.query.filter_by(
                related_requirement_id=req.id,
                status='pending',
            ).count() == 0
        finally:
            _cleanup_requirement(req_id)


def test_dr10_reminder_path_never_sends_client_or_third_party_email(app, seed):
    """
    DR10: Given any reminder scan path,
    When reminders are created,
    Then no ContactEmail / Gmail client-send path is invoked.
    """
    req_id = None
    with app.app_context():
        try:
            org_id = seed['org_a']
            tx = Transaction.query.get(seed['tx_a'])
            _ensure_lead_assignment(org_id, tx.id, seed['owner_a'])
            now = datetime(2026, 8, 4, 14, 0, 0)
            req = _make_open_requirement(
                org_id,
                tx.id,
                due_at=now + timedelta(days=1),
                key='inspection_dr10',
            )
            req_id = req.id

            with patch('models.ContactEmail') as contact_email_cls, \
                 patch('services.gmail_service.send_email', create=True) as gmail_fn, \
                 patch('services.email_service.EmailService', autospec=True) as email_svc:
                ReminderScheduler.scan_organization(org_id, now=now)
                db.session.flush()

            contact_email_cls.assert_not_called()
            gmail_fn.assert_not_called()
            email_svc.assert_not_called()

            deadline_events = NotificationEvent.query.filter_by(
                organization_id=org_id,
                category='deadline',
                user_id=seed['owner_a'],
            ).all()
            assert deadline_events
            for event in deadline_events:
                assert event.user_id == seed['owner_a']
        finally:
            _cleanup_requirement(req_id)
