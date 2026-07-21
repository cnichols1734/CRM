"""Per-user contact group helpers.

Groups are owned by a single user within an organization. Contact assignment
always uses the contact owner's catalog.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Sequence

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from models import Contact, ContactGroup, contact_groups, db
from services.cache_helpers import (
    clear_user_contact_groups_cache,
    get_user_contact_groups,
)


class ContactGroupError(Exception):
    """Raised for user-facing group validation/ownership failures."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_group_name(name: str | None) -> str:
    """Normalize a group name for aggregate matching (case-insensitive trim)."""
    return ' '.join((name or '').strip().split()).casefold()


def list_user_groups(
    org_id: int,
    user_id: int,
    *,
    active_only: bool = True,
    use_cache: bool = True,
) -> list[ContactGroup]:
    """Return groups owned by a user, optionally active-only."""
    if use_cache and active_only:
        return get_user_contact_groups(org_id, user_id, active_only=True)

    query = ContactGroup.query.filter_by(
        organization_id=org_id,
        user_id=user_id,
    )
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(ContactGroup.sort_order, ContactGroup.id).all()


def groups_for_contact_owner(
    contact: Contact,
    *,
    active_only: bool = True,
) -> list[ContactGroup]:
    """Groups available when assigning to a contact (owner's catalog)."""
    return list_user_groups(
        contact.organization_id,
        contact.user_id,
        active_only=active_only,
    )


def get_owned_group(
    org_id: int,
    user_id: int,
    group_id: int,
) -> ContactGroup:
    """Fetch a group owned by the user or raise 404-style error."""
    group = ContactGroup.query.filter_by(
        id=group_id,
        organization_id=org_id,
        user_id=user_id,
    ).first()
    if not group:
        raise ContactGroupError('Group not found.', status_code=404)
    return group


def resolve_groups_for_owner(
    org_id: int,
    owner_user_id: int,
    group_ids: Iterable[int] | None,
    *,
    active_only: bool = True,
) -> list[ContactGroup]:
    """Resolve submitted group IDs against an owner's catalog.

    Raises ContactGroupError if any ID is missing/unowned (or inactive when
    active_only is True).
    """
    if not group_ids:
        return []

    try:
        ids = sorted({int(gid) for gid in group_ids if gid is not None and str(gid) != ''})
    except (TypeError, ValueError) as exc:
        raise ContactGroupError('Invalid group selection.') from exc

    if not ids:
        return []

    query = ContactGroup.query.filter(
        ContactGroup.organization_id == org_id,
        ContactGroup.user_id == owner_user_id,
        ContactGroup.id.in_(ids),
    )
    if active_only:
        query = query.filter_by(is_active=True)

    groups = query.all()
    found = {g.id for g in groups}
    missing = [gid for gid in ids if gid not in found]
    if missing:
        raise ContactGroupError(
            'One or more selected groups are not available for this contact owner.'
        )
    # Preserve submitted order
    by_id = {g.id: g for g in groups}
    return [by_id[gid] for gid in ids]


def assign_groups_to_contact(
    contact: Contact,
    group_ids: Iterable[int] | None,
    *,
    preserve_inactive: bool = True,
) -> list[ContactGroup]:
    """Replace a contact's active group memberships with the given IDs.

    When preserve_inactive is True, existing memberships in inactive groups are
    kept even if not in the submitted list (they are hidden from pickers).
    """
    selected = resolve_groups_for_owner(
        contact.organization_id,
        contact.user_id,
        group_ids,
        active_only=True,
    )

    if preserve_inactive:
        inactive_kept = [
            g for g in contact.groups
            if g.user_id == contact.user_id and not g.is_active
        ]
        # Deduplicate by id
        by_id = {g.id: g for g in inactive_kept}
        for g in selected:
            by_id[g.id] = g
        contact.groups = list(by_id.values())
    else:
        contact.groups = selected

    return selected


def resolve_groups_by_name(
    org_id: int,
    owner_user_id: int,
    names: Sequence[str],
    *,
    active_only: bool = True,
) -> tuple[list[ContactGroup], list[str]]:
    """Resolve group names for an owner. Returns (matched, missing_names)."""
    cleaned = [n.strip() for n in names if n and n.strip()]
    if not cleaned:
        return [], []

    query = ContactGroup.query.filter(
        ContactGroup.organization_id == org_id,
        ContactGroup.user_id == owner_user_id,
        ContactGroup.name.in_(cleaned),
    )
    if active_only:
        query = query.filter_by(is_active=True)
    groups = query.all()
    found_names = {g.name for g in groups}
    missing = [n for n in cleaned if n not in found_names]
    return groups, missing


