"""
Backfill Magic Inbox addresses for all existing users.

By default this script does a dry run that reports how many users still
need provisioning. Pass ``--commit`` to actually allocate addresses, and
``--announce`` to send the welcome/announcement email at the same time.

Usage:
    # Show how many users still need an address (no writes).
    python3 scripts/backfill_inbox_addresses.py

    # Provision missing addresses but skip the announcement email.
    python3 scripts/backfill_inbox_addresses.py --commit

    # Provision + send the announcement to every user that just got one.
    python3 scripts/backfill_inbox_addresses.py --commit --announce

    # Re-send the announcement to users who already had an address
    # (useful for a one-off relaunch email — pair with --commit).
    python3 scripts/backfill_inbox_addresses.py --commit --announce \
        --include-existing

Safety:
- The provisioning loop commits per user, so a single bad row does not
  poison the rest of the run.
- ``--announce`` is best-effort. SendGrid failures are logged and the
  script keeps going. Re-running with the same flags is safe.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app  # noqa: E402  -- needs sys.path patched first.
from models import User, db  # noqa: E402
from services.inbox_provisioning import provision_inbox_address  # noqa: E402
from services.sendgrid_outbound import send_inbox_welcome  # noqa: E402


logger = logging.getLogger('backfill_inbox')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
)


def _eligible_users(include_existing: bool):
    """Return users that should get an inbox address (and optionally email)."""
    q = User.query.filter(User.organization_id.isnot(None))
    if not include_existing:
        q = q.filter((User.inbox_address.is_(None))
                     | (User.inbox_token.is_(None)))
    return q.order_by(User.id.asc()).all()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', action='store_true',
                        help='Actually write to the database. '
                             'Default is dry-run.')
    parser.add_argument('--announce', action='store_true',
                        help='Send send_inbox_welcome() to each affected user.')
    parser.add_argument('--include-existing', action='store_true',
                        help='Also process users that already have an address. '
                             'Useful for re-sending the announcement.')
    parser.add_argument('--limit', type=int, default=0,
                        help='Stop after N users (0 = no limit).')
    args = parser.parse_args()

    with app.app_context():
        users = _eligible_users(args.include_existing)
        if args.limit:
            users = users[: args.limit]

        logger.info('Found %d users to process (commit=%s, announce=%s, '
                    'include_existing=%s).',
                    len(users), args.commit, args.announce,
                    args.include_existing)

        provisioned = 0
        skipped = 0
        emailed = 0
        failed_provision = 0
        failed_email = 0

        for u in users:
            had_address = bool(u.inbox_address)

            if not had_address:
                if not args.commit:
                    logger.info('  [dry-run] would provision user_id=%s '
                                'email=%s', u.id, u.email)
                    provisioned += 1
                    continue
                try:
                    provision_inbox_address(u, commit=False)
                    db.session.commit()
                    provisioned += 1
                    logger.info('  provisioned user_id=%s → %s',
                                u.id, u.inbox_address)
                except Exception:
                    db.session.rollback()
                    failed_provision += 1
                    logger.exception('  failed provisioning user_id=%s', u.id)
                    continue
            else:
                skipped += 1

            if args.announce and (args.commit or had_address):
                try:
                    send_inbox_welcome(u)
                    emailed += 1
                except Exception:
                    failed_email += 1
                    logger.exception('  failed announcement email user_id=%s',
                                     u.id)

        logger.info(
            'Done. provisioned=%d skipped(existing)=%d emailed=%d '
            'errors(provision)=%d errors(email)=%d',
            provisioned, skipped, emailed, failed_provision, failed_email,
        )

        if not args.commit:
            logger.warning('Dry run only. Re-run with --commit to apply.')

    return 0


if __name__ == '__main__':
    sys.exit(main())
