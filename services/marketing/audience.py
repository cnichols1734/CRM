"""Resolve a contact filter into the people a campaign would actually reach.

The audience is a filter, not a list, until launch snapshots it. That is why a
saved "Buyers in 78130" stays current, and why a running drip does not quietly
pick up whoever joined the group on Tuesday.

The estimate is the trust surface. An agent who is told "412 contacts" and then
sees 380 emails go out will not use this twice. Every exclusion has a reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import func, or_

from models import Contact, ContactGroup, MarketingSuppression, contact_groups, db
from services.marketing import suppression as supp


class AudienceError(ValueError):
    """The filter is not usable. Message is shown to the agent."""


SKIP_NO_EMAIL = 'no_email'
SKIP_SUPPRESSED = 'suppressed'
SKIP_OPTED_OUT = 'opted_out'
SKIP_CONSENT = 'consent_required'
SKIP_DUPLICATE = 'duplicate_email'


@dataclass
class Filter:
    groups: list[int] = field(default_factory=list)
    zips: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    owners: list[int] = field(default_factory=list)
    contact_ids: list[int] = field(default_factory=list)
    require_consent: bool = False
    whole_org: bool = False

    def to_dict(self) -> dict:
        return {
            'groups': list(self.groups),
            'zips': list(self.zips),
            'cities': list(self.cities),
            'states': list(self.states),
            'owners': list(self.owners),
            'contact_ids': list(self.contact_ids),
            'require_consent': bool(self.require_consent),
            'whole_org': bool(self.whole_org),
        }

    def has_selection(self) -> bool:
        """True when the agent actually picked people or a filter.

        An empty filter is nobody, not every contact they own.
        """
        return bool(
            self.groups or self.zips or self.cities or self.states
            or self.owners or self.whole_org or self.contact_ids
        )

    def summarize(self) -> str:
        parts = []
        if self.whole_org and not self.owners:
            parts.append('everyone in the org')
        if self.contact_ids:
            n = len(self.contact_ids)
            parts.append(f'{n} picked contact{"s" if n != 1 else ""}')
        if self.groups:
            parts.append(f'{len(self.groups)} group{"s" if len(self.groups) != 1 else ""}')
        if self.zips:
            parts.append(', '.join(self.zips[:4]) + ('…' if len(self.zips) > 4 else ''))
        if self.cities:
            parts.append(', '.join(self.cities[:4]))
        if self.states:
            parts.append(', '.join(self.states))
        if self.require_consent:
            parts.append('opted-in only')
        return ', '.join(parts) or 'nobody yet'


def parse_filter(raw: Any) -> Filter:
    if raw is None:
        return Filter()
    if isinstance(raw, Filter):
        return raw
    if not isinstance(raw, dict):
        raise AudienceError('Audience filter must be an object.')

    def _ints(key):
        values = raw.get(key) or []
        if not isinstance(values, list):
            raise AudienceError(f'{key} must be a list.')
        out = []
        for item in values:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out

    def _strs(key):
        values = raw.get(key) or []
        if not isinstance(values, list):
            raise AudienceError(f'{key} must be a list.')
        return [str(v).strip() for v in values if str(v).strip()]

    return Filter(
        groups=_ints('groups'),
        zips=_strs('zips'),
        cities=_strs('cities'),
        states=_strs('states'),
        owners=_ints('owners'),
        contact_ids=_ints('contact_ids'),
        require_consent=bool(raw.get('require_consent')),
        whole_org=bool(raw.get('whole_org')),
    )


def can_use_org_scope(user) -> bool:
    return getattr(user, 'org_role', None) in ('owner', 'admin') or getattr(user, 'role', None) == 'admin'


def _owner_ids(filt: Filter, user) -> Optional[list[int]]:
    """Whose contacts this filter may see. None means the whole org."""
    if filt.owners:
        if not can_use_org_scope(user) and any(oid != user.id for oid in filt.owners):
            raise AudienceError('You can only send to your own contacts.')
        return filt.owners
    if filt.whole_org:
        if not can_use_org_scope(user):
            raise AudienceError('Only an owner or admin can send to the whole org.')
        return None
    return [user.id]


def _sort_contacts(contacts) -> list[Contact]:
    return sorted(
        contacts,
        key=lambda c: (
            (c.last_name or '').lower(),
            (c.first_name or '').lower(),
            c.id,
        ),
    )


def _picked_contacts(organization_id: int, filt: Filter, user) -> list[Contact]:
    if not filt.contact_ids:
        return []
    query = Contact.query.filter(
        Contact.organization_id == organization_id,
        Contact.id.in_(filt.contact_ids),
    )
    if not can_use_org_scope(user):
        query = query.filter(Contact.user_id == user.id)
    return query.all()


def _filtered_contacts(organization_id: int, filt: Filter, user) -> list[Contact]:
    query = Contact.query.filter(Contact.organization_id == organization_id)

    owner_ids = _owner_ids(filt, user)
    if owner_ids is not None:
        query = query.filter(Contact.user_id.in_(owner_ids))

    if filt.groups:
        query = query.join(contact_groups).filter(
            contact_groups.c.group_id.in_(filt.groups)
        )

    if filt.zips:
        zip_clauses = [
            func.lower(Contact.zip_code).like(f'{z.strip().lower()}%')
            for z in filt.zips
        ]
        query = query.filter(or_(*zip_clauses))

    if filt.cities:
        city_clauses = [
            func.lower(Contact.city) == c.strip().lower()
            for c in filt.cities
        ]
        query = query.filter(or_(*city_clauses))

    if filt.states:
        state_clauses = [
            func.lower(Contact.state) == s.strip().lower()
            for s in filt.states
        ]
        query = query.filter(or_(*state_clauses))

    return query.distinct().all()


def matching_contacts(organization_id: int, filt: Filter, user) -> list[Contact]:
    """Contacts that match the filter, before sendability checks."""
    if not filt.has_selection():
        return []

    picked = _picked_contacts(organization_id, filt, user)
    has_filters = bool(
        filt.groups or filt.zips or filt.cities or filt.states
        or filt.owners or filt.whole_org
    )
    if picked and not has_filters:
        return _sort_contacts(picked)

    filtered = _filtered_contacts(organization_id, filt, user)
    if picked:
        by_id = {contact.id: contact for contact in filtered}
        for contact in picked:
            by_id[contact.id] = contact
        return _sort_contacts(by_id.values())
    return _sort_contacts(filtered)


@dataclass
class Recipient:
    contact: Contact
    email: str


@dataclass
class Exclusion:
    contact: Contact
    reason: str
    email: Optional[str] = None


@dataclass
class Estimate:
    matched: int
    sendable: list[Recipient]
    excluded: list[Exclusion]
    filter: Filter

    @property
    def sendable_count(self) -> int:
        return len(self.sendable)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)

    def breakdown(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.excluded:
            counts[row.reason] = counts.get(row.reason, 0) + 1
        return counts

    def as_dict(self) -> dict:
        return {
            'matched': self.matched,
            'sendable': self.sendable_count,
            'excluded': self.excluded_count,
            'breakdown': self.breakdown(),
            'filter': self.filter.to_dict(),
            'summary': self.filter.summarize(),
        }


def classify(
    contacts: list[Contact],
    organization_id: int,
    filt: Filter,
) -> Estimate:
    """Split matches into sendable vs excluded, with a reason for each skip."""
    emails_seen: set[str] = set()
    sendable: list[Recipient] = []
    excluded: list[Exclusion] = []

    emails = [supp.normalize(c.email) for c in contacts if c.email]
    suppressed = supp.suppressed_reasons(emails, organization_id)

    for contact in contacts:
        email = supp.normalize(contact.email)
        if not email:
            excluded.append(Exclusion(contact, SKIP_NO_EMAIL))
            continue
        if contact.marketing_consent == 'opted_out':
            excluded.append(Exclusion(contact, SKIP_OPTED_OUT, email))
            continue
        if filt.require_consent and contact.marketing_consent != 'opted_in':
            excluded.append(Exclusion(contact, SKIP_CONSENT, email))
            continue
        if email in suppressed:
            excluded.append(Exclusion(contact, SKIP_SUPPRESSED, email))
            continue
        if email in emails_seen:
            excluded.append(Exclusion(contact, SKIP_DUPLICATE, email))
            continue
        emails_seen.add(email)
        sendable.append(Recipient(contact, email))

    return Estimate(
        matched=len(contacts),
        sendable=sendable,
        excluded=excluded,
        filter=filt,
    )


def estimate(organization_id: int, raw_filter, user) -> Estimate:
    filt = parse_filter(raw_filter)
    contacts = matching_contacts(organization_id, filt, user)
    return classify(contacts, organization_id, filt)


def group_choices(organization_id: int, user) -> list[ContactGroup]:
    query = ContactGroup.query.filter_by(
        organization_id=organization_id, is_active=True,
    )
    if not can_use_org_scope(user):
        query = query.filter_by(user_id=user.id)
    return query.order_by(ContactGroup.sort_order.asc(), ContactGroup.name.asc()).all()
