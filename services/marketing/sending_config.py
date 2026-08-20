"""What an org needs in place before it can send marketing email.

Two independent gates:

    readiness   the org's own disclosure fields. Every marketing email has to
                carry the brokerage name, license number, and a physical
                mailing address, so a campaign cannot launch without them.
    quota       a monthly send cap. Not packaging — the sending domain is
                shared, so one org blasting a purchased list degrades inbox
                placement for every other tenant on it.

Both are read at launch and shown in the UI beforehand, because finding out
about either one at the moment you press send is a bad way to find out.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func

from config import Config
from models import MarketingSend, db
from services.marketing import compliance
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
    """The envelope. ``from_name`` carries the agent so the recipient sees a
    person, while the address stays on our authenticated subdomain: mail from a
    domain we do not sign fails DMARC and lands in spam.
    """
    from_email: str
    from_name: str
    reply_to: Optional[str]


def sender_for(agent, org, *, reply_to: Optional[str] = None) -> Sender:
    from_name = _display_name(agent, org)
    return Sender(
        from_email=Config.MARKETING_FROM_EMAIL,
        from_name=from_name,
        reply_to=reply_to or getattr(agent, 'email', None),
    )


def _display_name(agent, org) -> str:
    agent_name = _agent_name(agent)
    brokerage = getattr(org, 'broker_name', None) or getattr(org, 'name', None)
    if agent_name and brokerage:
        return f'{agent_name} | {brokerage}'
    return agent_name or brokerage or Config.MARKETING_FROM_NAME


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
# Readiness
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Readiness:
    ok: bool
    missing: list[str]

    @property
    def message(self) -> Optional[str]:
        if self.ok:
            return None
        if len(self.missing) == 1:
            return f'Add your {self.missing[0]} before sending marketing email.'
        listed = ', '.join(self.missing[:-1]) + f' and {self.missing[-1]}'
        return f'Add your {listed} before sending marketing email.'


def readiness_for(org) -> Readiness:
    """Whether the org can legally identify itself in a marketing email."""
    missing = compliance.missing_org_disclosure(org)
    return Readiness(ok=not missing, missing=missing)


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
