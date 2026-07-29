"""Identity, locale, and result types for B.O.B. tool calls.

Handlers receive a ``BobContext`` and never read ``current_user``, ``request``,
or ``session``. That constraint is what lets the same tool layer serve the
in-app chat today and an SMS webhook later, where no request context exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pytz

DEFAULT_TIMEZONE = 'America/Chicago'

# Risk tiers drive the confirmation policy in registry.dispatch().
RISK_READ = 'read'
RISK_LOW_WRITE = 'low_write'
RISK_HIGH_WRITE = 'high_write'

ADMIN_ORG_ROLES = ('owner', 'admin')


@dataclass(frozen=True)
class BobContext:
    """Who B.O.B. is acting as, and where the request came from."""

    user_id: int
    organization_id: int
    timezone: str = DEFAULT_TIMEZONE
    surface: str = 'bob_chat'
    org_role: str = 'agent'
    is_org_admin: bool = False

    @classmethod
    def from_user(cls, user, *, surface: str = 'bob_chat',
                  timezone: str = DEFAULT_TIMEZONE) -> 'BobContext':
        """Build a context from an authenticated user.

        Raises rather than defaulting, because a tool layer that silently falls
        back to a missing org is a tenant-isolation bug waiting to happen.
        """
        if user is None or not getattr(user, 'id', None):
            raise ValueError('BobContext requires a persisted user')
        org_id = getattr(user, 'organization_id', None)
        if not org_id:
            raise ValueError('BobContext requires a user with an organization')

        org_role = getattr(user, 'org_role', None) or 'agent'
        return cls(
            user_id=user.id,
            organization_id=org_id,
            timezone=timezone,
            surface=surface,
            org_role=org_role,
            is_org_admin=org_role in ADMIN_ORG_ROLES,
        )

    @property
    def tzinfo(self):
        try:
            return pytz.timezone(self.timezone)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(DEFAULT_TIMEZONE)

    def now_local(self) -> datetime:
        return datetime.now(self.tzinfo)

    def today(self) -> date:
        return self.now_local().date()

    def load_user(self):
        """Fetch the acting user, scoped to the context's org.

        Activation events need a real User instance. Re-checking the org here
        means a stale context can never write against another tenant.
        """
        from models import User

        return User.query.filter_by(
            id=self.user_id,
            organization_id=self.organization_id,
        ).first()


@dataclass
class ToolResult:
    """Outcome of one tool call, shaped for both the model and the UI."""

    ok: bool
    summary: str
    data: dict = field(default_factory=dict)
    error: str | None = None
    requires_confirmation: bool = False
    action_id: int | None = None
    undoable: bool = False
    record_url: str | None = None

    @classmethod
    def failure(cls, error: str, **kwargs) -> 'ToolResult':
        return cls(ok=False, summary=error, error=error, **kwargs)

    @classmethod
    def success(cls, summary: str, data: dict | None = None, **kwargs) -> 'ToolResult':
        return cls(ok=True, summary=summary, data=data or {}, **kwargs)

    @classmethod
    def pending(cls, summary: str, *, action_id: int, preview: dict) -> 'ToolResult':
        return cls(
            ok=True,
            summary=summary,
            data={'status': 'awaiting_confirmation', 'preview': preview},
            requires_confirmation=True,
            action_id=action_id,
        )

    def for_model(self) -> dict[str, Any]:
        """The JSON the model sees as the tool's return value.

        Deliberately narrow: no internal IDs beyond record IDs the model needs,
        and an explicit status so it cannot mistake a pending action for a
        completed one.
        """
        if not self.ok:
            return {'status': 'error', 'error': self.error or self.summary}
        if self.requires_confirmation:
            return {
                'status': 'awaiting_confirmation',
                'message': (
                    'This change was NOT applied. The agent must approve it '
                    'first. Tell them what is waiting for approval.'
                ),
                'preview': self.data.get('preview', {}),
            }
        payload = {'status': 'ok'}
        payload.update(self.data)
        return payload

    def for_client(self) -> dict[str, Any]:
        """The event payload streamed to the browser for chips and cards."""
        return {
            'ok': self.ok,
            'summary': self.summary,
            'error': self.error,
            'requires_confirmation': self.requires_confirmation,
            'action_id': self.action_id,
            'undoable': self.undoable,
            'record_url': self.record_url,
            'preview': self.data.get('preview') if self.requires_confirmation else None,
        }
