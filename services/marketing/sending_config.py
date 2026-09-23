"""Connected Gmail sender identity and monthly marketing send quotas."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func

from models import MarketingSend, UserEmailIntegration, db
from tier_config.tier_limits import get_tier_defaults

# Per-org quota override. Lives in the feature_flags JSON so raising a limit for
# one org is a platform-admin edit rather than a migration.
QUOTA_OVERRIDE_KEY = 'MARKETING_MONTHLY_SENDS'

# Statuses that consumed quota. A skipped recipient never reached a mailbox
# provider, so it does not count against the cap.
BILLABLE_STATUSES = ('queued', 'sending', 'sent', 'delivered', 'bounced',
                     'dropped', 'deferred', 'failed')


# ---------------------------------------------------------------------------
# Sender identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Sender:
    """The agent's authenticated Gmail mailbox and message identity."""
    from_email: str
    from_name: str
    reply_to: Optional[str]
    integration: UserEmailIntegration


class GmailConnectionError(ValueError):
    """The campaign owner must connect or reconnect Gmail."""


def gmail_for(user_id, organization_id) -> UserEmailIntegration:
    integration = UserEmailIntegration.query.filter_by(
        user_id=user_id,
        organization_id=organization_id,
        provider='gmail',
        sync_enabled=True,
    ).first() if user_id and organization_id else None
    if integration is None:
        raise GmailConnectionError(
            'The sending agent must connect their Google account in their profile '
            'before sending marketing emails.'
        )
    if (
        integration.needs_reauth
        or not integration.connected_email
        or not integration.access_token_encrypted
        or not integration.refresh_token_encrypted
    ):
        raise GmailConnectionError(
            'The sending agent must reconnect their Gmail account in their profile '
            'before sending marketing emails.'
        )
    return integration


def sender_for(agent, org, *, reply_to: Optional[str] = None,
               from_name: Optional[str] = None) -> Sender:
    integration = gmail_for(getattr(agent, 'id', None), getattr(org, 'id', None))
    return Sender(
        from_email=integration.connected_email,
        from_name=from_name or _agent_name(agent) or integration.connected_email,
        reply_to=reply_to or integration.connected_email,
        integration=integration,
    )


def _agent_name(agent) -> Optional[str]:
    if agent is None:
        return None
    full = getattr(agent, 'full_name', None)
    if full:
        return full
    parts = [getattr(agent, 'first_name', None), getattr(agent, 'last_name', None)]
    joined = ' '.join(p for p in parts if p).strip()
    return joined or None


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Quota:
    limit: int
    used: int
    period_start: datetime

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining <= 0

    def allows(self, count: int) -> bool:
        return count <= self.remaining

    def shortfall(self, count: int) -> int:
        return max(count - self.remaining, 0)

    def refusal(self, count: int) -> Optional[str]:
        """Why a launch of ``count`` recipients cannot proceed.

        Names the exact numbers: "over your limit" with no figures leaves the
        agent guessing how much to trim.
        """
        if self.allows(count):
            return None
        if self.limit <= 0:
            return 'Marketing email is not included in your plan.'
        return (
            f'This send needs {count:,} emails and you have '
            f'{self.remaining:,} left this month.'
        )


def monthly_limit(org) -> int:
    override = (org.feature_flags or {}).get(QUOTA_OVERRIDE_KEY)
    if isinstance(override, int) and not isinstance(override, bool):
        return max(override, 0)

    if getattr(org, 'is_platform_admin', False):
        return get_tier_defaults('enterprise')['monthly_marketing_sends']

    tier = org.subscription_tier or 'free'
    return get_tier_defaults(tier).get('monthly_marketing_sends', 0)


def period_start(now: Optional[datetime] = None) -> datetime:
    """First instant of the current calendar month, in UTC.

    A calendar month rather than a rolling window so the number an agent sees
    in the UI matches the one they would compute themselves.
    """
    now = now or datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def used_this_month(organization_id: int, now: Optional[datetime] = None) -> int:
    start = period_start(now)
    return db.session.query(func.count(MarketingSend.id)).filter(
        MarketingSend.organization_id == organization_id,
        MarketingSend.created_at >= start,
        MarketingSend.status.in_(BILLABLE_STATUSES),
    ).scalar() or 0


def quota_for(org, now: Optional[datetime] = None) -> Quota:
    return Quota(
        limit=monthly_limit(org),
        used=used_this_month(org.id, now),
        period_start=period_start(now),
    )
