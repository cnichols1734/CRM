"""MCP auth lifecycle events on AuditEvent."""
from __future__ import annotations

import logging

from models import AuditEvent, db

logger = logging.getLogger(__name__)


def log_mcp_event(
    event_type: str,
    *,
    organization_id: int | None,
    actor_id: int | None,
    description: str,
    event_data: dict | None = None,
) -> None:
    try:
        db.session.add(AuditEvent(
            organization_id=organization_id,
            actor_id=actor_id,
            event_type=event_type,
            description=description[:500],
            event_data=event_data or {},
            source='mcp',
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('MCP audit event failed type=%s', event_type)
