"""Shell context from live org and agent records."""
from __future__ import annotations

from typing import Optional

from services.marketing.links import unsubscribe_url
from services.marketing.shell import ShellContext


def agent_display_name(agent) -> Optional[str]:
    if agent is None:
        return None
    full = getattr(agent, 'full_name', None)
    if full:
        return full
    parts = [getattr(agent, 'first_name', None), getattr(agent, 'last_name', None)]
    joined = ' '.join(p for p in parts if p).strip()
    return joined or None


def shell_for(
    org,
    agent=None,
    *,
    unsubscribe_token: Optional[str] = None,
    preheader: Optional[str] = None,
    eyebrow: Optional[str] = None,
    reason_line: Optional[str] = None,
) -> ShellContext:
    """Build the wrapper the renderer needs for this org and sending agent."""
    header = (
        getattr(org, 'broker_name', None)
        or getattr(org, 'name', None)
        or 'AgentFlow'
    )
    agent_name = agent_display_name(agent)
    agent_title = None
    if getattr(agent, 'license_number', None):
        agent_title = 'REALTOR®'

    if reason_line is None:
        if agent_name:
            reason_line = (
                f'You are receiving this because you are a client or contact of '
                f'{agent_name}.'
            )
        else:
            reason_line = (
                'You are receiving this because you are a client or contact of ours.'
            )

    return ShellContext(
        header_title=header,
        eyebrow=eyebrow,
        agent_name=agent_name,
        agent_title=agent_title,
        agent_email=getattr(agent, 'email', None),
        agent_phone=getattr(agent, 'phone', None),
        brokerage_name=getattr(org, 'broker_name', None) or getattr(org, 'name', None),
        brokerage_license=getattr(org, 'broker_license_number', None),
        brokerage_address=getattr(org, 'broker_address', None),
        unsubscribe_url=unsubscribe_url(unsubscribe_token) if unsubscribe_token else None,
        reason_line=reason_line,
        preheader=preheader,
    )
