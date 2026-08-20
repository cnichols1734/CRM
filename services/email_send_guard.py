"""Internal outbound-email safety checks used by send helpers.

This is not a product feature. It only blocks live sends from pytest/Playwright
and known fixture recipient domains.
"""
from __future__ import annotations

import logging

from flask import current_app, has_app_context

logger = logging.getLogger(__name__)

# Addresses that exist only in tests / browser fixtures.
_FIXTURE_DOMAINS = frozenset({
    'test.com',
    'example.com',
    'localhost',
})


def is_fixture_recipient(to_email: str | None) -> bool:
    """True for @test.com, @example.com, @localhost, and their subdomains."""
    addr = (to_email or '').strip().lower()
    if '@' not in addr:
        return False
    domain = addr.rsplit('@', 1)[-1]
    if domain in _FIXTURE_DOMAINS:
        return True
    return any(domain.endswith('.' + suffix) for suffix in _FIXTURE_DOMAINS)


def app_is_testing() -> bool:
    """True when the current Flask app is in TESTING mode."""
    if not has_app_context():
        return False
    return bool(current_app.config.get('TESTING'))


def outbound_send_block_reason(to_email: str | None) -> str | None:
    """Return a skip reason, or None if a live send is allowed."""
    if app_is_testing():
        return 'TESTING'
    if is_fixture_recipient(to_email):
        return 'fixture_recipient'
    return None


def skip_outbound_send(to_email: str | None) -> bool:
    """Log and return True when helpers must not call a live mail API."""
    reason = outbound_send_block_reason(to_email)
    if not reason:
        return False
    logger.info('Outbound email skipped (%s) to=%s', reason, to_email)
    return True
