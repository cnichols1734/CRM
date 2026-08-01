"""RQ job: run one B.O.B. Telegram turn (message or callback).

The webhook returns 200 immediately and enqueues here. Telegram retries any
non-2XX and does not publish a schedule, so slow LLM work must never sit inside
the request handler.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def process_telegram_message_job(
    *,
    org_id: int,
    channel_id: int,
    text: str,
    telegram_message_id: str | None = None,
    voice_file_id: str | None = None,
    voice_duration_seconds: int | None = None,
    photo_file_id: str | None = None,
) -> None:
    from app import app
    from jobs.base import set_job_org_context
    from services.messaging.conversation import handle_inbound_message

    with app.app_context():
        set_job_org_context(org_id)
        try:
            handle_inbound_message(
                channel_id=channel_id,
                org_id=org_id,
                text=text,
                telegram_message_id=telegram_message_id,
                voice_file_id=voice_file_id,
                voice_duration_seconds=voice_duration_seconds,
                photo_file_id=photo_file_id,
            )
        except Exception:
            logger.exception(
                'Telegram message job failed org=%s channel=%s',
                org_id, channel_id,
            )
            raise


def process_telegram_callback_job(*, org_id: int, channel_id: int,
                                  callback_query_id: str, data: str,
                                  message_id: str | None = None) -> None:
    from app import app
    from jobs.base import set_job_org_context
    from services.messaging.conversation import handle_callback_query

    with app.app_context():
        set_job_org_context(org_id)
        try:
            handle_callback_query(
                channel_id=channel_id,
                org_id=org_id,
                callback_query_id=callback_query_id,
                data=data,
                message_id=message_id,
            )
        except Exception:
            logger.exception(
                'Telegram callback job failed org=%s channel=%s',
                org_id, channel_id,
            )
            raise
