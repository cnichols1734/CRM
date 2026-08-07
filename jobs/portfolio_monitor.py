"""
Portfolio Monitor Job - Phase 2 (E2-1)

Scans open transactions for stale files, third-party SLA breaches,
portfolio risk, and brokerage compliance escalations.

Usage:
    python jobs/portfolio_monitor.py
    python jobs/portfolio_monitor.py --org-id 1
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


def run_portfolio_monitor_scan(org_id: Optional[int] = None) -> Dict[str, int]:
    """Run portfolio monitor for one org or all active orgs."""
    from jobs.base import set_job_org_context
    from models import Organization, db
    from services.portfolio_monitor import PortfolioMonitor

    if org_id is not None:
        org_ids = [org_id]
    else:
        org_ids = [
            row.id
            for row in Organization.query.filter_by(status='active').all()
        ]

    logger.info('Starting portfolio monitor for %s org(s)', len(org_ids))

    totals = {
        'orgs': 0,
        'transactions_scanned': 0,
        'stale': 0,
        'sla_breaches': 0,
        'risk_alerts': 0,
        'compliance_escalations': 0,
        'created': 0,
        'existing': 0,
        'errors': 0,
    }

    db.session.remove()

    for current_org_id in org_ids:
        try:
            set_job_org_context(current_org_id)
            stats = PortfolioMonitor.scan_organization(current_org_id)
            db.session.commit()
            # SET LOCAL is transaction-scoped; restore after commit for next org.
            set_job_org_context(current_org_id)

            totals['orgs'] += 1
            for key in (
                'transactions_scanned', 'stale', 'sla_breaches',
                'risk_alerts', 'compliance_escalations', 'created', 'existing',
            ):
                totals[key] += stats.get(key, 0)

            logger.info(
                'Org %s: scanned=%s stale=%s sla=%s risk=%s compliance=%s '
                'created=%s existing=%s',
                current_org_id,
                stats.get('transactions_scanned', 0),
                stats.get('stale', 0),
                stats.get('sla_breaches', 0),
                stats.get('risk_alerts', 0),
                stats.get('compliance_escalations', 0),
                stats.get('created', 0),
                stats.get('existing', 0),
            )
        except Exception:
            totals['errors'] += 1
            logger.exception('Error scanning portfolio for org %s', current_org_id)
            db.session.rollback()
            set_job_org_context(current_org_id)
        finally:
            db.session.remove()

    logger.info(
        'Portfolio monitor complete: orgs=%s scanned=%s stale=%s sla=%s '
        'risk=%s compliance=%s created=%s existing=%s errors=%s',
        totals['orgs'],
        totals['transactions_scanned'],
        totals['stale'],
        totals['sla_breaches'],
        totals['risk_alerts'],
        totals['compliance_escalations'],
        totals['created'],
        totals['existing'],
        totals['errors'],
    )
    return totals


def main():
    parser = argparse.ArgumentParser(description='Scan portfolio health / SLA / risk')
    parser.add_argument(
        '--org-id',
        type=int,
        default=None,
        help='Limit scan to a single organization id',
    )
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    with app.app_context():
        run_portfolio_monitor_scan(org_id=args.org_id)


if __name__ == '__main__':
    main()
