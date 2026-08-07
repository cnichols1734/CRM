"""
Transaction Reminder Scan Job - Phase 1C (E1C-7)

Scans open requirements and emits core date / closing-readiness
NotificationEvents via ReminderScheduler + NotificationOutboxService.

Never contacts clients or third parties — internal assignees only.

Usage:
    python jobs/transaction_reminders.py
    python jobs/transaction_reminders.py --org-id 1
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Dict, Optional

# Add repo root for imports when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def run_transaction_reminder_scan(org_id: Optional[int] = None) -> Dict[str, int]:
    """
    Run the reminder scan for one org or all active orgs.

    Uses set_job_org_context for RLS-aware queries per organization.
    """
    from jobs.base import set_job_org_context
    from models import Organization, db
    from services.reminder_scheduler import ReminderScheduler

    if org_id is not None:
        org_ids = [org_id]
    else:
        org_ids = [
            row.id
            for row in Organization.query.filter_by(status='active').all()
        ]

    logger.info('Starting transaction reminder scan for %s org(s)', len(org_ids))

    totals = {
        'orgs': 0,
        'created': 0,
        'existing': 0,
        'closing_alerts': 0,
        'requirements_scanned': 0,
        'errors': 0,
    }

    # Drop any ORM objects from the org query before per-org work.
    db.session.remove()

    for current_org_id in org_ids:
        try:
            set_job_org_context(current_org_id)
            stats = ReminderScheduler.scan_organization(current_org_id)
            db.session.commit()
            # SET LOCAL is transaction-scoped; restore after commit for next org.
            set_job_org_context(current_org_id)

            totals['orgs'] += 1
            totals['created'] += stats.get('created', 0)
            totals['existing'] += stats.get('existing', 0)
            totals['closing_alerts'] += stats.get('closing_alerts', 0)
            totals['requirements_scanned'] += stats.get('requirements_scanned', 0)

            logger.info(
                'Org %s: created=%s existing=%s closing_alerts=%s scanned=%s',
                current_org_id,
                stats.get('created', 0),
                stats.get('existing', 0),
                stats.get('closing_alerts', 0),
                stats.get('requirements_scanned', 0),
            )
        except Exception:
            totals['errors'] += 1
            logger.exception('Error scanning reminders for org %s', current_org_id)
            db.session.rollback()
            set_job_org_context(current_org_id)
        finally:
            db.session.remove()

    logger.info(
        'Transaction reminder scan complete: orgs=%s created=%s existing=%s '
        'closing_alerts=%s scanned=%s errors=%s',
        totals['orgs'],
        totals['created'],
        totals['existing'],
        totals['closing_alerts'],
        totals['requirements_scanned'],
        totals['errors'],
    )
    return totals


def main():
    parser = argparse.ArgumentParser(description='Scan transaction deadline reminders')
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
        run_transaction_reminder_scan(org_id=args.org_id)


if __name__ == '__main__':
    main()
