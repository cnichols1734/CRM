"""Addresses we will not email, and the tokens that let a recipient say so.

Two scopes, because every org's marketing shares one sending domain:

    org         this org stops emailing the address. An unsubscribe is a
                decision about one agent's mail, not about the whole platform.
    platform    nobody emails the address. A spam complaint or a hard bounce is
                a reputation problem for every tenant on the domain, so one
                org's bad list cannot be allowed to cost everyone else.

Suppression is keyed on the address rather than the contact. The same person can
exist as separate contacts under separate agents, and an opt-out has to hold for
all of them.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from models import Contact, MarketingSend, MarketingSuppression, db

logger = logging.getLogger(__name__)

SCOPE_ORG = 'org'
SCOPE_PLATFORM = 'platform'

REASON_UNSUBSCRIBE = 'unsubscribe'
REASON_BOUNCE = 'bounce'
REASON_SPAM = 'spam_report'
REASON_MANUAL = 'manual'
REASON_INVALID = 'invalid'

# Reasons an agent may undo. A hard bounce or a spam complaint is not one of
# them: those addresses stay dead until the provider says otherwise.
RELEASABLE_REASONS = frozenset({REASON_UNSUBSCRIBE, REASON_MANUAL})

_TOKEN_BYTES = 24


class SuppressionError(Exception):
    """Bad input to a suppression call."""


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

def normalize(email: Optional[str]) -> str:
    """Casefolded and trimmed. Suppression lookups are exact string matches, so
    everything that writes or reads a suppression goes through here.

    Plus-addressing and dots are left alone on purpose: they are Gmail
    conventions, not standards, and quietly rewriting an address the recipient
    gave us would suppress mail they never asked to stop.
    """
    return (email or '').strip().casefold()


def scope_for(reason: str) -> str:
    """A complaint or bounce is everyone's problem; an opt-out is one org's."""
    if reason in MarketingSuppression.PLATFORM_REASONS:
        return SCOPE_PLATFORM
    return SCOPE_ORG


# ---------------------------------------------------------------------------
# Unsubscribe tokens
# ---------------------------------------------------------------------------
# The unsubscribe link is clicked by someone with no session, which means the
# route cannot know the tenant before it queries, and RLS is forced on
# marketing_sends. So the org id travels in the token and the route uses it to
# set org context before looking anything up. The secret half is what actually
# authorizes: a guessed org id gets you nothing.

def issue_token(organization_id: int) -> str:
    return f'{int(organization_id)}.{secrets.token_urlsafe(_TOKEN_BYTES)}'


def org_id_from_token(token: Optional[str]) -> Optional[int]:
    """The tenant hint from a token, or None if it is not shaped like one."""
    if not token or '.' not in token:
        return None
    head, _, tail = token.partition('.')
    if not tail:
        return None
    try:
        org_id = int(head)
    except (TypeError, ValueError):
        return None
    return org_id if org_id > 0 else None


def find_send_by_token(token: str) -> Optional[MarketingSend]:
    """Resolve a token to its send row.

    The caller must have set org context from :func:`org_id_from_token` first.
    Comparison is constant-time so a timing signal cannot be used to walk the
    token space.
    """
    org_id = org_id_from_token(token)
    if org_id is None:
        return None

    send = MarketingSend.query.filter_by(unsubscribe_token=token).first()
    if send is None:
        return None
    if not hmac.compare_digest(send.unsubscribe_token, token):
        return None
    if send.organization_id != org_id:
        # The token's tenant hint disagrees with the row. Should be impossible;
        # if it happens, something is wrong enough to refuse.
        logger.warning(
            'Unsubscribe token org mismatch: token=%s send=%s', org_id, send.id,
        )
        return None
    return send


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def is_suppressed(email: Optional[str], organization_id: int) -> bool:
    address = normalize(email)
    if not address:
        return False
    return db.session.query(
        MarketingSuppression.query.filter(
            MarketingSuppression.email == address,
            or_(
                MarketingSuppression.scope == SCOPE_PLATFORM,
                MarketingSuppression.organization_id == organization_id,
            ),
        ).exists()
    ).scalar()


def suppressed_reasons(
    emails: Iterable[str], organization_id: int,
) -> dict[str, str]:
    """Map of suppressed address -> reason, for a batch of addresses.

    Audience counts and campaign launches check thousands of addresses at once;
    doing that one query at a time is the difference between a page that loads
    and one that times out.
    """
    addresses = {normalize(e) for e in emails if e}
    addresses.discard('')
    if not addresses:
        return {}

    found: dict[str, str] = {}
    for chunk in _chunked(sorted(addresses), 900):
        rows = (
            MarketingSuppression.query
            .filter(
                MarketingSuppression.email.in_(chunk),
                or_(
                    MarketingSuppression.scope == SCOPE_PLATFORM,
                    MarketingSuppression.organization_id == organization_id,
                ),
            )
            .with_entities(
                MarketingSuppression.email,
                MarketingSuppression.scope,
                MarketingSuppression.reason,
            )
            .all()
        )
        for address, scope, reason in rows:
            # Platform rows win: they are the stronger statement, and an agent
            # reading "unsubscribed" for a spam complaint would try to fix it.
            if scope == SCOPE_PLATFORM or address not in found:
                found[address] = reason
    return found


def _chunked(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def suppress(
    email: Optional[str],
    reason: str,
    *,
    organization_id: Optional[int] = None,
    source_send_id: Optional[int] = None,
    created_by_id: Optional[int] = None,
    note: Optional[str] = None,
) -> Optional[MarketingSuppression]:
    """Record an address we will not email again. Idempotent.

    Returns the existing row when the address is already suppressed at this
    scope, so callers can treat a repeat unsubscribe click as success. Does not
    commit: the caller owns the transaction, since suppression is usually one
    step of a larger unit of work.
    """
    address = normalize(email)
    if not address:
        return None
    if reason not in MarketingSuppression.REASONS:
        raise SuppressionError(f'Unknown suppression reason: {reason}')

    scope = scope_for(reason)
    org_id = None if scope == SCOPE_PLATFORM else organization_id
    if scope == SCOPE_ORG and org_id is None:
        raise SuppressionError(f'{reason} suppression needs an organization')

    existing = _find(address, scope, org_id)
    if existing is not None:
        return existing

    row = MarketingSuppression(
        organization_id=org_id,
        email=address,
        scope=scope,
        reason=reason,
        source_send_id=source_send_id,
        created_by_id=created_by_id,
        note=note,
        created_at=datetime.utcnow(),
    )
    db.session.add(row)
    try:
        db.session.flush()
    except IntegrityError:
        # Two webhook deliveries for the same complaint, or a recipient
        # double-clicking. The unique constraint is the real guard; this just
        # turns the race into the idempotent answer.
        db.session.rollback()
        return _find(address, scope, org_id)
    return row


def _find(
    address: str, scope: str, organization_id: Optional[int],
) -> Optional[MarketingSuppression]:
    query = MarketingSuppression.query.filter(
        MarketingSuppression.email == address,
        MarketingSuppression.scope == scope,
    )
    if organization_id is None:
        query = query.filter(MarketingSuppression.organization_id.is_(None))
    else:
        query = query.filter(
            MarketingSuppression.organization_id == organization_id
        )
    return query.first()


def release(
    email: Optional[str], organization_id: int, *, actor_id: Optional[int] = None,
) -> bool:
    """Undo an org-scoped opt-out. Returns whether anything changed.

    Only an unsubscribe or a manual entry can be released, and only for the org
    that owns it. Platform rows are deliberately out of reach: letting an agent
    clear a spam complaint would let one tenant burn the shared domain.
    """
    address = normalize(email)
    if not address:
        return False

    row = _find(address, SCOPE_ORG, organization_id)
    if row is None or row.reason not in RELEASABLE_REASONS:
        return False

    db.session.delete(row)
    logger.info(
        'Marketing suppression released: org=%s actor=%s reason=%s',
        organization_id, actor_id, row.reason,
    )
    return True


def record_unsubscribe(
    send: MarketingSend, *, note: Optional[str] = None,
) -> MarketingSuppression:
    """Handle a recipient clicking the unsubscribe link on a specific email.

    Suppresses the address for the org and marks the contact opted out, so the
    agent sees the decision on the contact record instead of only in a
    suppression list they never open.
    """
    row = suppress(
        send.to_email,
        REASON_UNSUBSCRIBE,
        organization_id=send.organization_id,
        source_send_id=send.id,
        note=note,
    )
    _set_contact_consent(
        send.contact_id, 'opted_out', 'unsubscribe_link',
    )
    return row


def resubscribe(send: MarketingSend, *, actor_id: Optional[int] = None) -> bool:
    """Undo an unsubscribe the recipient just made by mistake.

    Consent goes back to 'unknown' rather than 'opted_in': all we know is that
    they took it back, not that they affirmatively agreed.
    """
    changed = release(
        send.to_email, send.organization_id, actor_id=actor_id,
    )
    if changed:
        _set_contact_consent(send.contact_id, 'unknown', 'unsubscribe_link')
    return changed


def _set_contact_consent(
    contact_id: Optional[int], state: str, source: str,
) -> None:
    if not contact_id:
        return
    contact = db.session.get(Contact, contact_id)
    if contact is None:
        return
    contact.marketing_consent = state
    contact.marketing_consent_source = source
    contact.marketing_consent_at = datetime.utcnow()


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

def unsubscribe_headers(
    unsubscribe_url: str, *, mailto: Optional[str] = None,
) -> dict[str, str]:
    """RFC 8058 one-click headers.

    Gmail and Outlook show their own unsubscribe control when these are
    present, which is the cheapest deliverability win available: a recipient
    who can opt out in one click does that instead of reporting spam, and a
    complaint costs far more than a lost subscriber.

    ``List-Unsubscribe-Post`` is a promise that the URL accepts an unattended
    POST and unsubscribes without asking anything further, so the route must
    honor POST without a confirmation step.
    """
    targets = [f'<{unsubscribe_url}>']
    if mailto:
        targets.append(f'<mailto:{mailto}>')
    return {
        'List-Unsubscribe': ', '.join(targets),
        'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
    }
