"""Hand-rolled MCP rate limits. Process-local for DCR; grant-row for tool calls."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import date, datetime

from models import db

REGISTER_PER_HOUR = 20
CALLS_PER_MINUTE = 120
CALLS_PER_DAY = 2000
READ_CALLS_PER_DAY = 5000

_register_hits: dict[str, deque[float]] = defaultdict(deque)


def register_allowed(ip: str) -> bool:
    now = time.time()
    window = _register_hits[ip or 'unknown']
    cutoff = now - 3600
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= REGISTER_PER_HOUR:
        return False
    window.append(now)
    return True


def reset_register_limits() -> None:
    _register_hits.clear()


def consume_call(grant, *, is_read: bool) -> tuple[bool, str]:
    today = date.today()
    if grant.calls_on != today:
        grant.calls_on = today
        grant.calls_today = 0
        grant.read_calls_today = 0

    minute_key = f'{grant.id}:{int(time.time() // 60)}'
    if not _minute_allowed(minute_key):
        return False, 'Too many MCP calls this minute. Try again shortly.'

    if grant.calls_today >= CALLS_PER_DAY:
        return False, 'Daily MCP call limit reached.'
    if is_read and grant.read_calls_today >= READ_CALLS_PER_DAY:
        return False, 'Daily MCP read limit reached.'

    grant.calls_today += 1
    if is_read:
        grant.read_calls_today += 1
    grant.last_used_at = datetime.utcnow()
    db.session.commit()
    return True, ''


_minute_hits: dict[str, int] = {}


def _minute_allowed(key: str) -> bool:
    current = _minute_hits.get(key, 0)
    if current >= CALLS_PER_MINUTE:
        return False
    _minute_hits[key] = current + 1
    if len(_minute_hits) > 5000:
        stale_prefix = str(int(time.time() // 60) - 2)
        for stored in list(_minute_hits):
            if stored.rsplit(':', 1)[-1] <= stale_prefix:
                _minute_hits.pop(stored, None)
    return True
