"""Phase 2 portfolio monitor: stale, SLA, risk score, weekly digest."""

from datetime import datetime, timedelta

from models import (
    NotificationEvent,
    Transaction,
    TransactionAssignment,
    TransactionRequirement,
    User,
    db,
)
from services.portfolio_monitor import PortfolioMonitor


def _ensure_lead(org_id, tx_id, user_id):
    row = TransactionAssignment.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
        user_id=user_id,
    ).first()
    if row:
        row.role = 'lead_agent'
        return row
    row = TransactionAssignment(
        organization_id=org_id,
        transaction_id=tx_id,
        user_id=user_id,
        role='lead_agent',
    )
    db.session.add(row)
    db.session.flush()
    return row


def _req(org_id, tx_id, **kwargs):
    key = kwargs.pop('requirement_key', 'earnest_money')
    title = kwargs.pop('title', 'Earnest money')
    existing = TransactionRequirement.query.filter_by(
        transaction_id=tx_id, requirement_key=key,
    ).first()
    if existing:
        for attr, value in kwargs.items():
            setattr(existing, attr, value)
        existing.title = title
        db.session.flush()
        return existing
    defaults = dict(
        organization_id=org_id,
        transaction_id=tx_id,
        package_key='seller_ctc',
        phase_key='option_period',
        requirement_key=key,
        title=title,
        work_status='pending',
    )
    defaults.update(kwargs)
    req = TransactionRequirement(**defaults)
    db.session.add(req)
    db.session.flush()
    return req


def test_score_transaction_risk_bands():
    healthy = PortfolioMonitor.score_transaction_risk()
    assert healthy['band'] == 'healthy'
    assert healthy['score'] == 0

    elevated = PortfolioMonitor.score_transaction_risk(
        overdue_count=3, is_stale=True, sla_breach_count=1,
    )
    assert elevated['band'] in ('elevated', 'critical')
    assert elevated['score'] >= 40

    critical = PortfolioMonitor.score_transaction_risk(
        overdue_count=3,
        high_risk_count=2,
        sla_breach_count=2,
        is_stale=True,
        days_to_close=2,
    )
    assert critical['band'] == 'critical'
    assert critical['score'] >= 70


def test_scan_emits_stale_and_sla_with_dedupe(app, seed):
    created_req_ids = []
    prior_status = None
    prior_updated_at = None
    with app.app_context():
        org_id = seed['org_a']
        tx = Transaction.query.get(seed['tx_a'])
        prior_status = tx.status
        prior_updated_at = tx.updated_at
        try:
            tx.status = 'under_contract'
            tx.updated_at = datetime.utcnow() - timedelta(days=14)
            _ensure_lead(org_id, tx.id, seed['owner_a'])

            overdue_due = datetime.utcnow() - timedelta(days=10)
            earnest = _req(
                org_id, tx.id,
                requirement_key='earnest_money',
                due_at=overdue_due,
                risk_level='high',
            )
            waiting = _req(
                org_id, tx.id,
                requirement_key='title_commitment',
                title='Title commitment',
                responsibility_type='title',
                phase_key='closing',
            )
            waiting.updated_at = datetime.utcnow() - timedelta(days=5)
            waiting.created_at = waiting.updated_at
            created_req_ids = [earnest.id, waiting.id]
            db.session.commit()

            now = datetime(2026, 8, 4, 15, 0, 0)
            stats1 = PortfolioMonitor.scan_organization(org_id, now=now, sla_days=3)
            db.session.commit()
            assert stats1['transactions_scanned'] >= 1
            assert stats1['created'] >= 1

            events = NotificationEvent.query.filter_by(
                organization_id=org_id,
                category='portfolio',
            ).all()
            assert events
            types = {e.event_type for e in events}
            assert 'portfolio_stale_transaction' in types or 'portfolio_sla_breach' in types

            stats2 = PortfolioMonitor.scan_organization(org_id, now=now, sla_days=3)
            db.session.commit()
            assert stats2['created'] == 0
            assert stats2['existing'] >= stats1['created']
        finally:
            if created_req_ids:
                NotificationEvent.query.filter(
                    NotificationEvent.related_requirement_id.in_(created_req_ids)
                ).delete(synchronize_session=False)
                TransactionRequirement.query.filter(
                    TransactionRequirement.id.in_(created_req_ids)
                ).delete(synchronize_session=False)
            tx = Transaction.query.get(seed['tx_a'])
            if tx is not None:
                tx.status = prior_status
                tx.updated_at = prior_updated_at
            db.session.commit()


def test_weekly_report_once_per_iso_week(app, seed):
    with app.app_context():
        org_id = seed['org_a']
        owner = User.query.get(seed['owner_a'])
        owner.org_role = 'owner'
        db.session.commit()

        now = datetime(2026, 8, 4, 12, 0, 0)
        first = PortfolioMonitor.generate_weekly_report(org_id, now=now)
        db.session.commit()
        assert first['created'] >= 1

        second = PortfolioMonitor.generate_weekly_report(org_id, now=now)
        db.session.commit()
        assert second['created'] == 0
        assert second['existing'] >= 1

        week = now.date().isocalendar()
        bucket = f'{week.year}-W{week.week:02d}'
        rows = NotificationEvent.query.filter_by(
            organization_id=org_id,
            event_type='weekly_portfolio_report',
            dedupe_bucket=bucket,
        ).all()
        # One event per owner/admin; second run must not add more.
        assert len(rows) == first['recipients']
        assert len(rows) == second['existing']