def resolve_group_by_fuzzy_name(
    org_id: int,
    owner_user_id: int,
    requested_name: str | None,
    *,
    active_only: bool = True,
) -> ContactGroup | None:
    """Resolve a body/alias group hint to an owner's group (alphanumeric match)."""
    key = ''.join(ch for ch in (requested_name or '').lower() if ch.isalnum())
    if not key:
        return None
    # Skip cache — Magic Inbox/import resolve immediately after group changes
    # and must not miss newly created labels.
    groups = list_user_groups(
        org_id, owner_user_id, active_only=active_only, use_cache=False
    )
    for group in groups:
        group_key = ''.join(ch for ch in group.name.lower() if ch.isalnum())
        if group_key == key:
            return group
    return None


def group_usage_counts(org_id: int, user_id: int) -> dict[int, int]:
    """Return {group_id: contact_count} for a user's groups."""
    rows = (
        db.session.query(
            contact_groups.c.group_id,
            func.count(contact_groups.c.contact_id),
        )
        .join(ContactGroup, ContactGroup.id == contact_groups.c.group_id)
        .filter(
            ContactGroup.organization_id == org_id,
            ContactGroup.user_id == user_id,
        )
        .group_by(contact_groups.c.group_id)
        .all()
    )
    return {group_id: count for group_id, count in rows}


def create_group(
    org_id: int,
    user_id: int,
    name: str,
    category: str,
    *,
    commit: bool = True,
) -> ContactGroup:
    name = (name or '').strip()
    category = (category or '').strip()
    if not name or not category:
        raise ContactGroupError('Name and category are required.')
    if len(name) > 100:
        raise ContactGroupError('Group name must be 100 characters or fewer.')

    highest = (
        db.session.query(func.max(ContactGroup.sort_order))
        .filter_by(organization_id=org_id, user_id=user_id, category=category)
        .scalar()
    ) or 0

    group = ContactGroup(
        organization_id=org_id,
        user_id=user_id,
        name=name,
        category=category,
        sort_order=highest + 1,
        is_active=True,
    )
    db.session.add(group)
    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ContactGroupError(
            'A group with that name already exists.'
        ) from exc

    clear_user_contact_groups_cache(org_id, user_id)
    return group


def update_group(
    org_id: int,
    user_id: int,
    group_id: int,
    *,
    name: str | None = None,
    category: str | None = None,
    sort_order: int | None = None,
    is_active: bool | None = None,
    commit: bool = True,
) -> ContactGroup:
    group = get_owned_group(org_id, user_id, group_id)

    if name is not None:
        name = name.strip()
        if not name:
            raise ContactGroupError('Name is required.')
        group.name = name
    if category is not None:
        category = category.strip()
        if not category:
            raise ContactGroupError('Category is required.')
        group.category = category
    if sort_order is not None:
        group.sort_order = int(sort_order)
    if is_active is not None:
        group.is_active = bool(is_active)

    try:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ContactGroupError(
            'A group with that name already exists.'
        ) from exc

    clear_user_contact_groups_cache(org_id, user_id)
    return group


def delete_group(
    org_id: int,
    user_id: int,
    group_id: int,
    *,
    commit: bool = True,
) -> None:
    group = get_owned_group(org_id, user_id, group_id)
    usage = group.contacts.count()
    if usage > 0:
        raise ContactGroupError(
            'This group is still on contacts. Deactivate it instead of deleting.',
            status_code=400,
        )
    db.session.delete(group)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    clear_user_contact_groups_cache(org_id, user_id)


