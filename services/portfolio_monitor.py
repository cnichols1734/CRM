"""
Portfolio Monitor - Phase 2 (E2-1)

Practical (non-ML) scan of an org's open transactions for:
- stale files (no activity / overdue requirements)
- third-party SLA breaches (waiting_on title/lender/etc. past threshold)
- portfolio risk score + brokerage compliance escalation
- weekly portfolio digest events

Emits NotificationEvents via NotificationOutboxService with stable dedupe keys.
Never contacts clients or third parties.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from models import (
    Transaction,
    TransactionRequirement,
    User,
    db,
)
from services.notification_outbox import NotificationOutboxService
from services.reminder_scheduler import ReminderScheduler

logger = logging.getLogger(__name__)

CLOSED_TX_STATUSES = frozenset({'closed', 'cancelled'})
CLOSED_REQ_STATUSES = frozenset({
    'completed', 'waived', 'cancelled', 'not_applicable', 'superseded',
})

# Requirements waiting on external parties (SLA clock).
THIRD_PARTY_RESPONSIBILITY = frozenset({
    'title', 'lender', 'inspector', 'surveyor', 'hoa', 'third_party',
    'escrow', 'underwriter', 'appraisal', 'appraiser',
})

# Defaults (days). Overridable via scan kwargs.
DEFAULT_STALE_DAYS = 7
DEFAULT_SLA_DAYS = 3
DEFAULT_ACTIVITY_STALE_DAYS = 10

ESCALATION_ROLES = frozenset({'owner', 'admin'})
MONITOR_ASSIGNEE_ROLES = frozenset({'lead_agent', 'transaction_coordinator'})


class PortfolioMonitor:
    """Scan org portfolio health and enqueue internal NotificationEvents."""

    @staticmethod
    def scan_organization(
        organization_id: int,
        *,
        now: Optional[datetime] = None,
        stale_days: int = DEFAULT_STALE_DAYS,
        sla_days: int = DEFAULT_SLA_DAYS,
        activity_stale_days: int = DEFAULT_ACTIVITY_STALE_DAYS,
    ) -> Dict[str, int]:
        """
        Run stale / SLA / risk / compliance scans for one organization.

        Returns counts of created events and scanned transactions.
        """
        now = now or datetime.utcnow()
        today = now.date()

        transactions = (
            Transaction.query
            .filter(
                Transaction.organization_id == organization_id,
                Transaction.status.notin_(tuple(CLOSED_TX_STATUSES)),
            )
            .all()
        )
        if not transactions:
            return {
                'transactions_scanned': 0,
                'stale': 0,
                'sla_breaches': 0,
                'risk_alerts': 0,
                'compliance_escalations': 0,
                'created': 0,
                'existing': 0,
            }

        tx_ids = [tx.id for tx in transactions]
        requirements = (
            TransactionRequirement.query
            .filter(
                TransactionRequirement.organization_id == organization_id,
                TransactionRequirement.transaction_id.in_(tx_ids),
            )
            .all()
        )
        reqs_by_tx: Dict[int, List[TransactionRequirement]] = {}
        for req in requirements:
            reqs_by_tx.setdefault(req.transaction_id, []).append(req)

        assignments_by_tx = ReminderScheduler._assignments_by_transaction(
            organization_id, tx_ids
        )
        owners_admins = PortfolioMonitor._owner_admin_user_ids(organization_id)

        created = 0
        existing = 0
        stale_n = 0
        sla_n = 0
        risk_n = 0
        compliance_n = 0

        portfolio_rows: List[dict] = []

        for tx in transactions:
            reqs = reqs_by_tx.get(tx.id, [])
            open_reqs = [r for r in reqs if r.work_status not in CLOSED_REQ_STATUSES]
            overdue = [
                r for r in open_reqs
                if r.due_at and (
                    (r.due_at.date() if isinstance(r.due_at, datetime) else r.due_at) < today
                )
            ]
            high_risk = [
                r for r in open_reqs
                if (r.risk_level or '').lower() in ('high', 'critical')
            ]
            sla_breaches = PortfolioMonitor._sla_breaches_for_tx(
                open_reqs, now=now, sla_days=sla_days,
            )
            is_stale = PortfolioMonitor._is_stale(
                tx, open_reqs, overdue, now=now,
                stale_days=stale_days,
                activity_stale_days=activity_stale_days,
            )
            risk = PortfolioMonitor.score_transaction_risk(
                overdue_count=len(overdue),
                high_risk_count=len(high_risk),
                sla_breach_count=len(sla_breaches),
                is_stale=is_stale,
                days_to_close=PortfolioMonitor._days_to_close(tx, today),
            )

            portfolio_rows.append({
                'transaction_id': tx.id,
                'address': tx.street_address,
                'status': tx.status,
                'risk_score': risk['score'],
                'risk_band': risk['band'],
                'overdue_count': len(overdue),
                'sla_breach_count': len(sla_breaches),
                'is_stale': is_stale,
            })

            recipients = ReminderScheduler.recipients_for_transaction(
                tx, assignments_by_tx.get(tx.id, []),
            )

            if is_stale and recipients:
                c, e = PortfolioMonitor._emit_for_users(
                    user_ids=recipients,
                    organization_id=organization_id,
                    event_type='portfolio_stale_transaction',
                    payload={
                        'transaction_id': tx.id,
                        'address': tx.street_address,
                        'status': tx.status,
                        'overdue_count': len(overdue),
                        'open_requirement_count': len(open_reqs),
                        'updated_at': tx.updated_at.isoformat() if tx.updated_at else None,
                    },
                    priority='normal',
                    dedupe_key=f'tx:{tx.id}:stale',
                    dedupe_bucket=today.isoformat(),
                    related_transaction_id=tx.id,
                    category='portfolio',
                )
                created += c
                existing += e
                stale_n += 1

            for breach in sla_breaches:
                if not recipients:
                    break
                c, e = PortfolioMonitor._emit_for_users(
                    user_ids=recipients,
                    organization_id=organization_id,
                    event_type='portfolio_sla_breach',
                    payload={
                        'transaction_id': tx.id,
                        'address': tx.street_address,
                        'requirement_id': breach['requirement_id'],
                        'requirement_key': breach['requirement_key'],
                        'title': breach['title'],
                        'waiting_on': breach['waiting_on'],
                        'days_waiting': breach['days_waiting'],
                        'threshold_days': sla_days,
                    },
                    priority='high',
                    dedupe_key=f'req:{breach["requirement_id"]}:sla',
                    dedupe_bucket=today.isoformat(),
                    related_transaction_id=tx.id,
                    related_requirement_id=breach['requirement_id'],
                    category='portfolio',
                )
                created += c
                existing += e
                sla_n += 1

            if risk['band'] in ('elevated', 'critical') and recipients:
                c, e = PortfolioMonitor._emit_for_users(
                    user_ids=recipients,
                    organization_id=organization_id,
                    event_type='portfolio_risk_alert',
                    payload={
                        'transaction_id': tx.id,
                        'address': tx.street_address,
                        'risk_score': risk['score'],
                        'risk_band': risk['band'],
                        'factors': risk['factors'],
                    },
                    priority='high' if risk['band'] == 'critical' else 'normal',
                    dedupe_key=f'tx:{tx.id}:risk:{risk["band"]}',
                    dedupe_bucket=today.isoformat(),
                    related_transaction_id=tx.id,
                    category='portfolio',
                )
                created += c
                existing += e
                risk_n += 1

            # Brokerage-wide compliance: high/critical overdue or critical risk.
            needs_escalation = (
                risk['band'] == 'critical'
                or any(
                    (r.risk_level or '').lower() in ('high', 'critical')
                    for r in overdue
                )
            )
            if needs_escalation and owners_admins:
                c, e = PortfolioMonitor._emit_for_users(
                    user_ids=owners_admins,
                    organization_id=organization_id,
                    event_type='portfolio_compliance_escalation',
                    payload={
                        'transaction_id': tx.id,
                        'address': tx.street_address,
                        'risk_score': risk['score'],
                        'risk_band': risk['band'],
                        'overdue_high_risk': [
                            {
                                'requirement_id': r.id,
                                'title': r.title,
                                'risk_level': r.risk_level,
                            }
                            for r in overdue
                            if (r.risk_level or '').lower() in ('high', 'critical')
                        ][:10],
                    },
                    priority='high',
                    dedupe_key=f'tx:{tx.id}:compliance',
                    dedupe_bucket=today.isoformat(),
                    related_transaction_id=tx.id,
                    category='compliance',
                    escalation_level=1,
                )
                created += c
                existing += e
                compliance_n += 1

        db.session.flush()
        return {
            'transactions_scanned': len(transactions),
            'stale': stale_n,
            'sla_breaches': sla_n,
            'risk_alerts': risk_n,
            'compliance_escalations': compliance_n,
            'created': created,
            'existing': existing,
            'portfolio_summary': {
                'elevated': sum(1 for r in portfolio_rows if r['risk_band'] == 'elevated'),
                'critical': sum(1 for r in portfolio_rows if r['risk_band'] == 'critical'),
                'stale': sum(1 for r in portfolio_rows if r['is_stale']),
            },
        }

    @staticmethod
    def generate_weekly_report(
        organization_id: int,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, int]:
        """
        Emit one weekly portfolio digest per owner/admin (deduped by ISO week).

        Read-only aggregation — does not re-emit daily stale/SLA/risk events
        (those stay in ``scan_organization`` / the portfolio_monitor job).
        """
        now = now or datetime.utcnow()
        today = now.date()
        week_bucket = f'{today.isocalendar().year}-W{today.isocalendar().week:02d}'

        owners_admins = PortfolioMonitor._owner_admin_user_ids(organization_id)
        if not owners_admins:
            return {'created': 0, 'existing': 0, 'recipients': 0}

        summary = PortfolioMonitor.summarize_organization(organization_id, now=now)
        payload = {
            'week': week_bucket,
            'open_transactions': summary['open_transactions'],
            'overdue_requirements': summary['overdue_requirements'],
            'stale_transactions': summary['stale_transactions'],
            'elevated_risk': summary['elevated_risk'],
            'critical_risk': summary['critical_risk'],
            'sla_breaches_flagged': summary['sla_breaches'],
            'compliance_watch': summary['compliance_watch'],
        }
        open_txs = summary['open_transactions']
        overdue_count = summary['overdue_requirements']

        created = 0
        existing = 0
        for user_id in owners_admins:
            before = PortfolioMonitor._existing_event_id(
                user_id, f'org:{organization_id}:weekly_portfolio', week_bucket,
            )
            event = NotificationOutboxService.create_event(
                user_id=user_id,
                organization_id=organization_id,
                event_type='weekly_portfolio_report',
                payload=payload,
                priority='normal',
                dedupe_key=f'org:{organization_id}:weekly_portfolio',
                dedupe_bucket=week_bucket,
                category='portfolio',
            )
            PortfolioMonitor._ensure_deliveries(event.id, organization_id)
            if before and event.id == before:
                existing += 1
            else:
                created += 1

        db.session.flush()
        return {
            'created': created,
            'existing': existing,
            'recipients': len(owners_admins),
            'open_transactions': open_txs,
            'overdue_requirements': overdue_count,
        }

    # ------------------------------------------------------------------
    # Scoring / detection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def summarize_organization(
        organization_id: int,
        *,
        now: Optional[datetime] = None,
        stale_days: int = DEFAULT_STALE_DAYS,
        sla_days: int = DEFAULT_SLA_DAYS,
        activity_stale_days: int = DEFAULT_ACTIVITY_STALE_DAYS,
    ) -> Dict[str, int]:
        """Aggregate portfolio stats without creating NotificationEvents."""
        now = now or datetime.utcnow()
        today = now.date()

        transactions = (
            Transaction.query
            .filter(
                Transaction.organization_id == organization_id,
                Transaction.status.notin_(tuple(CLOSED_TX_STATUSES)),
            )
            .all()
        )
        tx_ids = [tx.id for tx in transactions]
        requirements: List[TransactionRequirement] = []
        if tx_ids:
            requirements = (
                TransactionRequirement.query
                .filter(
                    TransactionRequirement.organization_id == organization_id,
                    TransactionRequirement.transaction_id.in_(tx_ids),
                )
                .all()
            )
        reqs_by_tx: Dict[int, List[TransactionRequirement]] = {}
        for req in requirements:
            reqs_by_tx.setdefault(req.transaction_id, []).append(req)

        stale_n = 0
        elevated = 0
        critical = 0
        sla_n = 0
        compliance_watch = 0
        overdue_total = 0

        for tx in transactions:
            reqs = reqs_by_tx.get(tx.id, [])
            open_reqs = [r for r in reqs if r.work_status not in CLOSED_REQ_STATUSES]
            overdue = [
                r for r in open_reqs
                if r.due_at and (
                    (r.due_at.date() if isinstance(r.due_at, datetime) else r.due_at) < today
                )
            ]
            overdue_total += len(overdue)
            high_risk = [
                r for r in open_reqs
                if (r.risk_level or '').lower() in ('high', 'critical')
            ]
            sla_breaches = PortfolioMonitor._sla_breaches_for_tx(
                open_reqs, now=now, sla_days=sla_days,
            )
            sla_n += len(sla_breaches)
            is_stale = PortfolioMonitor._is_stale(
                tx, open_reqs, overdue, now=now,
                stale_days=stale_days,
                activity_stale_days=activity_stale_days,
            )
            if is_stale:
                stale_n += 1
            risk = PortfolioMonitor.score_transaction_risk(
                overdue_count=len(overdue),
                high_risk_count=len(high_risk),
                sla_breach_count=len(sla_breaches),
                is_stale=is_stale,
                days_to_close=PortfolioMonitor._days_to_close(tx, today),
            )
            if risk['band'] == 'elevated':
                elevated += 1
            elif risk['band'] == 'critical':
                critical += 1
            if risk['band'] == 'critical' or any(
                (r.risk_level or '').lower() in ('high', 'critical') for r in overdue
            ):
                compliance_watch += 1

        return {
            'open_transactions': len(transactions),
            'overdue_requirements': overdue_total,
            'stale_transactions': stale_n,
            'elevated_risk': elevated,
            'critical_risk': critical,
            'sla_breaches': sla_n,
            'compliance_watch': compliance_watch,
        }

    @staticmethod
    def score_transaction_risk(
        *,
        overdue_count: int = 0,
        high_risk_count: int = 0,
        sla_breach_count: int = 0,
        is_stale: bool = False,
        days_to_close: Optional[int] = None,
    ) -> Dict:
        """
        Deterministic 0–100 risk score with band labels.

        Practical heuristics — not ML. Higher = more attention needed.
        """
        score = 0
        factors: List[str] = []

        if overdue_count:
            add = min(40, overdue_count * 12)
            score += add
            factors.append(f'{overdue_count} overdue')
        if high_risk_count:
            add = min(30, high_risk_count * 15)
            score += add
            factors.append(f'{high_risk_count} high/critical risk')
        if sla_breach_count:
            add = min(25, sla_breach_count * 10)
            score += add
            factors.append(f'{sla_breach_count} SLA breach(es)')
        if is_stale:
            score += 15
            factors.append('stale file')
        if days_to_close is not None and days_to_close <= 3 and overdue_count:
            score += 20
            factors.append('closing within 3 days with open overdue work')

        score = max(0, min(100, score))
        if score >= 70:
            band = 'critical'
        elif score >= 40:
            band = 'elevated'
        elif score >= 15:
            band = 'watch'
        else:
            band = 'healthy'
        return {'score': score, 'band': band, 'factors': factors}

    @staticmethod
    def _is_stale(
        tx: Transaction,
        open_reqs: Sequence[TransactionRequirement],
        overdue: Sequence[TransactionRequirement],
        *,
        now: datetime,
        stale_days: int,
        activity_stale_days: int,
    ) -> bool:
        if overdue:
            oldest = min(
                (r.due_at for r in overdue if r.due_at),
                default=None,
            )
            if oldest is not None:
                due_day = oldest.date() if isinstance(oldest, datetime) else oldest
                if (now.date() - due_day).days >= stale_days:
                    return True

        anchor = tx.updated_at or tx.created_at
        if anchor is None:
            return bool(open_reqs)
        idle_days = (now - anchor).days
        if idle_days >= activity_stale_days and open_reqs:
            return True
        return False

    @staticmethod
    def _sla_breaches_for_tx(
        open_reqs: Sequence[TransactionRequirement],
        *,
        now: datetime,
        sla_days: int,
    ) -> List[dict]:
        breaches = []
        for req in open_reqs:
            waiting_on = (req.responsibility_type or '').strip().lower()
            if waiting_on not in THIRD_PARTY_RESPONSIBILITY:
                # Also honor explicit "waiting" style work_status labels.
                if (req.work_status or '').lower() not in ('waiting', 'blocked'):
                    continue
                waiting_on = waiting_on or req.responsible_party_label or 'third_party'

            # Clock starts at last update (or created_at), not due_at —
            # SLA is "how long have we been waiting on them".
            started = req.updated_at or req.created_at
            if started is None:
                continue
            days_waiting = (now - started).days
            if days_waiting < sla_days:
                continue
            # If still within due date window and recently touched, skip.
            if req.due_at:
                due_day = req.due_at.date() if isinstance(req.due_at, datetime) else req.due_at
                if due_day >= now.date() and days_waiting < sla_days * 2:
                    # Still before deadline and within soft grace — only flag
                    # when clearly past SLA threshold above.
                    pass

            breaches.append({
                'requirement_id': req.id,
                'requirement_key': req.requirement_key,
                'title': req.title,
                'waiting_on': waiting_on,
                'days_waiting': days_waiting,
            })
        return breaches

    @staticmethod
    def _days_to_close(tx: Transaction, today: date) -> Optional[int]:
        if tx.expected_close_date:
            return (tx.expected_close_date - today).days
        return None

    @staticmethod
    def _owner_admin_user_ids(organization_id: int) -> List[int]:
        rows = (
            User.query
            .filter(
                User.organization_id == organization_id,
                User.org_role.in_(tuple(ESCALATION_ROLES)),
            )
            .all()
        )
        return [u.id for u in rows]

    @staticmethod
    def _emit_for_users(
        *,
        user_ids: Sequence[int],
        organization_id: int,
        event_type: str,
        payload: dict,
        priority: str,
        dedupe_key: str,
        dedupe_bucket: str,
        related_transaction_id: Optional[int] = None,
        related_requirement_id: Optional[int] = None,
        category: str = 'portfolio',
        escalation_level: int = 0,
    ) -> Tuple[int, int]:
        created = 0
        existing = 0
        for user_id in user_ids:
            before = PortfolioMonitor._existing_event_id(
                user_id, dedupe_key, dedupe_bucket,
            )
            event = NotificationOutboxService.create_event(
                user_id=user_id,
                organization_id=organization_id,
                event_type=event_type,
                payload=payload,
                priority=priority,
                dedupe_key=dedupe_key,
                dedupe_bucket=dedupe_bucket,
                related_transaction_id=related_transaction_id,
                related_requirement_id=related_requirement_id,
                category=category,
                escalation_level=escalation_level,
            )
            PortfolioMonitor._ensure_deliveries(event.id, organization_id)
            if before and event.id == before:
                existing += 1
            else:
                created += 1
        return created, existing

    @staticmethod
    def _ensure_deliveries(event_id: int, organization_id: int) -> None:
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


def scan_portfolio_for_org(
    organization_id: int,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Convenience wrapper for the background job."""
    return PortfolioMonitor.scan_organization(organization_id, now=now)


def weekly_report_for_org(
    organization_id: int,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    return PortfolioMonitor.generate_weekly_report(organization_id, now=now)
