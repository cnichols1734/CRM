"""
Backfill Magic Inbox addresses for all existing users.

By default this script does a dry run that reports how many users still
need provisioning. Pass ``--commit`` to actually allocate addresses, and
``--send-welcome`` to send the welcome/announcement email at the same time.

Usage:
    # Show how many users still need an address (no writes).
    python3 scripts/backfill_inbox_addresses.py

    # Provision missing addresses but skip the announcement email.
    python3 scripts/backfill_inbox_addresses.py --commit

    # Provision + send the announcement to every user that just got one.
    python3 scripts/backfill_inbox_addresses.py --commit --send-welcome \
        --base-url https://www.origentechnolog.com

    # Re-send the announcement to users who already had an address
    # (useful for a one-off relaunch email — pair with --commit).
    python3 scripts/backfill_inbox_addresses.py --commit --send-welcome \
        --include-existing --base-url https://www.origentechnolog.com

Safety:
- The provisioning loop commits per user, so a single bad row does not
  poison the rest of the run.
- No emails are sent during dry runs, even with ``--send-welcome``.
- ``--send-welcome`` is best-effort. SendGrid failures are logged and the
  script keeps going. Re-running with the same flags is safe.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from contextlib import nullcontext

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


def _normalize_base_url(value: str | None) -> str | None:
    value = (value or '').strip().rstrip('/')
    if not value:
        return None
    if not value.startswith(('http://', 'https://')):
        value = f'https://{value}'
    return value


def _default_base_url() -> str | None:
    # Prefer explicit app URL envs, then Railway's public domain if present.
    for name in ('APP_BASE_URL', 'PUBLIC_APP_URL', 'BASE_URL',
                 'RAILWAY_PUBLIC_DOMAIN'):
        base_url = _normalize_base_url(os.getenv(name))
        if base_url:
            return base_url
    return None


def _request_context(base_url: str | None):
    if not base_url:
        return nullcontext()
    return app.test_request_context('/', base_url=base_url)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', action='store_true',
                        help='Actually write to the database. '
                             'Default is dry-run.')
    parser.add_argument('--send-welcome', '--announce',
                        dest='send_welcome', action='store_true',
                        help='Send send_inbox_welcome() to each affected user. '
                             'Never sends during dry runs.')
    parser.add_argument('--include-existing', action='store_true',
                        help='Also process users that already have an address. '
                             'Useful for re-sending the announcement.')
    parser.add_argument('--limit', type=int, default=0,
                        help='Stop after N users (0 = no limit).')
    parser.add_argument('--base-url', default=_default_base_url(),
                        help='Public app URL used for email links, e.g. '
                             'https://www.origentechnolog.com. Required when '
                             'sending welcome emails unless APP_BASE_URL, '
                             'PUBLIC_APP_URL, BASE_URL, or '
                             'RAILWAY_PUBLIC_DOMAIN is set.')
    parser.add_argument('--sleep', type=float, default=0.0,
                        help='Seconds to pause between welcome emails.')
    args = parser.parse_args()
    args.base_url = _normalize_base_url(args.base_url)

    if args.send_welcome and args.commit and not args.base_url:
        parser.error('--send-welcome requires --base-url or an app URL env var')

    with app.app_context():
        users = _eligible_users(args.include_existing)
        if args.limit:
            users = users[: args.limit]

        logger.info(
            'Found %d users to process (commit=%s, send_welcome=%s, '
            'include_existing=%s, base_url=%s).',
            len(users), args.commit, args.send_welcome,
            args.include_existing, args.base_url or 'none',
        )

        provisioned = 0
        skipped = 0
        would_email = 0
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
                    if args.send_welcome and u.email:
                        logger.info('  [dry-run] would email user_id=%s '
                                    'email=%s', u.id, u.email)
                        would_email += 1
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
                if args.send_welcome and not args.commit and u.email:
                    logger.info('  [dry-run] would email existing user_id=%s '
                                'email=%s', u.id, u.email)
                    would_email += 1

            if args.send_welcome and args.commit:
                try:
                    with _request_context(args.base_url):
                        if send_inbox_welcome(u):
                            emailed += 1
                        else:
                            failed_email += 1
                            logger.warning(
                                '  announcement email returned false '
                                'user_id=%s email=%s', u.id, u.email)
                except Exception:
                    failed_email += 1
                    logger.exception('  failed announcement email user_id=%s',
                                     u.id)
                if args.sleep > 0:
                    time.sleep(args.sleep)

        logger.info(
            'Done. provisioned=%d skipped(existing)=%d would_email=%d emailed=%d '
            'errors(provision)=%d errors(email)=%d',
            provisioned, skipped, would_email, emailed,
            failed_provision, failed_email,
        )

        if not args.commit:
            logger.warning('Dry run only. No database writes or emails were sent.')

    return 0


if __name__ == '__main__':
    sys.exit(main())
