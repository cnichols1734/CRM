"""Proactive pushes from B.O.B. to a linked messaging channel.

Telegram has no send window and no template gate, so unprompted messages are
free and can carry the same Confirm / Done buttons as replies. Quiet hours and
a per-user daily proactive cap keep a bad cron from spamming anyone.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from models import AgentMessagingChannel, User, db
from services.messaging.base import ChoiceOption
from services.messaging.binding import get_active_channel
from services.messaging.telegram import get_transport
from services.notification_service import is_channel_enabled

logger = logging.getLogger(__name__)

PROVIDER = AgentMessagingChannel.PROVIDER_TELEGRAM
MAX_PROACTIVE_PER_DAY = 20
QUIET_HOURS_START = 21  # 9pm local
QUIET_HOURS_END = 7     # 7am local


def notify(
    user: User,
    category: str,
    body: str,
    *,
    options: Optional[Sequence[ChoiceOption]] = None,
    respect_quiet_hours: bool = True,
    force: bool = False,
) -> bool:
    """Send a proactive Telegram message if the agent is linked and opted in.

    Returns True when a message was actually sent.
    """
    if user is None or not getattr(user, 'id', None):
        return False

    if not force and not is_channel_enabled(user.id, category, 'telegram'):
        return False

    channel = get_active_channel(user.id, provider=PROVIDER)
    if channel is None:
        return False

    if respect_quiet_hours and _in_quiet_hours(user):
        logger.info(
            'Skipping Telegram notify user=%s category=%s (quiet hours)',
            user.id, category,
        )
        return False

    if not _bump_proactive_count(channel):
        logger.info(
            'Skipping Telegram notify user=%s category=%s (daily cap)',
            user.id, category,
        )
        return False

    transport = get_transport()
    try:
        if options:
            transport.send_choice(channel.chat_id, body, list(options))
        else:
            transport.send_text(channel.chat_id, body)
    except Exception:
        logger.exception(
            'Telegram notify failed user=%s category=%s', user.id, category,
        )
        return False
    return True


def notify_task_reminder(user: User, summary_parts: list[str],
                         *, action_url: Optional[str] = None) -> bool:
    """Format and send the task-reminder digest over Telegram."""
    if not summary_parts:
        return False
    lines = [
        'Task reminder',
        '',
        ', '.join(summary_parts) + '.',
    ]
    if action_url:
        lines.extend(['', f'Open tasks: {action_url}'])
    lines.extend(['', '--BOB'])
    return notify(user, 'task_reminder', '\n'.join(lines))


def _in_quiet_hours(user: User) -> bool:
    tz_name = getattr(user, 'timezone', None) or 'America/Chicago'
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo('America/Chicago')
    local_now = datetime.now(tz).time()
    start = time(QUIET_HOURS_START, 0)
    end = time(QUIET_HOURS_END, 0)
    # Window wraps midnight.
    if start <= end:
        return start <= local_now < end
    return local_now >= start or local_now < end


def _bump_proactive_count(channel: AgentMessagingChannel) -> bool:
    today = date.today()
    if channel.proactive_daily_count_date != today:
        channel.proactive_daily_count = 0
        channel.proactive_daily_count_date = today
    if channel.proactive_daily_count >= MAX_PROACTIVE_PER_DAY:
        return False
    channel.proactive_daily_count += 1
    db.session.commit()
    return True
