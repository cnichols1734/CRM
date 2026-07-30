"""Enqueue Telegram turns onto the RQ ``bob_telegram`` queue.

Falls back to an in-process background thread when Redis is unavailable
(local SQLite dev), matching the document-extraction pattern.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

QUEUE_NAME = 'bob_telegram'


def enqueue_telegram_message(*, org_id: int, channel_id: int, text: str,
                             telegram_message_id: str | None = None) -> None:
    _enqueue(
        'jobs.bob_telegram_reply.process_telegram_message_job',
        org_id=org_id,
        channel_id=channel_id,
        text=text,
        telegram_message_id=telegram_message_id,
    )


def enqueue_telegram_callback(*, org_id: int, channel_id: int,
                              callback_query_id: str, data: str,
                              message_id: str | None = None) -> None:
    _enqueue(
        'jobs.bob_telegram_reply.process_telegram_callback_job',
        org_id=org_id,
        channel_id=channel_id,
        callback_query_id=callback_query_id,
        data=data,
        message_id=message_id,
    )


def _enqueue(job_path: str, **kwargs: Any) -> None:
    try:
        from redis import Redis
        from rq import Queue
        from config import Config

        conn = Redis.from_url(
            Config.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        conn.ping()
        Queue(QUEUE_NAME, connection=conn).enqueue(
            job_path,
            **kwargs,
            job_timeout=300,
        )
        return
    except Exception as exc:
        logger.warning(
            'RQ unavailable for %s (%s); running in a background thread',
            job_path, exc,
        )

    def _run():
        try:
            if job_path.endswith('process_telegram_message_job'):
                from jobs.bob_telegram_reply import process_telegram_message_job
                process_telegram_message_job(**kwargs)
            else:
                from jobs.bob_telegram_reply import process_telegram_callback_job
                process_telegram_callback_job(**kwargs)
        except Exception:
            logger.exception('Inline Telegram job failed: %s', job_path)

    threading.Thread(target=_run, daemon=True).start()
