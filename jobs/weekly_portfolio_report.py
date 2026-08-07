"""
Weekly Portfolio Report Job - Phase 2 (E2-1)

Generates a weekly portfolio summary NotificationEvent for org owners/admins.

Usage:
    python jobs/weekly_portfolio_report.py
    python jobs/weekly_portfolio_report.py --org-id 1
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


def run_weekly_portfolio_report(org_id: Optional[int] = None) -> Dict[str, int]:
    """Emit weekly portfolio digests for one org or all active orgs."""
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

    logger.info('Starting weekly portfolio report for %s org(s)', len(org_ids))

    totals = {
        'orgs': 0,
        'created': 0,
        'existing': 0,
        'recipients': 0,
        'errors': 0,
    }

    db.session.remove()

    for current_org_id in org_ids:
        try:
            set_job_org_context(current_org_id)
            stats = PortfolioMonitor.generate_weekly_report(current_org_id)
            db.session.commit()
            # SET LOCAL is transaction-scoped; restore after commit for next org.
            set_job_org_context(current_org_id)

            totals['orgs'] += 1
            totals['created'] += stats.get('created', 0)
            totals['existing'] += stats.get('existing', 0)
            totals['recipients'] += stats.get('recipients', 0)

            logger.info(
                'Org %s: created=%s existing=%s recipients=%s open_tx=%s overdue=%s',
                current_org_id,
                stats.get('created', 0),
                stats.get('existing', 0),
                stats.get('recipients', 0),
                stats.get('open_transactions', 0),
                stats.get('overdue_requirements', 0),
            )
        except Exception:
            totals['errors'] += 1
            logger.exception(
                'Error generating weekly portfolio report for org %s',
                current_org_id,
            )
            db.session.rollback()
            set_job_org_context(current_org_id)
        finally:
            db.session.remove()

    logger.info(
        'Weekly portfolio report complete: orgs=%s created=%s existing=%s '
        'recipients=%s errors=%s',
        totals['orgs'],
        totals['created'],
        totals['existing'],
        totals['recipients'],
        totals['errors'],
    )
    return totals


def main():
    parser = argparse.ArgumentParser(description='Generate weekly portfolio digests')
    parser.add_argument(
        '--org-id',
        type=int,
        default=None,
        help='Limit report to a single organization id',
    )
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    with app.app_context():
        run_weekly_portfolio_report(org_id=args.org_id)


if __name__ == '__main__':
    main()
