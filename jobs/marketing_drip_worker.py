"""Advance due drip enrollments, then the outbox worker sends them.

Usage:
    python jobs/marketing_drip_worker.py
    python jobs/marketing_drip_worker.py --org-id 1
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def run_marketing_drip_worker(
    org_id: Optional[int] = None,
    *,
    limit: int = 200,
) -> dict[str, int]:
    from jobs.base import set_job_org_context
    from models import Organization, db
    from services.marketing import drip as dripmod

    if org_id is not None:
        org_ids = [org_id]
    else:
        org_ids = [
            row.id for row in Organization.query.filter_by(status='active').all()
        ]

    totals = {'orgs': 0, 'advanced': 0, 'errors': 0}
    now = datetime.utcnow()
    db.session.remove()

    for current_org_id in org_ids:
        try:
            set_job_org_context(current_org_id)
            rows = dripmod.due_enrollments(current_org_id, now=now, limit=limit)
            for enrollment in rows:
                try:
                    if dripmod.advance_one(enrollment, now=now):
                        totals['advanced'] += 1
                    db.session.commit()
                    set_job_org_context(current_org_id)
                except Exception:
                    totals['errors'] += 1
                    logger.exception('Drip advance failed enrollment=%s', enrollment.id)
                    db.session.rollback()
                    set_job_org_context(current_org_id)
            totals['orgs'] += 1
        except Exception:
            totals['errors'] += 1
            logger.exception('Drip worker error for org %s', current_org_id)
            db.session.rollback()
        finally:
            db.session.remove()

    logger.info(
        'Marketing drip complete: orgs=%s advanced=%s errors=%s',
        totals['orgs'], totals['advanced'], totals['errors'],
    )
    return totals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--org-id', type=int)
    parser.add_argument('--limit', type=int, default=200)
    args = parser.parse_args()
    from app import create_app
    app = create_app()
    with app.app_context():
        run_marketing_drip_worker(org_id=args.org_id, limit=args.limit)


if __name__ == '__main__':
    main()
