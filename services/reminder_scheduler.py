"""
Reminder Scheduler - Phase 1C (E1C-7)

Deterministic date and closing-readiness reminders for open transaction
requirements. Creates NotificationEvents via the outbox; never contacts
clients or third parties.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Set

from models import (
    Transaction,
    TransactionAssignment,
    TransactionRequirement,
    User,
    db,
)
from services.notification_outbox import NotificationOutboxService

logger = logging.getLogger(__name__)

# Terminal work statuses — no further reminders (DR5/DR6).
CLOSED_WORK_STATUSES = frozenset({
    'completed',
    'waived',
    'cancelled',
    'not_applicable',
    'superseded',  # amendment / replacement — stop reminders (plan DR7)
})

# Reminder windows keyed by days-until-due (overdue handled separately).
WINDOW_DAYS = {
    7: 't7',
    3: 't3',
    1: 't1',
    0: 'due_today',
}

HIGH_PRIORITY_WINDOWS = frozenset({'due_today', 'overdue'})

# Always deliver these even if a user thins their cadence prefs.
CRITICAL_WINDOWS = frozenset({'due_today', 'overdue'})

DEFAULT_ENABLED_WINDOWS = frozenset({'t7', 't3', 't1', 'due_today', 'overdue'})

REMINDER_ROLES = frozenset({'lead_agent', 'transaction_coordinator'})

CLOSING_REQUIREMENT_KEYS = frozenset({
    'closing',
    'closing_date',
    'closing_appointment',
})


class ReminderScheduler:
    """Scan open requirements and emit deduped deadline NotificationEvents."""

    @staticmethod
    def scan_organization(
        organization_id: int,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, int]:
        """
        Create reminder events for one organization.

        Returns counts: created, existing (dedupe hits), closing_alerts, requirements_scanned.
        """
        now = now or datetime.utcnow()
        today = now.date()

        requirements = (
            TransactionRequirement.query
            .filter(
                TransactionRequirement.organization_id == organization_id,
                TransactionRequirement.due_at.isnot(None),
                TransactionRequirement.work_status.notin_(tuple(CLOSED_WORK_STATUSES)),
            )
            .all()
        )

        # Prefetch assignments / transactions for recipient + closing readiness.
        tx_ids = {r.transaction_id for r in requirements}
        close_candidate_txs = ReminderScheduler._transactions_needing_closing_check(
            organization_id, today
        )
        tx_ids |= {tx.id for tx in close_candidate_txs}

        assignments_by_tx = ReminderScheduler._assignments_by_transaction(
            organization_id, tx_ids
        )
        transactions: Dict[int, Transaction] = {}
        if tx_ids:
            transactions = {
                tx.id: tx
                for tx in Transaction.query.filter(
                    Transaction.organization_id == organization_id,
                    Transaction.id.in_(list(tx_ids)),
                ).all()
            }

        created = 0
        existing = 0

        for req in requirements:
            window = ReminderScheduler.window_for_due_at(req.due_at, now)
            if not window:
                continue

            recipients = ReminderScheduler.recipients_for_transaction(
                transactions.get(req.transaction_id),
                assignments_by_tx.get(req.transaction_id, []),
            )
            if not recipients:
                continue

            bucket = today.isoformat()
            priority = 'high' if window in HIGH_PRIORITY_WINDOWS else 'normal'
            event_type = f'requirement_reminder_{window}'
            dedupe_key = f'req:{req.id}:{window}'
            payload = {
                'requirement_id': req.id,
                'requirement_key': req.requirement_key,
                'title': req.title,
                'transaction_id': req.transaction_id,
                'due_at': req.due_at.isoformat() if req.due_at else None,
                'window': window,
                'work_status': req.work_status,
                'risk_level': req.risk_level,
            }

            for user_id in recipients:
                if not ReminderScheduler.user_wants_window(user_id, window):
                    continue
                before_id = ReminderScheduler._existing_event_id(
                    user_id, dedupe_key, bucket
                )
                event = NotificationOutboxService.create_event(
                    user_id=user_id,
                    organization_id=organization_id,
                    event_type=event_type,
                    payload=payload,
                    priority=priority,
                    dedupe_key=dedupe_key,
                    dedupe_bucket=bucket,
                    related_transaction_id=req.transaction_id,
                    related_requirement_id=req.id,
                    category='deadline',
                )
                if before_id and event.id == before_id:
                    existing += 1
                else:
                    created += 1
                # Fan-out channels for the outbox worker (idempotent per method).
                ReminderScheduler._ensure_deliveries(event.id, organization_id)

        readiness_txs = {tx.id: tx for tx in close_candidate_txs}
        readiness_txs.update(transactions)
        closing_alerts = ReminderScheduler._emit_closing_readiness_alerts(
            organization_id=organization_id,
            today=today,
            transactions=list(readiness_txs.values()),
            assignments_by_tx=assignments_by_tx,
        )

        db.session.flush()
        return {
            'created': created,
            'existing': existing,
            'closing_alerts': closing_alerts,
            'requirements_scanned': len(requirements),
        }

    @staticmethod
    def window_for_due_at(due_at: datetime, now: datetime) -> Optional[str]:
        """Map due_at vs now to a reminder window, or None if outside windows."""
        if due_at is None:
            return None
        due_day = due_at.date() if isinstance(due_at, datetime) else due_at
        today = now.date() if isinstance(now, datetime) else now
        days_until = (due_day - today).days
        if days_until < 0:
            return 'overdue'
        return WINDOW_DAYS.get(days_until)

    @staticmethod
    def user_wants_window(user_id: int, window: str) -> bool:
        """
        Honor User.notification_prefs.cadence when present.

        Critical windows (due_today / overdue) always pass.
        Prefs shape:
          {"cadence": {"enabled_windows": ["t3","t1","due_today","overdue"]}}
          or {"cadence": {"skip_windows": ["t7"]}}
        """
        if window in CRITICAL_WINDOWS:
            return True
        user = User.query.get(user_id)
        prefs = getattr(user, 'notification_prefs', None) if user else None
        if not isinstance(prefs, dict):
            return True
        cadence = prefs.get('cadence')
        if not isinstance(cadence, dict):
            return True
        skip = cadence.get('skip_windows')
        if isinstance(skip, (list, tuple, set)) and window in skip:
            return False
        enabled = cadence.get('enabled_windows')
        if isinstance(enabled, (list, tuple, set)):
            return window in enabled
        return True

    @staticmethod
    def recipients_for_transaction(
        transaction: Optional[Transaction],
        assignments: Sequence[TransactionAssignment],
    ) -> List[int]:
        """
        Internal recipients only: lead_agent + transaction_coordinator.
        Fallback: transaction.created_by_id. Never clients/third parties.
        """
        user_ids: List[int] = []
        seen: Set[int] = set()
        for assignment in assignments:
            if assignment.role not in REMINDER_ROLES:
                continue
            if assignment.user_id in seen:
                continue
            seen.add(assignment.user_id)
            user_ids.append(assignment.user_id)

        if not user_ids and transaction is not None and transaction.created_by_id:
            user_ids.append(transaction.created_by_id)

        return user_ids

    @staticmethod
    def _assignments_by_transaction(
        organization_id: int,
        transaction_ids: Iterable[int],
    ) -> Dict[int, List[TransactionAssignment]]:
        ids = list(transaction_ids)
        if not ids:
            return {}
        rows = TransactionAssignment.query.filter(
            TransactionAssignment.organization_id == organization_id,
            TransactionAssignment.transaction_id.in_(ids),
            TransactionAssignment.role.in_(tuple(REMINDER_ROLES)),
        ).all()
        by_tx: Dict[int, List[TransactionAssignment]] = {}
        for row in rows:
            by_tx.setdefault(row.transaction_id, []).append(row)
        return by_tx

    @staticmethod
    def _transactions_needing_closing_check(
        organization_id: int,
        today: date,
    ) -> List[Transaction]:
        """
        Transactions whose expected_close_date or closing requirement due_at
        falls within 3 days (including past-due close).
        """
        horizon = today + timedelta(days=3)
        by_expected = Transaction.query.filter(
            Transaction.organization_id == organization_id,
            Transaction.expected_close_date.isnot(None),
            Transaction.expected_close_date <= horizon,
            Transaction.status.notin_(('closed', 'cancelled')),
        ).all()

        closing_horizon_end = datetime.combine(
            horizon + timedelta(days=1), datetime.min.time()
        )
        closing_req_tx_ids = [
            tid
            for (tid,) in TransactionRequirement.query.filter(
                TransactionRequirement.organization_id == organization_id,
                TransactionRequirement.requirement_key.in_(
                    tuple(CLOSING_REQUIREMENT_KEYS)
                ),
                TransactionRequirement.due_at.isnot(None),
                TransactionRequirement.due_at < closing_horizon_end,
                TransactionRequirement.work_status.notin_(tuple(CLOSED_WORK_STATUSES)),
            ).with_entities(TransactionRequirement.transaction_id).distinct().all()
        ]

        by_req: List[Transaction] = []
        if closing_req_tx_ids:
            by_req = Transaction.query.filter(
                Transaction.organization_id == organization_id,
                Transaction.id.in_(closing_req_tx_ids),
                Transaction.status.notin_(('closed', 'cancelled')),
            ).all()

        merged = {tx.id: tx for tx in by_expected}
        for tx in by_req:
            merged[tx.id] = tx
        return list(merged.values())

    @staticmethod
    def _closing_anchor_date(
        transaction: Transaction,
        requirements: Sequence[TransactionRequirement],
    ) -> Optional[date]:
        if transaction.expected_close_date:
            return transaction.expected_close_date
        for req in requirements:
            if req.requirement_key in CLOSING_REQUIREMENT_KEYS and req.due_at:
                return req.due_at.date()
            if req.phase_key == 'closing' and req.requirement_key == 'closing' and req.due_at:
                return req.due_at.date()
        return None

    @staticmethod
    def _open_blockers(
        requirements: Sequence[TransactionRequirement],
    ) -> List[TransactionRequirement]:
        """
        Open blockers: high/critical risk, or incomplete required work.

        Without a required column on TransactionRequirement, treat any open
        (non-terminal) requirement as incomplete required work.
        """
        blockers = []
        for req in requirements:
            if req.work_status in CLOSED_WORK_STATUSES:
                continue
            risk = (req.risk_level or '').lower()
            if risk in ('high', 'critical'):
                blockers.append(req)
                continue
            # Incomplete required / open work
            blockers.append(req)
        return blockers

    @staticmethod
    def _emit_closing_readiness_alerts(
        *,
        organization_id: int,
        today: date,
        transactions: Sequence[Transaction],
        assignments_by_tx: Dict[int, List[TransactionAssignment]],
    ) -> int:
        emitted = 0
        horizon = today + timedelta(days=3)
        seen_tx: Set[int] = set()

        for tx in transactions:
            if tx.id in seen_tx:
                continue
            seen_tx.add(tx.id)
            if tx.status in ('closed', 'cancelled'):
                continue

            reqs = TransactionRequirement.query.filter_by(
                organization_id=organization_id,
                transaction_id=tx.id,
            ).all()

            close_date = ReminderScheduler._closing_anchor_date(tx, reqs)
            if close_date is None or close_date > horizon:
                # Also catch closing requirement due within 3 days when
                # expected_close_date is unset / farther out.
                closing_req_due = None
                for req in reqs:
                    if req.requirement_key in CLOSING_REQUIREMENT_KEYS and req.due_at:
                        closing_req_due = req.due_at.date()
                        break
                if closing_req_due is None or closing_req_due > horizon:
                    continue
                close_date = closing_req_due

            blockers = ReminderScheduler._open_blockers(reqs)
            if not blockers:
                continue

            recipients = ReminderScheduler.recipients_for_transaction(
                tx,
                assignments_by_tx.get(tx.id) or ReminderScheduler._assignments_by_transaction(
                    organization_id, [tx.id]
                ).get(tx.id, []),
            )
            if not recipients:
                continue

            bucket = today.isoformat()
            dedupe_key = f'tx:{tx.id}:closing_readiness'
            payload = {
                'transaction_id': tx.id,
                'close_date': close_date.isoformat(),
                'blocker_count': len(blockers),
                'blockers': [
                    {
                        'requirement_id': b.id,
                        'title': b.title,
                        'work_status': b.work_status,
                        'risk_level': b.risk_level,
                    }
                    for b in blockers[:20]
                ],
            }

            for user_id in recipients:
                before_id = ReminderScheduler._existing_event_id(
                    user_id, dedupe_key, bucket
                )
                event = NotificationOutboxService.create_event(
                    user_id=user_id,
                    organization_id=organization_id,
                    event_type='closing_readiness_alert',
                    payload=payload,
                    priority='high',
                    dedupe_key=dedupe_key,
                    dedupe_bucket=bucket,
                    related_transaction_id=tx.id,
                    category='deadline',
                )
                if before_id is None or event.id != before_id:
                    emitted += 1
                ReminderScheduler._ensure_deliveries(event.id, organization_id)

        return emitted

    @staticmethod
    def _ensure_deliveries(event_id: int, organization_id: int) -> None:
        """Create in_app + telegram deliveries if missing for this event."""
        from models import NotificationDelivery
        existing = {
            d.delivery_method
            for d in NotificationDelivery.query.filter_by(event_id=event_id).all()
        }
        for method in ('in_app', 'telegram'):
            if method not in existing:
                NotificationOutboxService.create_delivery(
                    event_id, organization_id, method,
                )

    @staticmethod
    def _existing_event_id(
        user_id: int,
        dedupe_key: str,
        dedupe_bucket: str,
    ) -> Optional[int]:
        from models import NotificationEvent
        row = NotificationEvent.query.filter_by(
            user_id=user_id,
            dedupe_key=dedupe_key,
            dedupe_bucket=dedupe_bucket,
        ).first()
        return row.id if row else None


def scan_reminders_for_org(
    organization_id: int,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Convenience wrapper used by the background job."""
    return ReminderScheduler.scan_organization(organization_id, now=now)
