"""Partner Directory helpers for duplicate detection and transaction snapshots."""
from difflib import SequenceMatcher

from models import (
    PartnerContact,
    PartnerOrganization,
    TransactionParticipant,
    db,
    normalize_partner_address,
    normalize_partner_phone,
    normalize_partner_text,
)


PARTNER_TYPES = {
    'brokerage': 'Brokerage',
    'title_company': 'Title Company',
    'lender': 'Lender',
    'attorney': 'Attorney',
    'inspector': 'Inspector',
    'other': 'Other Partner',
}


ROLE_PARTNER_TYPE_MAP = {
    'buyers_agent': 'brokerage',
    'listing_agent': 'brokerage',
    'title_company': 'title_company',
    'lender': 'lender',
    'transaction_coordinator': 'other',
}


EXTERNAL_PARTICIPANT_ROLES = {
    'buyers_agent',
    'title_company',
    'lender',
    'transaction_coordinator',
}


def coerce_partner_type(value):
    return value if value in PARTNER_TYPES else 'other'


def partner_type_for_role(role):
    return ROLE_PARTNER_TYPE_MAP.get(role or '', 'other')


def is_admin_role(user):
    return getattr(user, 'org_role', None) in ('owner', 'admin')


def sync_partner_organization_fields(partner):
    partner.partner_type = coerce_partner_type(partner.partner_type)
    partner.sync_normalized_fields()


def sync_partner_contact_fields(contact):
    contact.sync_normalized_fields()


def find_company_duplicate_candidates(
    organization_id,
    name,
    street_address=None,
    city=None,
    state=None,
    zip_code=None,
    exclude_id=None,
):
    """Return exact and warning duplicate candidates for a company create/edit."""
    normalized_name = normalize_partner_text(name)
    normalized_address = normalize_partner_address(street_address, city, state, zip_code)
    query = PartnerOrganization.query.filter_by(organization_id=organization_id)
    if exclude_id:
        query = query.filter(PartnerOrganization.id != exclude_id)

    exact = []
    warnings = []

    if normalized_name:
        exact = query.filter(PartnerOrganization.normalized_name == normalized_name).all()

    candidates = query.limit(200).all()
    for partner in candidates:
        if partner in exact:
            continue

        same_zip = bool(zip_code and partner.zip_code and partner.zip_code.strip() == zip_code.strip())
        address_match = bool(normalized_address and partner.normalized_address == normalized_address)
        name_similarity = SequenceMatcher(None, normalized_name or '', partner.normalized_name or '').ratio()

        if (same_zip and name_similarity >= 0.86) or (address_match and name_similarity >= 0.72):
            warnings.append(partner)

    return {'exact': exact, 'warnings': warnings}


def find_contact_duplicate_candidates(
    partner_organization_id,
    first_name,
    last_name,
    email=None,
    phone=None,
    exclude_id=None,
):
    """Return exact and warning duplicate candidates for a child partner contact."""
    normalized_full_name = normalize_partner_text(f'{first_name or ""} {last_name or ""}')
    normalized_email = email.strip().lower() if email else None
    normalized_phone = normalize_partner_phone(phone)

    query = PartnerContact.query.filter_by(partner_organization_id=partner_organization_id)
    if exclude_id:
        query = query.filter(PartnerContact.id != exclude_id)

    exact = []
    warnings = []

    if normalized_full_name:
        exact = query.filter(PartnerContact.normalized_full_name == normalized_full_name).all()

    warning_filters = []
    if normalized_email:
        warning_filters.append(PartnerContact.normalized_email == normalized_email)
    if normalized_phone:
        warning_filters.append(PartnerContact.normalized_phone == normalized_phone)

    if warning_filters:
        warnings = query.filter(db.or_(*warning_filters)).all()
        warnings = [candidate for candidate in warnings if candidate not in exact]

    return {'exact': exact, 'warnings': warnings}


def build_partner_participant(transaction, role, partner_organization, partner_contact=None):
    """Create a TransactionParticipant snapshot from Partner Directory records."""
    if partner_contact:
        name = partner_contact.full_name
        email = partner_contact.email
        phone = partner_contact.phone
    else:
        name = partner_organization.name
        email = partner_organization.email
        phone = partner_organization.phone

    return TransactionParticipant(
        organization_id=transaction.organization_id,
        transaction_id=transaction.id,
        role=role,
        partner_organization_id=partner_organization.id,
        partner_contact_id=partner_contact.id if partner_contact else None,
        name=name,
        email=email,
        phone=phone,
        company=partner_organization.name,
        is_primary=False,
    )


def partner_search_payload(partner, contact=None):
    label = partner.name
    name = partner.name
    email = partner.email
    phone = partner.phone

    if contact:
        name = contact.full_name
        label = f'{partner.name} - {contact.full_name}'
        email = contact.email or partner.email
        phone = contact.phone or partner.phone

    return {
        'partner_organization_id': partner.id,
        'partner_contact_id': contact.id if contact else None,
        'company': partner.name,
        'name': name,
        'label': label,
        'type': partner.partner_type,
        'type_label': partner.type_label,
        'email': email,
        'phone': phone,
        'address': partner.full_address,
    }
