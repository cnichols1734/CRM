"""In-app notifications for the changes B.O.B. makes on an agent's behalf.

A chat turn scrolls away and a Telegram thread gets buried, so the bell is the
one place an agent will still find "what did B.O.B. change?" tomorrow.

Grouped per request rather than per tool call: one sentence can trigger three
writes, and three separate bells for one instruction reads as noise.
"""
from __future__ import annotations

import logging

from models import BobAction, db
from services.bob_tools.context import BobContext
from services.notification_service import create_notification

logger = logging.getLogger(__name__)

CATEGORY = 'bob_action'
ICON = 'fa-robot'

# How the agent would describe where they asked, not our internal surface key.
SURFACE_LABELS = {
    'bob_chat': 'chat',
    'bob_telegram': 'Telegram',
}

MAX_LISTED = 4


class ActionCollector:
    """Gathers the writes from one request so they notify together.

    Passed explicitly through ``dispatch`` rather than kept in module state, so
    two concurrent turns can never pour into each other's notification.
    """

    def __init__(self):
        self._entries: list[tuple[BobAction, str | None]] = []

    def add(self, action: BobAction, result) -> None:
        self._entries.append((action, getattr(result, 'record_url', None)))

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list:
        return list(self._entries)


def notify_actions(entries: list, ctx: BobContext):
    """Create one notification covering everything B.O.B. just changed.

    Never raises: a bell that fails to ring must not roll back real CRM work
    that already succeeded.
    """
    live = [
        (action, url) for action, url in entries
        if action is not None and action.status == BobAction.STATUS_EXECUTED
    ]
    if not live:
        return None

    try:
        notif = create_notification(
            user_id=ctx.user_id,
            organization_id=ctx.organization_id,
            category=CATEGORY,
            title=_title(live),
            body=_body(live, ctx),
            icon=ICON,
            action_url=_action_url(live),
        )
        if notif is not None:
            for action, _ in live:
                action.notification_id = notif.id
            db.session.commit()
        return notif
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. action notification failed user=%s', ctx.user_id)
        return None


def flush(collector: ActionCollector | None, ctx: BobContext):
    """Emit the notification for a finished turn, if anything was written."""
    if collector is None or not len(collector):
        return None
    return notify_actions(collector.entries, ctx)


def forget_action(action: BobAction) -> None:
    """Drop the notification for an undone action.

    Leaving "B.O.B. created Cooper Blake" in the bell after the agent undid it
    would be a lie, and its link would 404. A grouped notification only goes
    when every action it covered has been undone.
    """
    notification_id = getattr(action, 'notification_id', None)
    if not notification_id:
        return

    try:
        from models import Notification

        # Confirm the row is still the notification we announced. Postgres
        # nulls the reference on delete, but a stale pointer must never let an
        # undo remove some unrelated entry from the agent's bell.
        notif = db.session.get(Notification, notification_id)
        if (notif is None
                or notif.user_id != action.user_id
                or notif.category != CATEGORY):
            action.notification_id = None
            db.session.commit()
            return

        siblings = BobAction.query.filter_by(
            notification_id=notification_id,
            user_id=action.user_id,
        ).all()
        if any(s.status != BobAction.STATUS_UNDONE for s in siblings):
            return

        db.session.delete(notif)
        for sibling in siblings:
            sibling.notification_id = None
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('B.O.B. notification cleanup failed action=%s', action.id)


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def _title(live: list) -> str:
    if len(live) == 1:
        return f'B.O.B. {_lower_first(_summary(live[0][0]))}'[:200]
    return f'B.O.B. made {len(live)} changes'


def _body(live: list, ctx: BobContext) -> str:
    where = SURFACE_LABELS.get(ctx.surface, 'B.O.B.')
    if len(live) == 1:
        return f'Asked in {where}.'

    parts = [_summary(action) for action, _ in live[:MAX_LISTED]]
    remaining = len(live) - len(parts)
    if remaining > 0:
        parts.append(f'and {remaining} more')
    return f"{' · '.join(parts)} — asked in {where}."


def _action_url(live: list) -> str | None:
    for _, url in live:
        # Guard the type: a non-string here would only surface as a failed
        # INSERT at the very end of an otherwise successful turn.
        if url and isinstance(url, str):
            return url
    return None


def _summary(action: BobAction) -> str:
    return (action.summary or action.tool_name.replace('_', ' ')).strip()


def _lower_first(text: str) -> str:
    if not text:
        return text
    # Only the leading capital, so "Created Cooper Blake" reads as a sentence
    # while an acronym or a proper noun keeps its shape.
    return text[0].lower() + text[1:]
