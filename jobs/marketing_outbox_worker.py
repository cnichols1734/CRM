"""Send queued marketing emails.

Usage:
    python jobs/marketing_outbox_worker.py
    python jobs/marketing_outbox_worker.py --org-id 1
    python jobs/marketing_outbox_worker.py --limit 50
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def run_marketing_outbox_worker(
    org_id: Optional[int] = None,
    *,
    limit: int = 100,
) -> dict[str, int]:
    from jobs.base import set_job_org_context
    from models import MarketingCampaign, MarketingSend, Organization, db
    from services.marketing import launch as launchmod
    from services.marketing.send import SendError, deliver

    if org_id is not None:
        org_ids = [org_id]
    else:
        org_ids = [
            row.id for row in Organization.query.filter_by(status='active').all()
        ]

    totals = {
        'orgs': 0, 'processed': 0, 'sent': 0, 'failed': 0,
        'skipped': 0, 'errors': 0,
    }
    now = datetime.utcnow()
    db.session.remove()

    for current_org_id in org_ids:
        try:
            set_job_org_context(current_org_id)
            # A process can exit after Gmail accepts a message but before the
            # final commit. Surface that uncertainty instead of sending twice.
            stale = MarketingSend.query.filter(
                MarketingSend.organization_id == current_org_id,
                MarketingSend.status == 'sending',
                MarketingSend.last_attempt_at < now - timedelta(hours=1),
                MarketingSend.tracking.has(),
            ).with_for_update(skip_locked=True).limit(limit).all()
            for abandoned in stale:
                _mark_delivery_unknown(abandoned)
                launchmod.maybe_complete(abandoned.campaign)
                totals['failed'] += 1
            if stale:
                db.session.commit()
                set_job_org_context(current_org_id)
            send_ids = (
                db.session.query(MarketingSend.id)
                .join(MarketingCampaign, MarketingSend.campaign_id == MarketingCampaign.id)
                .filter(
                    MarketingSend.organization_id == current_org_id,
                    MarketingSend.status == 'queued',
                    MarketingSend.scheduled_for <= now,
                    MarketingCampaign.status.in_(('sending', 'active', 'scheduled')),
                )
                .order_by(MarketingSend.scheduled_for.asc(), MarketingSend.id.asc())
                .limit(limit)
                .all()
            )
            for (send_id,) in send_ids:
                try:
                    # Recheck each row under a lock; another worker may have sent it.
                    send = MarketingSend.query.filter_by(
                        id=send_id, organization_id=current_org_id, status='queued',
                    ).with_for_update(skip_locked=True).first()
                    if send is None:
                        db.session.rollback()
                        set_job_org_context(current_org_id)
                        continue
                    totals['processed'] += 1
                    if send.campaign and send.campaign.status == 'scheduled':
                        send.campaign.status = 'sending'
                    deliver(send, now=now, persist_tracking=True)
                    db.session.commit()
                    set_job_org_context(current_org_id)
                    send = db.session.get(MarketingSend, send.id)
                    if send and send.status == 'sent':
                        totals['sent'] += 1
                    elif send and send.status == 'failed':
                        totals['failed'] += 1
                    elif send and send.status == 'skipped':
                        totals['skipped'] += 1
                    campaign = send.campaign if send else None
                    if campaign:
                        launchmod.maybe_complete(campaign)
                        db.session.commit()
                        set_job_org_context(current_org_id)
                except SendError:
                    totals['errors'] += 1
                    db.session.rollback()
                    set_job_org_context(current_org_id)
                except Exception:
                    totals['errors'] += 1
                    logger.exception('Marketing send failed id=%s', send_id)
                    db.session.rollback()
                    set_job_org_context(current_org_id)
                    # Once the payload is committed, a provider exception can leave
                    # the delivery outcome unknown. Never retry that blindly.
                    pending = MarketingSend.query.filter_by(
                        id=send_id, organization_id=current_org_id, status='sending',
                    ).first()
                    if pending:
                        _mark_delivery_unknown(pending)
                        launchmod.maybe_complete(pending.campaign)
                        db.session.commit()
                        set_job_org_context(current_org_id)
            totals['orgs'] += 1
        except Exception:
            totals['errors'] += 1
            logger.exception('Marketing outbox error for org %s', current_org_id)
            db.session.rollback()
        finally:
            db.session.remove()

    logger.info(
        'Marketing outbox complete: orgs=%s processed=%s sent=%s failed=%s errors=%s',
        totals['orgs'], totals['processed'], totals['sent'],
        totals['failed'], totals['errors'],
    )
    return totals


def _mark_delivery_unknown(send):
    send.status = 'failed'
    send.error = 'Delivery outcome unknown. Check Gmail Sent before sending again.'
    if send.campaign:
        send.campaign.queued_count = max((send.campaign.queued_count or 1) - 1, 0)
        send.campaign.failed_count = (send.campaign.failed_count or 0) + 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--org-id', type=int)
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    from app import create_app
    app = create_app()
    with app.app_context():
        run_marketing_outbox_worker(org_id=args.org_id, limit=args.limit)


if __name__ == '__main__':
    main()