def reorder_groups(
    org_id: int,
    user_id: int,
    items: Sequence[dict],
    *,
    commit: bool = True,
) -> None:
    """Reorder groups. Items: [{'id': int, 'sort_order': int}, ...]."""
    if not items:
        return

    owned = {
        g.id: g
        for g in ContactGroup.query.filter_by(
            organization_id=org_id,
            user_id=user_id,
        ).all()
    }

    for item in items:
        try:
            gid = int(item['id'])
            order = int(item['sort_order'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContactGroupError('Invalid reorder payload.') from exc
        group = owned.get(gid)
        if group:
            group.sort_order = order

    if commit:
        db.session.commit()
    else:
        db.session.flush()
    clear_user_contact_groups_cache(org_id, user_id)


def add_missing_defaults(
    org_id: int,
    user_id: int,
    *,
    commit: bool = True,
) -> list[ContactGroup]:
    """Insert canonical default groups the user is missing (by exact name)."""
    from services.tenant_service import DEFAULT_CONTACT_GROUPS

    existing_names = {
        g.name
        for g in ContactGroup.query.filter_by(
            organization_id=org_id,
            user_id=user_id,
        ).all()
    }

    created = []
    for group_data in DEFAULT_CONTACT_GROUPS:
        if group_data['name'] in existing_names:
            continue
        group = ContactGroup(
            organization_id=org_id,
            user_id=user_id,
            name=group_data['name'],
            category=group_data['category'],
            sort_order=group_data['sort_order'],
            is_active=True,
        )
        db.session.add(group)
        created.append(group)

    if created:
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        clear_user_contact_groups_cache(org_id, user_id)
    return created


def aggregate_filter_groups(
    org_id: int,
    *,
    owner_user_ids: Sequence[int] | None = None,
) -> list[dict]:
    """Build filter options for admin all-contacts view.

    Returns list of dicts:
      {name, label, group_ids, count}
    Aggregated by normalized name across the given owners (or all org users).
    Counts are distinct contacts.
    """
    query = (
        db.session.query(
            ContactGroup.name,
            ContactGroup.id,
            func.count(func.distinct(Contact.id)).label('contact_count'),
        )
        .outerjoin(contact_groups, ContactGroup.id == contact_groups.c.group_id)
        .outerjoin(Contact, Contact.id == contact_groups.c.contact_id)
        .filter(
            ContactGroup.organization_id == org_id,
            ContactGroup.is_active.is_(True),
        )
    )
    if owner_user_ids is not None:
        query = query.filter(ContactGroup.user_id.in_(list(owner_user_ids)))

    rows = query.group_by(ContactGroup.id, ContactGroup.name).all()

    buckets: dict[str, dict] = {}
    for name, group_id, count in rows:
        key = normalize_group_name(name)
        bucket = buckets.get(key)
        if not bucket:
            buckets[key] = {
                'name': name,
                'label': name,
                'group_ids': [group_id],
                'count': int(count or 0),
            }
        else:
            bucket['group_ids'].append(group_id)
            bucket['count'] += int(count or 0)
            # Prefer the lexicographically first display name for stability
            if name < bucket['label']:
                bucket['label'] = name
                bucket['name'] = name

    return sorted(buckets.values(), key=lambda b: b['label'].casefold())


def aggregate_group_stats(
    org_id: int,
    *,
    owner_user_id: int | None = None,
    show_all: bool = False,
) -> list[dict]:
    """Dashboard/report group stats with distinct contact counts.

    When show_all, aggregates by normalized name across the org.
    Otherwise scopes to a single owner's groups.
    """
    query = (
        db.session.query(
            ContactGroup.name,
            ContactGroup.id,
            func.count(func.distinct(contact_groups.c.contact_id)).label('count'),
        )
        .join(contact_groups, ContactGroup.id == contact_groups.c.group_id)
        .join(Contact, Contact.id == contact_groups.c.contact_id)
        .filter(
            ContactGroup.organization_id == org_id,
            ContactGroup.is_active.is_(True),
        )
    )

    if show_all:
        rows = query.group_by(ContactGroup.id, ContactGroup.name).all()
        buckets: dict[str, dict] = defaultdict(lambda: {'name': '', 'count': 0})
        for name, _gid, count in rows:
            key = normalize_group_name(name)
            if not buckets[key]['name'] or name < buckets[key]['name']:
                buckets[key]['name'] = name
            buckets[key]['count'] += int(count or 0)
        return [
            {'name': b['name'], 'count': b['count']}
            for b in buckets.values()
            if b['count'] > 0
        ]

    if owner_user_id is None:
        return []

    rows = (
        query.filter(ContactGroup.user_id == owner_user_id)
        .filter(Contact.user_id == owner_user_id)
        .group_by(ContactGroup.id, ContactGroup.name)
        .all()
    )
    return [
        {'name': name, 'count': int(count)}
        for name, _gid, count in rows
        if count > 0
    ]


def serialize_group(group: ContactGroup, *, usage: int | None = None) -> dict:
    data = {
        'id': group.id,
        'name': group.name,
        'category': group.category,
        'sort_order': group.sort_order,
        'is_active': bool(group.is_active),
        'user_id': group.user_id,
    }
    if usage is not None:
        data['usage_count'] = usage
    return data
