"""Contract-to-Transaction Bootstrap Service (E1B-0)

Handles uploaded contract documents before they're attached to transactions:
- Record file metadata (sha256, page_count, etc.)
- Classify and extract field data
- Find matching transactions within the same organization (NEVER cross-org)
- Resolve match (attach/select/create_new/manual)
- Build review payload for agent approval
- Apply approved changes (create transaction + participants + requirements + deadlines)

FAIL CLOSED: No silent AI attach, no cross-org matches, no legal claims.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from models import (
    AuditEvent,
    Contact,
    ContactGroup,
    ContractBootstrapSession,
    Transaction,
    TransactionAssignment,
    TransactionChangeProposal,
    TransactionDocument,
    TransactionParticipant,
    TransactionRequirement,
    TransactionType,
    User,
    db,
)
from services.deadline_rules import DeadlineRulesService
from services.proposal_service import ProposalService
from services.requirements_service import RequirementsService

logger = logging.getLogger(__name__)

# Bootstrap extraction fields — observations only; never auto-applied.
BOOTSTRAP_EXTRACTION_FIELDS = {
    'property_address': 'Full property street address from Paragraph 2 or the header (street, city, state, ZIP if present).',
    'street_address': 'Street number and street name only, if separable from city/state/ZIP.',
    'city': 'City if written.',
    'state': 'State if written (2-letter preferred).',
    'zip_code': 'ZIP code if written.',
    'buyer_name': (
        'Buyer name(s) as written in Paragraph 1 or signature blocks. '
        'When multiple people, list each full name separated by " and " '
        '(e.g. "Clark Smith and Rachel Smith"). Do not combine into one blob.'
    ),
    'seller_name': (
        'Seller name(s) as written in Paragraph 1 or signature blocks. '
        'When multiple people, list each full name separated by " and " '
        '(e.g. "Billy Copaus and Kimberly Copaus"). Do not combine into one blob.'
    ),
    'seller_email': (
        'Seller email address(es) if printed on the document. '
        'When more than one, list in the same order as seller_name, separated by commas.'
    ),
    'seller_phone': (
        'Seller phone number(s) if printed on the document. '
        'When more than one, list in the same order as seller_name, separated by commas.'
    ),
    'buyer_email': (
        'Buyer email address(es) if printed on the document. '
        'When more than one, list in the same order as buyer_name, separated by commas.'
    ),
    'buyer_phone': (
        'Buyer phone number(s) if printed on the document. '
        'When more than one, list in the same order as buyer_name, separated by commas.'
    ),
    'side': (
        'Which side the uploading agent appears to represent, ONLY if explicitly '
        'indicated on the document (listing agent / seller\'s broker / buyer\'s broker / '
        'selling agent labels, brokerage blocks, or clear representation language). '
        'Return exactly one of: buyer, seller, landlord, tenant, unknown. '
        'Prefer unknown over guessing when the representing side is unclear.'
    ),
    'document_type': 'Short label for the primary document (e.g. residential_contract, amendment, addendum).',
    'purchase_contract_type': (
        'Primary purchase contract family. Return exactly one of: '
        'resale_one_to_four, condominium, new_construction_complete, '
        'new_construction_incomplete, farm_and_ranch, other, unknown. '
        'Use the form title and contents, never the filename.'
    ),
    'financing_type': (
        'Primary financing type explicitly selected or stated. Return cash, '
        'conventional, fha, va, usda, seller_financing, assumption, other, or unknown.'
    ),
    'hoa_applicable': (
        'True only when the contract or attached addendum explicitly shows that a '
        'mandatory owners association applies; false only when explicitly shown otherwise; null when unclear.'
    ),
    'built_before_1978': (
        'True or false only when the contract package explicitly states whether the property was built before 1978; null when not shown.'
    ),
    'sale_of_other_property_contingency': (
        'True only when a sale-of-other-property contingency or addendum is visibly present; false only when explicitly absent; null when unclear.'
    ),
    'temporary_lease_type': (
        'Return buyer_temporary_lease, seller_temporary_lease, none, or unknown based only on explicit contract/addendum selections.'
    ),
    'survey_choice': 'Concise survey option explicitly selected in the contract, or null when unclear.',
    'offer_price': 'Total sales price from Paragraph 3C (digits only, no $ or commas).',
    'sales_price': 'Same as offer_price when both are present (digits only).',
    'purchase_price': 'Same as offer_price when both are present (digits only).',
    'earnest_money': 'Initial earnest money amount (digits only).',
    'option_fee': 'Option fee amount if any (digits only).',
    'option_period_days': 'Number of option period days if written.',
    'effective_date': 'Effective / execution date if written (YYYY-MM-DD).',
    'proposed_close_date': 'Closing date from Paragraph 9 (YYYY-MM-DD).',
    'closing_date': 'Same as proposed_close_date when both are present (YYYY-MM-DD).',
    'title_company': 'Escrow/title company name if written.',
    'buyer_signature_present': (
        'True only when at least one buyer signature is visibly present. '
        'False only when the buyer signature area is visibly blank. Use null when uncertain.'
    ),
    'seller_signature_present': (
        'True only when at least one seller signature is visibly present. '
        'False only when the seller signature area is visibly blank. Use null when uncertain.'
    ),
    # Broker Information page — used to identify which side the uploader is on.
    # Keep the two columns strictly separate; never merge or swap them.
    'listing_broker_firm': (
        'Listing Broker Firm name exactly as printed in the Broker Information '
        'section (the seller-side column). Null when blank.'
    ),
    'listing_broker_license_no': 'License No. printed for the Listing Broker Firm, or null.',
    'listing_associate_name': (
        "Listing Associate's Name from the Broker Information section, or null."
    ),
    'listing_associate_license_no': "License No. for the Listing Associate, or null.",
    'selling_associate_name': (
        "Selling Associate's Name from the Broker Information section (still the "
        'listing broker column on TREC forms), or null.'
    ),
    'other_broker_firm': (
        'Other Broker Firm name exactly as printed in the Broker Information '
        "section (the buyer's broker column). Null when blank."
    ),
    'other_broker_license_no': 'License No. printed for the Other Broker Firm, or null.',
    'other_broker_associate_name': (
        "Associate's Name in the Other Broker column, or null."
    ),
    'other_broker_associate_license_no': (
        "License No. for the associate in the Other Broker column, or null."
    ),
}

# Listing-agreement extraction (TXR-1101) — used when identity is listing_agreement.
LISTING_BOOTSTRAP_EXTRACTION_FIELDS = {
    'property_address': 'Full property street address from the listing agreement (street, city, state, ZIP if present).',
    'street_address': 'Street number and street name only, if separable from city/state/ZIP.',
    'city': 'City if written.',
    'state': 'State if written (2-letter preferred).',
    'zip_code': 'ZIP code if written.',
    'seller_name': (
        'Seller / owner name(s) as written. When multiple people, list each full name '
        'separated by " and ".'
    ),
    'seller_email': (
        'Seller email address(es) if printed (often near notices / Paragraph 20). '
        'When more than one, list in the same order as seller_name, separated by commas.'
    ),
    'seller_phone': (
        'Seller phone number(s) if printed. When more than one, list in the same '
        'order as seller_name, separated by commas.'
    ),
    'side': (
        'Which side the uploading agent appears to represent. For listing agreements '
        'this is almost always seller. Return exactly one of: buyer, seller, landlord, '
        'tenant, unknown. Prefer unknown over guessing.'
    ),
    'document_type': 'Short label for the primary document (e.g. listing_agreement).',
    'list_price': 'The listing/sales price of the property (digits only, no $ or commas).',
    'listing_start_date': 'The listing agreement start/beginning date (YYYY-MM-DD).',
    'listing_end_date': 'The listing agreement end/expiration date (YYYY-MM-DD).',
    'broker_fee_section': (
        'Which Paragraph 5 fee path is used: "5a" if Seller pays Broker under 5A, '
        '"5b" if only 5B listing-broker fee is filled, or null if unclear.'
    ),
    'broker_fee_5a_choice': (
        'Within 5A(1): "percent" if (a) percent of sales price is filled, '
        '"other" if (b) free-text/other amount is filled, or null.'
    ),
    'broker_fee_raw_text': (
        'Exact free text from 5A(1)(b) when used '
        '(e.g. "$8,000 + 2% to a Buyer\'s Broker"). Null if unused.'
    ),
    'total_commission': (
        'ONLY when 5A(1) is a single percent of sales price. Number only, no %. '
        'If 5A(1)(b) is a hybrid flat + percent, leave null and fill listing_side_* / '
        'buyer_agent_* plus total_commission_display.'
    ),
    'total_commission_display': (
        'Human-readable total seller compensation from 5A(1), e.g. "6%", "$10,000", '
        'or "$8,000 + 2%". Required when the fee is not a single percent.'
    ),
    'listing_side_percent': (
        'Listing broker\'s own compensation as a percent of sales price after reasoning '
        'about 5A(1) and 5A(2). Number only, no %. Null if listing side is flat-only.'
    ),
    'listing_side_flat': (
        'Listing broker\'s own flat dollar compensation (digits only). '
        'Example: "$8,000 + 2% to a Buyer\'s Broker" with 5A(2)=2% → 8000.'
    ),
    'buyer_agent_percent': 'Buyer agent/other broker percentage share from Section 5A(2) (number only, no %).',
    'buyer_agent_flat': 'Buyer agent/other broker flat fee from Section 5A(2) (digits only).',
    'listing_only_percent': 'Listing broker only fee percentage from Section 5B(1) (number only, no %).',
    'listing_only_flat': 'Listing broker only flat fee from Section 5B(1) (digits only).',
    'protection_period_days': 'Number of days for the protection period from Section 5F (number only).',
    'financing_types': (
        'Comma-separated list of accepted financing types checked in Section 11C '
        '(e.g. "Conventional, VA, FHA, Cash"). Only include types explicitly checked.'
    ),
    'has_hoa': (
        'Whether the property is subject to a mandatory owners association. '
        'Return "yes" if Section 2E "is" is checked, OR if Special Provisions / other '
        'filled text names an HOA, POA, or owners association. Return "no" only if '
        'Section 2E "is not" is checked and no HOA is named. Null if unmarked/unclear.'
    ),
    'has_existing_survey': (
        'Whether the seller has or will provide an existing survey. Return "yes" if '
        'Special Provisions or other filled text says an existing survey will be '
        'provided / is available (including T-47 / T-47.1 language with a survey date). '
        'Return "no" only if the document explicitly says no survey is available. '
        'Null if not stated.'
    ),
    'built_before_1978': (
        'Whether the property was built before 1978. Return true/false ONLY if the '
        'document explicitly states year built or pre-1978 / post-1978 status. '
        'An unchecked lead-paint addendum checkbox is NOT enough — return null.'
    ),
    'special_districts': (
        'Whether the property is in a MUD, PID, or other special taxing district. '
        'Return true/false ONLY if explicitly stated in filled text. An unchecked '
        'MUD/tax-district addendum checkbox is NOT enough — return null.'
    ),
    'flood_hazard': (
        'Whether the property is in a special flood hazard area. Return true/false '
        'ONLY if explicitly stated. An unchecked flood-hazard addendum checkbox '
        'is NOT enough — return null.'
    ),
    'has_septic': (
        'Whether the property has a septic / on-site sewer facility. Return true/false '
        'ONLY if explicitly stated. An unchecked sewer-facility addendum checkbox '
        'is NOT enough — return null.'
    ),
    'referral_fee': (
        'Whether another broker/agent will receive a referral fee on this listing. '
        'Return true only if a referral fee / referral agreement is explicitly stated. '
        'Service-provider referral language in Paragraph 5D(2) does NOT count. '
        'Null if not stated.'
    ),
    'special_provisions': 'The full text of any special provisions from Section 15, or null if blank.',
    'seller_signature_present': (
        'True only when at least one seller signature is visibly present. '
        'False only when the seller signature area is visibly blank. Use null when uncertain.'
    ),
    'detected_documents': (
        'JSON array of every distinct document identified inside this PDF, in order. '
        'Each item must include "document_type" using one of: listing_agreement, iabs, '
        'sellers_disclosure, lead_based_paint, hoa_addendum, wire_fraud_warning, '
        'flood_hazard, t47_affidavit, special_tax_district_notice, sewer_facility, '
        'referral_agreement, other. Each item must also include 1-based "start_page" '
        'and "end_page" integers and an optional human "title". '
        'Only list documents that are actually present as pages in this PDF — '
        'do not invent forms that are merely checked as addenda on the listing agreement. '
        'If the PDF is only the listing agreement, return a single-item array.'
    ),
}

# Extracted only to identify the uploader's side; not deal terms to review.
_ROUTING_ONLY_FIELDS = frozenset({
    'listing_broker_firm',
    'listing_broker_license_no',
    'listing_associate_name',
    'listing_associate_license_no',
    'selling_associate_name',
    'other_broker_firm',
    'other_broker_license_no',
    'other_broker_associate_name',
    'other_broker_associate_license_no',
})

_NAME_SUFFIXES = frozenset({'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'esq', 'esq.'})
_VALID_SIDES = frozenset({'buyer', 'seller', 'landlord', 'tenant'})
_DATE_FIELD_KEYS = frozenset({
    'listing_start_date',
    'listing_end_date',
    'effective_date',
    'closing_date',
    'proposed_close_date',
    'close_date',
})


def _normalize_date_value(value: Any) -> Any:
    """Accept MM/DD/YYYY or YYYY-MM-DD; store as YYYY-MM-DD. Pass through others."""
    if value is None or value == '':
        return value
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    iso = re.match(r'^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$', text)
    if iso:
        return f'{iso.group(1)}-{iso.group(2)}-{iso.group(3)}'
    us = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', text)
    if us:
        month, day, year = us.groups()
        return f'{year}-{int(month):02d}-{int(day):02d}'
    return value

_FIELD_PRESENTATION = {
    'property_address': ('Property address', 'deal', False),
    'street_address': ('Street address', 'deal', False),
    'city': ('City', 'deal', False),
    'state': ('State', 'deal', False),
    'zip_code': ('ZIP code', 'deal', False),
    'sales_price': ('Sales price', 'deal', False),
    'purchase_price': ('Purchase price', 'deal', False),
    'offer_price': ('Offer price', 'deal', False),
    'list_price': ('List price', 'deal', False),
    'listing_start_date': ('Listing start', 'deadlines', True),
    'listing_end_date': ('Listing end', 'deadlines', True),
    'total_commission': ('Total commission %', 'deal', False),
    'total_commission_display': ('Total commission', 'deal', False),
    'listing_side_percent': ('Listing side commission %', 'deal', False),
    'listing_side_flat': ('Listing side commission $', 'deal', False),
    'buyer_agent_percent': ('Buyer side commission %', 'deal', False),
    'buyer_agent_flat': ('Buyer side commission $', 'deal', False),
    'broker_fee_raw_text': ('Broker fee text (5A(1)(b))', 'deal', False),
    'has_hoa': ('HOA', 'other', False),
    'effective_date': ('Effective date', 'deadlines', True),
    'closing_date': ('Closing date', 'deadlines', True),
    'proposed_close_date': ('Closing date', 'deadlines', True),
    'option_period_days': ('Option period', 'deadlines', True),
    'earnest_money': ('Earnest money', 'deadlines', True),
    'option_fee': ('Option fee', 'deadlines', True),
    'title_company': ('Title company', 'other', False),
    'buyer_name': ('Buyer', 'party_names', False),
    'seller_name': ('Seller', 'party_names', False),
    'buyer_email': ('Buyer email', 'other', False),
    'seller_email': ('Seller email', 'other', False),
    'buyer_phone': ('Buyer phone', 'other', False),
    'seller_phone': ('Seller phone', 'other', False),
    'buyer_signature_present': ('Buyer signature', 'other', True),
    'seller_signature_present': ('Seller signature', 'other', True),
}

_LOW_CONFIDENCE_THRESHOLD = 0.75


def split_party_names(raw: str) -> list[str]:
    """Split a party string into individual full names.

    Handles "Billy Copaus and Kimberly Copaus", ampersands, semicolons, slashes,
    and commas between full names. Avoids over-splitting middle names / suffixes.
    """
    if raw is None:
        return []
    text = re.sub(r'\s+', ' ', str(raw)).strip()
    if not text:
        return []

    chunks = re.split(r'\s+and\s+|\s+&\s+|/', text, flags=re.IGNORECASE)
    names: list[str] = []
    for chunk in chunks:
        for piece in chunk.split(';'):
            piece = piece.strip()
            if not piece:
                continue
            names.extend(_split_comma_full_names(piece))
    return names


def _split_comma_full_names(text: str) -> list[str]:
    """Split on commas only when they separate distinct full names."""
    parts = [p.strip() for p in text.split(',') if p.strip()]
    if len(parts) <= 1:
        return [text.strip()] if text.strip() else []

    names: list[str] = []
    i = 0
    while i < len(parts):
        current = parts[i]
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        if nxt and nxt.lower().rstrip('.') in {s.rstrip('.') for s in _NAME_SUFFIXES}:
            names.append(f'{current}, {nxt}')
            i += 2
            continue
        # "Last, First" (single token each) → one person
        if nxt and len(current.split()) == 1 and len(nxt.split()) == 1:
            if nxt.lower().rstrip('.') not in {s.rstrip('.') for s in _NAME_SUFFIXES}:
                names.append(f'{nxt} {current}')
                i += 2
                continue
        names.append(current)
        i += 1
    return names


def parse_person_name(full: str) -> dict[str, str]:
    """Parse a full name into first_name / last_name."""
    text = re.sub(r'\s+', ' ', (full or '')).strip()
    if not text:
        return {'first_name': '', 'last_name': ''}
    parts = text.split()
    if len(parts) == 1:
        return {'first_name': parts[0], 'last_name': ''}
    # Keep suffixes with last name: "Billy Copaus, Jr." / "Mary Ann Smith"
    if parts[-1].lower().rstrip('.') in {s.rstrip('.') for s in _NAME_SUFFIXES} and len(parts) >= 3:
        return {
            'first_name': ' '.join(parts[:-2]),
            'last_name': ' '.join(parts[-2:]),
        }
    return {'first_name': ' '.join(parts[:-1]), 'last_name': parts[-1]}


_EMAIL_RE = re.compile(r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}', re.I)

_ROLE_CONTACT_KEYS = {
    'seller': {
        'email': ('seller_email', 'seller_email_fax', 'seller_emails'),
        'phone': ('seller_phone', 'seller_phones'),
        'email_extra': ('seller_email_2', 'seller_email_fax_2', 'co_seller_email'),
        'phone_extra': ('seller_phone_2', 'co_seller_phone'),
    },
    'buyer': {
        'email': ('buyer_email', 'buyer_emails'),
        'phone': ('buyer_phone', 'buyer_phones'),
        'email_extra': ('buyer_email_2', 'co_buyer_email'),
        'phone_extra': ('buyer_phone_2', 'co_buyer_phone'),
    },
}


def _session_field_value(session: ContractBootstrapSession, *keys: str) -> Any:
    classification = session.classification or {}
    candidates = session.extracted_candidates or {}
    for key in keys:
        raw = classification.get(key)
        if raw not in (None, '', [], {}):
            return raw
        cand = candidates.get(key)
        if isinstance(cand, dict):
            value = cand.get('value')
            if value not in (None, '', [], {}):
                return value
        elif cand not in (None, '', [], {}):
            return cand
    return None


def _split_contact_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r'\s*(?:,|;|/|\band\b)\s*', text) if part.strip()]


def _emails_from(raw: Any) -> list[str]:
    if raw is None:
        return []
    found = [match.group(0).lower() for match in _EMAIL_RE.finditer(str(raw))]
    if found:
        return found
    return [part.lower() for part in _split_contact_values(raw) if '@' in part]


def _phones_from(raw: Any) -> list[str]:
    from utils import format_phone_number

    phones: list[str] = []
    seen: set[str] = set()
    for part in _split_contact_values(raw) or [str(raw or '')]:
        formatted = format_phone_number(part)
        if formatted and formatted not in seen:
            seen.add(formatted)
            phones.append(formatted)
    return phones


def _party_family(role: str) -> str | None:
    if role in ('seller', 'co_seller'):
        return 'seller'
    if role in ('buyer', 'co_buyer'):
        return 'buyer'
    return None


def _party_extracted_contact_info(
    session: ContractBootstrapSession,
    role: str,
    index: int,
) -> dict[str, str | None]:
    """Pull email/phone/address for one party from extracted document fields."""
    info: dict[str, str | None] = {
        'email': None,
        'phone': None,
        'street_address': None,
        'city': None,
        'state': None,
        'zip_code': None,
    }
    family = _party_family(role)
    if not family:
        return info

    keys = _ROLE_CONTACT_KEYS[family]
    emails = _emails_from(_session_field_value(session, *keys['email']))
    phones = _phones_from(_session_field_value(session, *keys['phone']))
    if index >= 1:
        extra_emails = _emails_from(_session_field_value(session, *keys['email_extra']))
        extra_phones = _phones_from(_session_field_value(session, *keys['phone_extra']))
        emails = extra_emails or emails[1:]
        phones = extra_phones or phones[1:]
    if emails:
        info['email'] = emails[0][:120]
    if phones:
        info['phone'] = phones[0][:20]
    if family == 'seller':
        street = _session_field_value(session, 'seller_address', 'street_address')
        if street:
            info['street_address'] = str(street).strip()[:200]
        for field, limit in (('city', 100), ('state', 50), ('zip_code', 20)):
            value = _session_field_value(session, f'seller_{field}', field)
            if value:
                info[field] = str(value).strip()[:limit]
    return info


def _find_contact_by_email(org_id: int, email: str | None) -> Contact | None:
    if not org_id or not email:
        return None
    from sqlalchemy import func

    return Contact.query.filter(
        Contact.organization_id == org_id,
        func.lower(Contact.email) == email.strip().lower(),
    ).first()


def default_party_resolutions(
    session: ContractBootstrapSession,
    user_id: int,
) -> list[dict[str, Any]]:
    """Link a high-confidence match, otherwise create a contact from the document."""
    stored = (session.classification or {}).get('parties')
    parties = stored if isinstance(stored, list) and stored else build_party_proposals(
        session, user_id,
    )
    resolutions: list[dict[str, Any]] = []
    for party in parties:
        if not isinstance(party, dict):
            continue
        first = (party.get('first_name') or '').strip()
        last = (party.get('last_name') or '').strip()
        full = (party.get('full_name') or '').strip()
        if not first and not last and not full:
            continue
        recommended = party.get('recommended_contact_id')
        action = 'link' if recommended else 'create'
        resolutions.append({
            'party_key': party.get('party_key'),
            'role': party.get('role'),
            'full_name': full,
            'first_name': first or (full.split()[0] if full else ''),
            'last_name': last,
            'action': action,
            'contact_id': recommended,
            'email': party.get('email'),
            'phone': party.get('phone'),
            'street_address': party.get('street_address'),
            'city': party.get('city'),
            'state': party.get('state'),
            'zip_code': party.get('zip_code'),
        })
    return resolutions


def find_contact_matches(
    org_id: int,
    user_id: int,
    full_name: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find org contacts matching a full name (case-insensitive).

    Prefers exact first+last matches, then last-name matches. User-owned
    contacts score higher than other org contacts.
    """
    if not org_id or not full_name:
        return []

    parsed = parse_person_name(full_name)
    first = (parsed.get('first_name') or '').strip()
    last = (parsed.get('last_name') or '').strip()
    if not first and not last:
        return []

    from sqlalchemy import func

    results: list[dict[str, Any]] = []
    seen: set[int] = set()

    def _row(contact: Contact, score: float) -> dict[str, Any]:
        return {
            'id': contact.id,
            'name': f'{contact.first_name} {contact.last_name}'.strip(),
            'email': contact.email,
            'phone': contact.phone,
            'score': score,
        }

    if first and last:
        exact = Contact.query.filter(
            Contact.organization_id == org_id,
            func.lower(Contact.first_name) == first.lower(),
            func.lower(Contact.last_name) == last.lower(),
        ).all()
        for contact in exact:
            if contact.id in seen:
                continue
            seen.add(contact.id)
            score = 1.0 if contact.user_id == user_id else 0.9
            results.append(_row(contact, score))

    if last and len(results) < limit:
        last_matches = Contact.query.filter(
            Contact.organization_id == org_id,
            func.lower(Contact.last_name) == last.lower(),
        ).all()
        for contact in last_matches:
            if contact.id in seen:
                continue
            seen.add(contact.id)
            score = 0.7 if contact.user_id == user_id else 0.5
            results.append(_row(contact, score))

    results.sort(key=lambda m: (-m['score'], 0 if m.get('id') else 1))
    # Stable prefer: user contacts already scored higher
    return results[:limit]


def build_party_proposals(
    session: ContractBootstrapSession,
    user_id: int,
) -> list[dict[str, Any]]:
    """Build per-person party proposals with contact match suggestions."""
    classification = session.classification or {}
    candidates = session.extracted_candidates or {}
    parties: list[dict[str, Any]] = []

    for field_key, primary_role, co_role in (
        ('seller_name', 'seller', 'co_seller'),
        ('buyer_name', 'buyer', 'co_buyer'),
    ):
        candidate = candidates.get(field_key) or {}
        raw_names = classification.get(field_key) or candidate.get('value') or ''
        names = split_party_names(raw_names)
        for idx, full_name in enumerate(names):
            role = primary_role if idx == 0 else co_role
            parsed = parse_person_name(full_name)
            matches = find_contact_matches(
                session.organization_id, user_id, full_name, limit=5,
            )
            extracted = _party_extracted_contact_info(session, role, idx)
            recommended = None
            if matches:
                top = matches[0]
                if top.get('score', 0) >= 0.9:
                    recommended = top['id']
            if not recommended and extracted.get('email'):
                by_email = _find_contact_by_email(
                    session.organization_id, extracted['email'],
                )
                if by_email:
                    recommended = by_email.id
                    if not any(m.get('id') == by_email.id for m in matches):
                        matches.insert(0, {
                            'id': by_email.id,
                            'name': f'{by_email.first_name} {by_email.last_name}'.strip(),
                            'email': by_email.email,
                            'phone': by_email.phone,
                            'score': 0.95,
                        })
            parties.append({
                'party_key': f'{primary_role}_{idx}',
                'role': role,
                'full_name': full_name,
                'first_name': parsed['first_name'],
                'last_name': parsed['last_name'],
                'matches': matches,
                'recommended_contact_id': recommended,
                'default_action': 'link' if recommended else 'create',
                **extracted,
            })
    return parties


def _build_bob_questions(
    parties: list[dict[str, Any]],
    classification: dict[str, Any],
) -> dict[str, Any]:
    """Structured confirmation questions for the review UI."""
    sellers = [p for p in parties if p.get('role') in ('seller', 'co_seller')]
    buyers = [p for p in parties if p.get('role') in ('buyer', 'co_buyer')]

    def _short(p: dict[str, Any]) -> str:
        return (p.get('first_name') or p.get('full_name') or 'Unknown').strip()

    seller_labels = '/'.join(_short(p) for p in sellers) if sellers else 'unknown'
    buyer_labels = '/'.join(_short(p) for p in buyers) if buyers else 'unknown'

    return {
        'side': {
            'key': 'side',
            'prompt': 'Which side are YOU representing?',
            'required': True,
            'options': [
                {'value': 'seller', 'label': 'Seller'},
                {'value': 'buyer', 'label': 'Buyer'},
            ],
            'help': (
                f'This contract lists sellers {seller_labels} and buyers {buyer_labels} '
                '— pick which side you represent.'
            ),
        },
        'parties': {
            'key': 'parties',
            'prompt': 'People on this deal',
            'required': True,
            'help': 'If nobody matches, we create a contact from the document.',
            'items': parties,
        },
    }


def _normalize_side(value: Any) -> str:
    side = (str(value).strip().lower() if value is not None else '') or 'unknown'
    if side in _VALID_SIDES or side == 'unknown':
        return side
    return 'unknown'


def _norm_address(address: str | None) -> str:
    """Normalize address for matching: lowercase, collapse whitespace, strip punctuation."""
    if not address:
        return ''
    s = address.lower().strip()
    s = re.sub(r'\b(street|st|avenue|ave|road|rd|drive|dr|lane|ln|court|ct|boulevard|blvd)\b', '', s)
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _page_count_from_bytes(file_bytes: bytes, mime_type: str) -> int:
    """Attempt to get page count using PyMuPDF if available, else 0."""
    if 'pdf' not in (mime_type or '').lower():
        return 0
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=file_bytes, filetype='pdf')
        count = len(doc)
        doc.close()
        return count
    except ImportError:
        logger.warning('PyMuPDF not available, cannot count pages')
        return 0
    except Exception as e:
        logger.warning('Failed to count PDF pages: %s', e)
        return 0


def _parse_date(value: Any) -> date | None:
    if value is None or value == '':
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y'):
        try:
            return datetime.strptime(text[:10] if fmt == '%Y-%m-%d' else text, fmt).date()
        except ValueError:
            continue
    return None


def _bootstrap_storage_dir(org_id: int) -> Path:
    root = Path(__file__).resolve().parent.parent / 'instance' / 'bootstrap' / str(org_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _mark_bootstrap_storage_backend(
    session: ContractBootstrapSession,
    backend: str,
) -> None:
    classification = dict(session.classification or {})
    classification['storage_backend'] = backend
    session.classification = classification
    flag_modified(session, 'classification')


def _local_bootstrap_mirror_path(
    *,
    organization_id: int,
    session_id: int,
    filename: str | None,
) -> tuple[Path, str]:
    """Return (absolute_path, repo-relative path) for the on-disk bootstrap mirror."""
    safe_name = re.sub(r'[^\w.\-]+', '_', filename or 'contract.pdf')[:180]
    path = _bootstrap_storage_dir(organization_id) / f'{session_id}_{safe_name}'
    rel = str(path.relative_to(Path(__file__).resolve().parent.parent))
    return path, rel


def store_bootstrap_file(
    *,
    session: ContractBootstrapSession,
    file_bytes: bytes,
) -> str:
    """Persist bootstrap bytes to Supabase when possible, always mirror locally.

    Multi-file inbox uploads kick off several background jobs at once. A local
    mirror keeps those jobs readable even when Supabase download races or SQLite
    is under write pressure.
    """
    safe_name = re.sub(r'[^\w.\-]+', '_', session.original_filename or 'contract.pdf')[:180]
    storage_key = f'bootstrap/{session.organization_id}/{session.id}/{safe_name}'
    content_type = session.mime_type or 'application/pdf'
    local_path, local_rel = _local_bootstrap_mirror_path(
        organization_id=session.organization_id,
        session_id=session.id,
        filename=session.original_filename,
    )
    local_path.write_bytes(file_bytes)

    classification = dict(session.classification or {})
    classification['local_cache_path'] = local_rel

    try:
        from services.supabase_storage import TRANSACTION_DOCUMENTS_BUCKET, upload_file

        upload_file(
            TRANSACTION_DOCUMENTS_BUCKET,
            storage_key,
            file_bytes,
            session.original_filename or safe_name,
            content_type,
        )
        session.storage_path = storage_key
        classification['storage_backend'] = 'supabase'
        session.classification = classification
        flag_modified(session, 'classification')
        db.session.flush()
        return storage_key
    except Exception as exc:
        # Local SQLite / missing SUPABASE_* — durable enough for single-node dev.
        logger.warning(
            'Bootstrap Supabase upload failed; using local storage: %s',
            exc,
        )

    session.storage_path = local_rel
    classification['storage_backend'] = 'local'
    session.classification = classification
    flag_modified(session, 'classification')
    db.session.flush()
    return local_rel


def _looks_like_local_bootstrap_path(storage_path: str) -> bool:
    return (
        storage_path.startswith('instance/')
        or storage_path.startswith('/')
        or storage_path.startswith('instance\\')
    )


def _read_local_bootstrap_path(storage_path: str) -> bytes | None:
    if not storage_path:
        return None
    if storage_path.startswith('/'):
        local_path = Path(storage_path)
    else:
        local_path = Path(__file__).resolve().parent.parent / storage_path
    if local_path.is_file():
        return local_path.read_bytes()
    return None


def read_bootstrap_file(session: ContractBootstrapSession) -> bytes | None:
    if not session.storage_path:
        # Fall through to local mirror candidates below.
        storage_path = ''
    else:
        storage_path = session.storage_path

    looks_local = _looks_like_local_bootstrap_path(storage_path) if storage_path else False
    classification = session.classification or {}
    local_cache = classification.get('local_cache_path')

    # Prefer the local mirror first — multi-upload jobs on SQLite are racing each
    # other and should not depend on a remote round-trip for every file.
    for candidate in (
        local_cache,
        storage_path if looks_local else None,
    ):
        data = _read_local_bootstrap_path(candidate or '')
        if data:
            return data

    if storage_path and not looks_local:
        try:
            from services.supabase_storage import TRANSACTION_DOCUMENTS_BUCKET, download_file

            return download_file(TRANSACTION_DOCUMENTS_BUCKET, storage_path)
        except Exception as exc:
            logger.warning(
                'Bootstrap Supabase download failed for %s: %s',
                storage_path,
                exc,
            )

    # Conventional mirror path even if classification lost the cache key.
    if session.id and session.organization_id:
        mirror_path, _ = _local_bootstrap_mirror_path(
            organization_id=session.organization_id,
            session_id=session.id,
            filename=session.original_filename,
        )
        if mirror_path.is_file():
            return mirror_path.read_bytes()

    # Path looked local but file missing — try Supabase once more.
    if storage_path and looks_local:
        try:
            from services.supabase_storage import TRANSACTION_DOCUMENTS_BUCKET, download_file

            return download_file(TRANSACTION_DOCUMENTS_BUCKET, storage_path)
        except Exception:
            pass
    return None


def record_upload_metadata(
    *,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    source: str,
    user: User,
    org_id: int,
    document_id: int | None = None,
) -> ContractBootstrapSession:
    """Record upload metadata for a contract document."""
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    page_count = _page_count_from_bytes(file_bytes, mime_type)

    session = ContractBootstrapSession(
        organization_id=org_id,
        uploader_user_id=user.id,
        document_id=document_id,
        file_sha256=sha256,
        original_filename=filename,
        mime_type=mime_type,
        page_count=page_count,
        upload_source=source,
        status=ContractBootstrapSession.STATUS_UPLOADED,
        match_status=ContractBootstrapSession.MATCH_PENDING,
    )
    db.session.add(session)
    db.session.flush()

    logger.info(
        'ContractBootstrapSession %s created: filename=%s sha256=%s pages=%d',
        session.id, filename, sha256[:8], page_count,
    )
    return session


def classify_upload_identity(
    *,
    file_bytes: bytes,
    filename: str | None = None,
    field_hints: dict[str, Any] | None = None,
):
    """Deterministic PDF identity for bootstrap (no OpenAI)."""
    from services.document_identity import identify_from_pdf

    return identify_from_pdf(
        file_bytes,
        filename=filename,
        field_hints=field_hints,
    )


def infer_side_for_upload(
    *,
    identity,
    file_bytes: bytes | None = None,
    user: Any = None,
    field_data: dict[str, Any] | None = None,
    text: str | None = None,
):
    """Infer the uploader's representation side from the document. Fails soft."""
    from services.representation_inference import (
        RepresentationInference,
        agent_profile_for_user,
        infer_representation,
    )

    try:
        if text is None:
            from services.document_identity import extract_pdf_text

            text = extract_pdf_text(file_bytes or b'')
        return infer_representation(
            identity=identity,
            text=text or '',
            field_data=field_data or {},
            profile=agent_profile_for_user(user),
        )
    except Exception:
        logger.exception('Representation inference failed')
        return RepresentationInference()


def _extraction_field_map_for_identity(identity) -> tuple[dict[str, str], str]:
    """Return (fields, system_prompt) for the bootstrap extraction schema."""
    from services.document_identity import KIND_LISTING_AGREEMENT

    if identity is not None and getattr(identity, 'kind', None) == KIND_LISTING_AGREEMENT:
        return (
            dict(LISTING_BOOTSTRAP_EXTRACTION_FIELDS),
            (
                'You are a precise document data extractor for Texas residential '
                'listing agreements (TXR-1101). A single uploaded PDF may be a '
                'listing package that also contains IABS, Seller\'s Disclosure, '
                'HOA addenda, lead-paint forms, and similar paperwork. Always '
                'populate detected_documents with one entry per distinct document '
                'actually present in the PDF, with accurate 1-based page ranges. '
                'Do NOT invent documents that are only mentioned or checked as '
                'needed addenda. Extract ONLY values explicitly written. '
                'Do NOT invent values. '
                'COMMISSION / BROKER FEE (Paragraph 5): reason carefully. '
                '5A(1)(b) is often free text for hybrid fees '
                '(flat dollars for the listing broker + percent for a buyer\'s broker). '
                'Parse that text together with 5A(2). '
                'Example: 5A(1)(b)="$8,000 + 2% to a Buyer\'s Broker" and 5A(2)=2% → '
                'listing_side_flat=8000, buyer_agent_percent=2, total_commission=null, '
                'total_commission_display="$8,000 + 2%", broker_fee_section="5a", '
                'broker_fee_5a_choice="other", broker_fee_raw_text=exact 5A(1)(b) text. '
                'When 5A(1)(a) is a single percent (e.g. 6%) and 5A(2) is 3%, set '
                'total_commission=6, listing_side_percent=3, buyer_agent_percent=3. '
                'Never force a hybrid flat+percent structure into total_commission '
                'as a single percent number.'
            ),
        )
    return (
        dict(BOOTSTRAP_EXTRACTION_FIELDS),
        (
            'You are a precise document data extractor for Texas residential '
            'purchase contracts and related forms. Extract ONLY values explicitly '
            'written on the document. Do NOT invent values, do NOT infer legal '
            'sufficiency, and use null when blank.'
        ),
    )


def extract_contract_fields(
    *,
    file_bytes: bytes,
    identity=None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Run AI extraction for bootstrap (observations only). Fail soft to {}."""
    try:
        from services.document_extractor import (
            _build_extraction_prompt,
            _extract_pdf_text,
            _render_pdf_to_images,
        )
        from services.ai_service import generate_document_extraction
    except Exception as e:
        logger.warning('Bootstrap extraction unavailable: %s', e)
        return {}

    if identity is None and file_bytes:
        try:
            identity = classify_upload_identity(
                file_bytes=file_bytes,
                filename=filename,
            )
        except Exception:
            identity = None

    field_map, system_prompt = _extraction_field_map_for_identity(identity)
    extraction_fields = dict(field_map)
    extraction_fields['_meta'] = (
        'Object keyed by field name. For each extracted field include the 1-based '
        'source page number and confidence from 0 to 1. Omit fields without evidence.'
    )
    schema = {
        'fields': extraction_fields,
        'system_prompt': system_prompt,
    }

    try:
        images = _render_pdf_to_images(file_bytes)
        pdf_text = _extract_pdf_text(file_bytes)
        user_prompt = _build_extraction_prompt(schema)
        if pdf_text:
            user_prompt = (
                f"{user_prompt}\n\n"
                "Selectable PDF text follows. Images are authoritative for checkboxes/layout.\n\n"
                f"{pdf_text[:60000]}"
            )
        result = generate_document_extraction(
            system_prompt=schema['system_prompt'],
            user_prompt=user_prompt,
            images=images,
        ) or {}
        filtered = {
            key: result.get(key)
            for key in field_map
            if result.get(key) not in (None, '', [], {})
        }
        if isinstance(result.get('_meta'), dict):
            filtered['_meta'] = result['_meta']
        return filtered
    except Exception as e:
        logger.exception('Bootstrap extraction failed: %s', e)
        return {}


def classify_and_extract(
    *,
    session: ContractBootstrapSession,
    field_data: dict[str, Any],
    identity=None,
) -> ContractBootstrapSession:
    """Map field_data into classification + extracted_candidates with evidence stubs."""
    from services.document_identity import DocumentIdentity, identify_from_text
    from services.document_routing import TransactionContext, decide_route

    existing_classification = dict(session.classification or {})
    human_side = _normalize_side(existing_classification.get('side'))
    side_was_confirmed = bool(existing_classification.get('side_confirmed_by_user'))
    extracted_side = _normalize_side(field_data.get('side'))

    from services.document_identity import refresh_execution_state

    # Prefer caller-supplied identity; else reuse persisted; else weak text fallback.
    if identity is None and existing_classification.get('document_identity'):
        identity = DocumentIdentity.from_dict(
            existing_classification.get('document_identity'),
        )
    if identity is None:
        identity = identify_from_text(
            '',
            filename=session.original_filename,
            field_hints=field_data,
        )

    # Refresh execution from AI signature fields without reclassifying content.
    if field_data:
        identity = refresh_execution_state(identity, field_hints=field_data)
        if field_data.get('purchase_contract_type') and not identity.purchase_contract_type:
            identity = DocumentIdentity(
                kind=identity.kind,
                template_slug=identity.template_slug,
                form_number=identity.form_number,
                label=identity.label,
                confidence=identity.confidence,
                matched_signals=identity.matched_signals,
                execution_state=identity.execution_state,
                possible_scopes=identity.possible_scopes,
                purchase_contract_type=field_data.get('purchase_contract_type'),
                addendum_key=identity.addendum_key,
                offer_document_type=identity.offer_document_type,
                ambiguous=identity.ambiguous,
                extras=dict(identity.extras or {}),
            )
        # AI detected_documents owns package membership after extraction.
        from services.document_identity import apply_ai_package_authority
        identity = apply_ai_package_authority(identity, field_data)

    # Let the document answer the representation question when it can. Form type
    # settles listing / buyer-rep agreements; the Broker Information block
    # settles two-sided contracts once extraction has read it.
    from services.representation_inference import RepresentationInference

    inference = RepresentationInference.from_dict(
        existing_classification.get('representation_inference'),
    )
    if not side_was_confirmed and not inference.is_confident:
        retry = infer_side_for_upload(
            identity=identity,
            user=User.query.get(session.uploader_user_id)
            if session.uploader_user_id
            else None,
            field_data=field_data,
            text='',
        )
        if retry.is_confident or retry.summary:
            inference = retry

    inferred_side = None if side_was_confirmed else (
        inference.side if inference.is_confident else None
    )

    route = decide_route(
        identity=identity,
        representation_side=(
            human_side if side_was_confirmed else (inferred_side or extracted_side)
        ),
        side_confirmed=side_was_confirmed or bool(inferred_side),
        transaction=TransactionContext(),
        destination_choice=existing_classification.get('destination_choice'),
    )

    classification = {
        key: value
        for key, value in existing_classification.items()
        if key in (
            'storage_backend',
            'processing_started_at',
            'processing_completed_at',
            'processing_failed_at',
            'processing_error',
            'destination_choice',
            # Multi-PDF inbox package — must survive classify/extract.
            'upload_batch_id',
        )
    }
    classification.update({
        'document_type': (
            identity.kind
            if identity and identity.kind not in ('unknown', 'other')
            else field_data.get('document_type')
            or field_data.get('document_classification')
            or 'contract'
        ),
        # Representation is a human fact unless the form type makes it
        # unambiguous (a listing agreement is always seller representation).
        'side': (
            human_side if side_was_confirmed else (inferred_side or extracted_side)
        ),
        'side_confirmed_by_user': side_was_confirmed or bool(inferred_side),
        'side_inferred_from_document': inferred_side,
        'representation_inference': inference.to_dict(),
        'extracted_side_hint': extracted_side if extracted_side != 'unknown' else None,
        'detected_documents': (
            field_data.get('detected_documents')
            if isinstance(field_data.get('detected_documents'), list)
            else existing_classification.get('detected_documents')
        ),
        'property_address': (
            field_data.get('property_address')
            or field_data.get('street_address')
        ),
        'buyer_name': field_data.get('buyer_name') or field_data.get('buyer_names'),
        'seller_name': field_data.get('seller_name') or field_data.get('seller_names'),
        'buyer_email': field_data.get('buyer_email'),
        'seller_email': field_data.get('seller_email') or field_data.get('seller_email_fax'),
        'buyer_phone': field_data.get('buyer_phone'),
        'seller_phone': field_data.get('seller_phone'),
        'document_identity': identity.to_dict() if identity else None,
        'route_decision': route.to_dict(),
        'purchase_contract_type': (
            field_data.get('purchase_contract_type')
            or (identity.purchase_contract_type if identity else None)
        ),
    })

    candidates = {}
    for key, value in field_data.items():
        if key.startswith('_') or key in ('document_type', 'document_classification', 'side'):
            continue
        if key in _ROUTING_ONLY_FIELDS:
            continue
        if value in (None, '', [], {}):
            continue

        meta = field_data.get('_meta', {}).get(key, {}) if '_meta' in field_data else {}
        evidence = {
            'source': 'extraction',
            'page': meta.get('page'),
            'confidence': meta.get('confidence'),
        }
        candidates[key] = {
            'value': value,
            'evidence': evidence,
        }

    # Normalize common aliases into canonical keys when missing
    alias_map = {
        'buyer_names': 'buyer_name',
        'seller_names': 'seller_name',
        'seller_email_fax': 'seller_email',
        'offer_price': 'sales_price',
        'proposed_close_date': 'closing_date',
    }
    for src, dest in alias_map.items():
        if src in candidates and dest not in candidates:
            candidates[dest] = candidates[src]

    session.classification = classification
    session.extracted_candidates = candidates
    db.session.flush()

    # Per-person party proposals (contact match suggestions) for review UI
    parties = build_party_proposals(session, session.uploader_user_id)
    classification = dict(session.classification or {})
    classification['parties'] = parties
    session.classification = classification
    flag_modified(session, 'classification')
    db.session.flush()

    logger.info(
        'ContractBootstrapSession %s classified: type=%s address=%s fields=%d parties=%d',
        session.id,
        classification['document_type'],
        classification.get('property_address'),
        len(candidates),
        len(parties),
    )
    return session


def find_transaction_matches(
    *,
    org_id: int,
    address: str | None,
    party_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Find matching transactions within the same organization only.

    CRITICAL: NEVER match across organizations. Fail closed.
    """
    if not org_id:
        raise ValueError('org_id is required for transaction matching')

    if not address:
        return []

    norm_addr = _norm_address(address)
    if not norm_addr:
        return []

    transactions = Transaction.query.filter_by(
        organization_id=org_id,
    ).filter(
        Transaction.street_address.isnot(None),
    ).all()

    matches = []
    for tx in transactions:
        tx_norm = _norm_address(tx.street_address)
        if not tx_norm:
            continue

        if norm_addr == tx_norm:
            matches.append({
                'transaction_id': tx.id,
                'address': tx.street_address,
                'city': tx.city,
                'state': tx.state,
                'status': tx.status,
                'side': tx.transaction_type.name if tx.transaction_type else None,
                'score': 1.0,
                'reason': 'exact_address_match',
            })
            continue

        if norm_addr in tx_norm or tx_norm in norm_addr:
            matches.append({
                'transaction_id': tx.id,
                'address': tx.street_address,
                'city': tx.city,
                'state': tx.state,
                'status': tx.status,
                'side': tx.transaction_type.name if tx.transaction_type else None,
                'score': 0.8,
                'reason': 'partial_address_match',
            })

    matches.sort(key=lambda m: m['score'], reverse=True)

    logger.info(
        'find_transaction_matches org=%d address=%s found %d matches',
        org_id, address, len(matches),
    )
    return matches


def run_match_discovery(session: ContractBootstrapSession) -> ContractBootstrapSession:
    """Populate match_candidates and set awaiting_match / ambiguous status.

    Never silently attaches — agent must call resolve_match.
    """
    classification = session.classification or {}
    address = classification.get('property_address')
    party_names = [
        n for n in (classification.get('buyer_name'), classification.get('seller_name')) if n
    ]
    matches = find_transaction_matches(
        org_id=session.organization_id,
        address=address,
        party_names=party_names,
    )
    confirmed_side = _normalize_side(classification.get('side'))
    if classification.get('side_confirmed_by_user') and confirmed_side in ('buyer', 'seller'):
        matches = [
            match for match in matches
            if match.get('side') not in ('buyer', 'seller')
            or match.get('side') == confirmed_side
        ]
    session.match_candidates = matches
    if len(matches) > 1:
        session.match_status = ContractBootstrapSession.MATCH_AMBIGUOUS
    else:
        session.match_status = ContractBootstrapSession.MATCH_PENDING
    session.status = ContractBootstrapSession.STATUS_AWAITING_MATCH
    db.session.flush()
    return session


# Kind rank for multi-PDF inbox packages. Listing / purchase / amendment decide
# the file; supporting notices never become the primary review target.
_BATCH_PRIMARY_KIND_RANK = {
    'listing_agreement': 0,
    'purchase_contract': 1,
    'amendment': 2,
    'addendum': 3,
    'disclosure': 4,
    'proof_of_funds': 5,
    'other': 6,
    'unknown': 7,
}


def select_primary_bootstrap_session(
    sessions: list[ContractBootstrapSession],
) -> ContractBootstrapSession | None:
    """Pick the package-defining session from a multi-PDF inbox upload."""
    ready = [
        s for s in sessions
        if s.status not in (
            ContractBootstrapSession.STATUS_FAILED,
            ContractBootstrapSession.STATUS_CANCELLED,
        )
    ]
    if not ready:
        return sessions[0] if sessions else None

    def sort_key(session: ContractBootstrapSession):
        identity = (session.classification or {}).get('document_identity') or {}
        kind = (identity.get('kind') or 'unknown').strip().lower()
        conf = float(identity.get('confidence') or 0.0)
        return (
            _BATCH_PRIMARY_KIND_RANK.get(kind, 9),
            -conf,
            session.id or 0,
        )

    return sorted(ready, key=sort_key)[0]


def batch_sessions_ready(sessions: list[ContractBootstrapSession]) -> bool:
    """True when every non-failed session has finished identification."""
    if not sessions:
        return False
    terminal = {
        ContractBootstrapSession.STATUS_AWAITING_REVIEW,
        ContractBootstrapSession.STATUS_AWAITING_MATCH,
        ContractBootstrapSession.STATUS_APPLIED,
        ContractBootstrapSession.STATUS_APPROVED,
        ContractBootstrapSession.STATUS_FAILED,
        ContractBootstrapSession.STATUS_CANCELLED,
    }
    unfinished = [
        s for s in sessions
        if s.status not in terminal
    ]
    # At least one useful identity must exist before we open review.
    identified = [
        s for s in sessions
        if s.status in (
            ContractBootstrapSession.STATUS_AWAITING_REVIEW,
            ContractBootstrapSession.STATUS_AWAITING_MATCH,
            ContractBootstrapSession.STATUS_APPLIED,
            ContractBootstrapSession.STATUS_APPROVED,
        )
        and ((s.classification or {}).get('document_identity') or {}).get('kind')
        not in (None, '', 'unknown')
    ]
    return not unfinished and bool(identified)


def sessions_for_upload_batch(
    *,
    org_id: int,
    batch_id: str,
) -> list[ContractBootstrapSession]:
    """Load all bootstrap sessions that share an inbox upload batch id."""
    if not batch_id:
        return []
    # JSON containment differs by dialect; filter in Python for SQLite + PG.
    recent = (
        ContractBootstrapSession.query.filter_by(organization_id=org_id)
        .order_by(ContractBootstrapSession.id.desc())
        .limit(80)
        .all()
    )
    return [
        s for s in recent
        if ((s.classification or {}).get('upload_batch_id') == batch_id)
    ]


_BATCH_DONE_STATUSES = frozenset({
    ContractBootstrapSession.STATUS_AWAITING_REVIEW,
    ContractBootstrapSession.STATUS_AWAITING_MATCH,
    ContractBootstrapSession.STATUS_APPLIED,
    ContractBootstrapSession.STATUS_APPROVED,
})


def batch_item_phase(status: str | None) -> str:
    """UI phase for one PDF in a multi-upload batch."""
    status = (status or '').strip().lower()
    if status == ContractBootstrapSession.STATUS_FAILED:
        return 'failed'
    if status in _BATCH_DONE_STATUSES:
        return 'identified'
    if status == ContractBootstrapSession.STATUS_PROCESSING:
        return 'reading'
    return 'queued'


def build_batch_status_payload(
    *,
    sessions: list[ContractBootstrapSession],
    batch_id: str,
) -> dict:
    """JSON-friendly progress for the live batch wait page."""
    items = []
    for session in sorted(sessions, key=lambda s: s.id or 0):
        identity = (session.classification or {}).get('document_identity') or {}
        phase = batch_item_phase(session.status)
        label = identity.get('label') or (
            'Identifying…' if phase == 'reading' else 'Waiting…'
        )
        items.append({
            'id': session.id,
            'filename': session.original_filename,
            'status': session.status,
            'phase': phase,
            'kind': identity.get('kind') or 'identifying',
            'label': label,
        })

    identified = sum(1 for i in items if i['phase'] == 'identified')
    failed = sum(1 for i in items if i['phase'] == 'failed')
    reading = sum(1 for i in items if i['phase'] == 'reading')
    ready = batch_sessions_ready(sessions)
    primary = select_primary_bootstrap_session(sessions) if ready else None
    return {
        'ok': True,
        'batch_id': batch_id,
        'ready': ready,
        'total': len(items),
        'identified_count': identified,
        'failed_count': failed,
        'reading_count': reading,
        'primary_session_id': primary.id if primary else None,
        'items': items,
    }


def process_inbox_upload(
    *,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    user: User,
    org_id: int,
    run_extraction: bool = True,
    confirmed_side: str | None = None,
    upload_batch_id: str | None = None,
) -> ContractBootstrapSession:
    """Full inbox upload path: metadata → store → extract → match discovery.

    ``confirmed_side`` is optional: when the agent has not said which side they
    are on, the form itself usually answers it (see representation_inference).
    """
    side = _normalize_side(confirmed_side)
    side_confirmed_by_user = side in ('buyer', 'seller')

    session = record_upload_metadata(
        file_bytes=file_bytes,
        filename=filename,
        mime_type=mime_type or 'application/pdf',
        source='inbox',
        user=user,
        org_id=org_id,
    )
    store_bootstrap_file(session=session, file_bytes=file_bytes)

    identity = classify_upload_identity(
        file_bytes=file_bytes,
        filename=filename,
    )

    inference = infer_side_for_upload(
        identity=identity,
        file_bytes=file_bytes,
        user=user,
    )
    if not side_confirmed_by_user and inference.is_confident:
        side = inference.side

    upload_classification = dict(session.classification or {})
    upload_classification.update({
        'side': side or None,
        'side_confirmed_by_user': side_confirmed_by_user,
        'side_inferred_from_document': (
            None if side_confirmed_by_user else inference.side
        ),
        'representation_inference': inference.to_dict(),
        'processing_started_at': datetime.utcnow().isoformat(),
        'document_identity': identity.to_dict(),
    })
    if upload_batch_id:
        upload_classification['upload_batch_id'] = upload_batch_id
    session.classification = upload_classification
    # Defer status to "uploaded" (queued) when extraction runs in the
    # background so the batch wait list can show Queued → Reading → Identified
    # one PDF at a time instead of "Reading…" on every row at once.
    session.status = ContractBootstrapSession.STATUS_UPLOADED
    flag_modified(session, 'classification')
    db.session.flush()

    if run_extraction:
        field_data = extract_contract_fields(
            file_bytes=file_bytes,
            identity=identity,
            filename=filename,
        )
        classify_and_extract(
            session=session,
            field_data=field_data,
            identity=identity,
        )
        run_match_discovery(session)
    db.session.commit()
    return session


# Serialize local SQLite bootstrap jobs. Multi-file inbox uploads otherwise
# stampede the same DB file and fail with "database is locked" / empty reads.
_bootstrap_local_lock = None
_bootstrap_local_queue: list[tuple[int, int, object]] = []
_bootstrap_local_worker_started = False


def _bootstrap_local_lock_obj():
    global _bootstrap_local_lock
    if _bootstrap_local_lock is None:
        import threading
        _bootstrap_local_lock = threading.Lock()
    return _bootstrap_local_lock


def _run_local_bootstrap_queue() -> None:
    """Drain serially so multi-upload jobs do not fight SQLite."""
    global _bootstrap_local_worker_started
    from jobs.contract_bootstrap import process_contract_bootstrap_job

    while True:
        with _bootstrap_local_lock_obj():
            if not _bootstrap_local_queue:
                _bootstrap_local_worker_started = False
                return
            session_id, org_id, app = _bootstrap_local_queue.pop(0)

        try:
            with app.app_context():
                process_contract_bootstrap_job(
                    session_id=session_id,
                    org_id=org_id,
                    _inline=True,
                )
        except Exception:
            logger.exception(
                'Local bootstrap worker failed for session %s',
                session_id,
            )


def enqueue_bootstrap_processing(*, session_id: int, org_id: int) -> None:
    """Queue contract extraction, with a local background-thread fallback."""
    from flask import current_app

    app = current_app._get_current_object()

    try:
        from config import Config
        from redis import Redis
        from rq import Queue

        if not Config.SQLALCHEMY_DATABASE_URI.startswith('sqlite'):
            conn = Redis.from_url(
                Config.REDIS_URL,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            conn.ping()
            Queue('contract_bootstrap', connection=conn).enqueue(
                'jobs.contract_bootstrap.process_contract_bootstrap_job',
                session_id=session_id,
                org_id=org_id,
                job_timeout=300,
            )
            return
    except Exception as exc:
        logger.warning(
            'Contract bootstrap queue unavailable for session %s: %s',
            session_id,
            exc,
        )

    import threading

    global _bootstrap_local_worker_started
    start_worker = False
    with _bootstrap_local_lock_obj():
        _bootstrap_local_queue.append((session_id, org_id, app))
        if not _bootstrap_local_worker_started:
            _bootstrap_local_worker_started = True
            start_worker = True

    if start_worker:
        threading.Thread(
            target=_run_local_bootstrap_queue,
            name='contract-bootstrap-worker',
            daemon=True,
        ).start()


def resolve_match(
    *,
    session: ContractBootstrapSession,
    decision: str,
    transaction_id: int | None = None,
    side: str | None = None,
) -> ContractBootstrapSession:
    """Resolve match decision for a bootstrap session. NEVER silently attach."""
    if decision not in ('attach', 'select', 'create_new', 'manual'):
        raise ValueError(f'Invalid decision: {decision}')

    if decision in ('attach', 'select'):
        if not transaction_id:
            raise ValueError(f'transaction_id required for decision={decision}')

        tx = Transaction.query.filter_by(
            id=transaction_id,
            organization_id=session.organization_id,
        ).first()
        if not tx:
            raise ValueError(
                f'Transaction {transaction_id} not found in org {session.organization_id}'
            )

        confirmed_side = _normalize_side((session.classification or {}).get('side'))
        transaction_side = (
            (tx.transaction_type.name or '').lower()
            if tx.transaction_type else 'unknown'
        )
        if (
            confirmed_side in ('buyer', 'seller')
            and transaction_side in ('buyer', 'seller')
            and transaction_side != confirmed_side
        ):
            raise ValueError(
                f'That is a {transaction_side} transaction, but you confirmed '
                f'{confirmed_side} representation. Choose another transaction or start a new one.'
            )

        session.matched_transaction_id = transaction_id
        session.match_status = ContractBootstrapSession.MATCH_MATCHED
        session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW

    elif decision == 'create_new':
        if not side:
            raise ValueError('side required for create_new decision')
        side_norm = side.strip().lower()
        if side_norm not in ('buyer', 'seller', 'landlord', 'tenant'):
            raise ValueError(f'Invalid side: {side}')

        # Phase 3: lease/tenant create requires privacy controls.
        if side_norm in ('landlord', 'tenant'):
            from models import Organization
            from services.document_privacy import lease_bootstrap_allowed
            org = Organization.query.get(session.organization_id)
            doc_type = (session.classification or {}).get('document_type')
            allowed, reason = lease_bootstrap_allowed(
                org=org, side=side_norm, document_type=doc_type,
            )
            if not allowed:
                raise ValueError(
                    'Lease/tenant bootstrap requires privacy controls '
                    f'(BOB_VTC_PILOT + BOB_VTC_PRIVACY_CONTROLS). ({reason})'
                )

        classification = dict(session.classification or {})
        classification['side'] = side_norm
        session.classification = classification
        flag_modified(session, 'classification')
        session.matched_transaction_id = None
        session.match_status = ContractBootstrapSession.MATCH_CREATE_NEW
        session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW

    elif decision == 'manual':
        session.match_status = ContractBootstrapSession.MATCH_MANUAL
        session.status = ContractBootstrapSession.STATUS_AWAITING_MATCH

    db.session.flush()

    logger.info(
        'ContractBootstrapSession %s resolved: decision=%s tx=%s match_status=%s',
        session.id, decision, transaction_id, session.match_status,
    )
    return session


def build_review_payload(
    *,
    session: ContractBootstrapSession,
) -> dict[str, Any]:
    """Build review payload for agent approval."""
    classification = dict(session.classification or {})
    # Never invent a representing side for the UI — leave unknown unselected.
    side_raw = _normalize_side(classification.get('side'))
    side_guess = side_raw if side_raw in _VALID_SIDES else None

    parties = build_party_proposals(session, session.uploader_user_id)
    bob_questions = _build_bob_questions(parties, classification)

    payload = {
        'session_id': session.id,
        'filename': session.original_filename,
        'classification': classification,
        'match_status': session.match_status,
        'matched_transaction_id': session.matched_transaction_id,
        'match_candidates': session.match_candidates or [],
        'status': session.status,
        'fields': [],
        'parties': parties,
        'bob_questions': bob_questions,
        'side_guess': side_guess,
    }

    crm_values = {}
    if session.matched_transaction_id:
        tx = Transaction.query.get(session.matched_transaction_id)
        if tx:
            extra = tx.extra_data or {}
            crm_values = {
                'street_address': tx.street_address,
                'expected_close_date': (
                    tx.expected_close_date.isoformat() if tx.expected_close_date else None
                ),
                'sales_price': extra.get('sales_price') or extra.get('purchase_price'),
                'status': tx.status,
            }
            payload['transaction'] = {
                'id': tx.id,
                'address': tx.street_address,
                'status': tx.status,
            }

    candidates = session.extracted_candidates or {}
    for key, data in candidates.items():
        if key in ('offer_price', 'purchase_price') and 'sales_price' in candidates:
            continue
        if key in ('proposed_close_date', 'close_date') and 'closing_date' in candidates:
            continue
        # Package-split metadata — not a deal term for the agent to check.
        if key in ('detected_documents', 'detected_document_types'):
            continue
        value = data.get('value')
        evidence = data.get('evidence', {})

        label, group, critical = _FIELD_PRESENTATION.get(
            key,
            (key.replace('_', ' ').title(), 'other', False),
        )
        confidence = evidence.get('confidence')
        try:
            confidence_number = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_number = None

        field = {
            'key': key,
            'label': label,
            'group': group,
            'critical': critical,
            'extracted_value': value,
            'page': evidence.get('page'),
            'confidence': confidence_number,
            'selected': True,
            'warning': None,
            'needs_confirmation': critical,
            'crm_value': None,
        }

        crm_key_map = {
            'property_address': 'street_address',
            'street_address': 'street_address',
            'closing_date': 'expected_close_date',
            'close_date': 'expected_close_date',
            'proposed_close_date': 'expected_close_date',
            'sales_price': 'sales_price',
            'purchase_price': 'sales_price',
            'offer_price': 'sales_price',
        }
        crm_key = crm_key_map.get(key)
        if crm_key and crm_key in crm_values:
            field['crm_value'] = crm_values[crm_key]
            if value and crm_values[crm_key] and str(value) != str(crm_values[crm_key]):
                field['warning'] = f'Differs from CRM: {crm_values[crm_key]}'

        low_confidence = (
            confidence_number is not None
            and confidence_number < _LOW_CONFIDENCE_THRESHOLD
        )
        if key in ('buyer_signature_present', 'seller_signature_present') and value is False:
            party_label = 'buyer' if key.startswith('buyer_') else 'seller'
            field['warning'] = (
                f'BOB could not confirm a {party_label} signature. Check the source PDF.'
            )
        if low_confidence:
            field['warning'] = field['warning'] or 'BOB was not confident about this value.'
        field['needs_confirmation'] = bool(
            critical or low_confidence or field['warning']
        )
        # Conflicts and explicit low-confidence observations must be opted in.
        field['selected'] = not bool(field['warning'])

        payload['fields'].append(field)

    # A transaction cannot be useful without an address. If extraction missed it,
    # give the agent a clear correction row instead of silently creating an
    # "Address pending review" record.
    address_keys = {'property_address', 'street_address'}
    if not any(field['key'] in address_keys for field in payload['fields']):
        fallback_address = classification.get('property_address') or ''
        payload['fields'].insert(0, {
            'key': 'property_address',
            'label': 'Property address',
            'group': 'deal',
            'critical': True,
            'extracted_value': fallback_address,
            'page': None,
            'confidence': None,
            'selected': bool(fallback_address),
            'warning': None if fallback_address else 'Enter the property address.',
            'needs_confirmation': True,
            'crm_value': None,
        })

    payload['attention_fields'] = [
        field for field in payload['fields'] if field.get('warning')
    ]
    payload['field_groups'] = {
        group: [field for field in payload['fields'] if field.get('group') == group]
        for group in ('deal', 'deadlines', 'people', 'other')
    }

    identity_data = classification.get('document_identity') or {}
    route_data = classification.get('route_decision') or {}
    payload['document_identity'] = identity_data
    payload['route_decision'] = route_data
    payload['destination_choice'] = classification.get('destination_choice')
    if route_data.get('needs_confirmation') and route_data.get('confirmation_options'):
        payload['destination_options'] = list(route_data.get('confirmation_options') or [])
        payload['destination_prompt'] = route_data.get('reason')
    elif (
        (identity_data.get('kind') == 'purchase_contract')
        and side_guess == 'buyer'
        and identity_data.get('execution_state') in (None, 'unknown', 'draft_proposed', 'party_signed')
        and not classification.get('destination_choice')
    ):
        payload['destination_options'] = ['offer_thread', 'controlling_contract']
        payload['destination_prompt'] = (
            'This buyer purchase contract is not clearly fully executed. '
            'Choose offer thread or controlling contract before applying.'
        )

    return payload


def _link_document_to_transaction(
    *,
    session: ContractBootstrapSession,
    transaction: Transaction,
) -> TransactionDocument | None:
    """Create a TransactionDocument from stored bootstrap bytes once a tx exists."""
    if session.document_id:
        doc = TransactionDocument.query.get(session.document_id)
        if doc and doc.transaction_id == transaction.id:
            return doc

    file_bytes = read_bootstrap_file(session)
    source_path = session.storage_path
    if file_bytes:
        try:
            from services.supabase_storage import upload_external_document
            uploaded = upload_external_document(
                transaction.id,
                file_bytes,
                session.original_filename or 'contract.pdf',
                session.mime_type or 'application/pdf',
            )
            source_path = uploaded.get('path') or source_path
        except Exception as e:
            logger.warning(
                'Supabase upload failed for bootstrap session %s; keeping local path: %s',
                session.id, e,
            )

    field_data = {
        key: data.get('value')
        for key, data in (session.extracted_candidates or {}).items()
    }

    classification = session.classification or {}
    side = (classification.get('side') or '').lower()
    doc_type = classification.get('document_type') or 'contract'
    identity_data = classification.get('document_identity') or {}
    route_data = classification.get('route_decision') or {}
    identity_kind = (identity_data.get('kind') or '').strip().lower()
    identity_slug = (identity_data.get('template_slug') or '').strip()
    identity_label = (identity_data.get('label') or '').strip()

    if route_data.get('template_slug'):
        template_slug = route_data['template_slug']
        template_name = route_data.get('template_name') or template_slug
    elif identity_slug and identity_kind not in ('purchase_contract', ''):
        # IABS, wire fraud, T-47, net proceeds, etc. — never "Offer Contract".
        template_slug = identity_slug
        template_name = identity_label or identity_slug
    elif identity_kind == 'listing_agreement':
        template_slug = 'listing-agreement'
        template_name = identity_label or 'Listing Agreement'
    elif side in ('landlord', 'tenant', 'lease'):
        template_slug = 'lease-document'
        template_name = 'Lease / Tenant Document'
    elif identity_kind == 'purchase_contract' and side == 'seller':
        # Inbound offer — never misfile as seller-accepted-contract.
        template_slug = 'seller-offer-contract'
        template_name = identity_label or 'Offer Contract'
    elif side == 'buyer' and identity_kind in ('purchase_contract', '', 'unknown'):
        contract_type = str(
            field_data.get('purchase_contract_type')
            or classification.get('purchase_contract_type')
            or identity_data.get('purchase_contract_type')
            or 'unknown'
        ).strip().lower()
        buyer_contracts = {
            'resale_one_to_four': (
                'one-to-four-family-contract',
                'One to Four Family Residential Contract',
            ),
            'condominium': (
                'condominium-contract',
                'Residential Condominium Contract',
            ),
            'new_construction_complete': (
                'new-home-completed-construction-contract',
                'New Home Contract - Completed Construction',
            ),
            'new_construction_incomplete': (
                'new-home-incomplete-construction-contract',
                'New Home Contract - Incomplete Construction',
            ),
            'farm_and_ranch': ('farm-and-ranch-contract', 'Farm and Ranch Contract'),
        }
        template_slug, template_name = buyer_contracts.get(
            contract_type,
            ('purchase-contract', identity_label or 'Purchase Contract'),
        )
    elif identity_slug:
        template_slug = identity_slug
        template_name = identity_label or identity_slug
    elif route_data.get('action') == 'attach_controlling_contract':
        template_slug = 'seller-accepted-contract'
        template_name = 'Executed Contract'
    else:
        template_slug = identity_slug or 'supporting-document'
        template_name = identity_label or 'Supporting Document'

    # Preserve package-split map even when it was not a reviewable field.
    detected = classification.get('detected_documents')
    if detected is not None and 'detected_documents' not in field_data:
        field_data = dict(field_data)
        field_data['detected_documents'] = detected

    if identity_data:
        from services.document_identity import (
            DocumentIdentity,
            persist_identity_on_field_data,
        )
        # Rewrite embeds from AI detected_documents before the doc is saved.
        field_data = persist_identity_on_field_data(
            field_data,
            DocumentIdentity.from_dict(identity_data),
        )
        if route_data:
            field_data = dict(field_data)
            field_data['_route_decision'] = route_data

    doc = TransactionDocument(
        organization_id=session.organization_id,
        transaction_id=transaction.id,
        template_slug=template_slug,
        template_name=template_name,
        status='signed',
        document_source='external',
        source_file_path=source_path,
        signed_original_filename=session.original_filename,
        signed_file_path=source_path,
        field_data=field_data,
        extraction_status='complete' if field_data else None,
        signing_method='physical',
    )
    from services.document_privacy import apply_sensitivity_to_document
    apply_sensitivity_to_document(
        document=doc,
        document_type=doc_type,
        transaction_side=side or None,
    )
    db.session.add(doc)
    db.session.flush()
    session.document_id = doc.id

    # Split a multi-document listing package into child PDFs (IABS, HOA, etc.)
    # and fulfill matching questionnaire placeholders in place.
    if (
        file_bytes
        and (
            template_slug == 'listing-agreement'
            or identity_kind == 'listing_agreement'
        )
        and isinstance((doc.field_data or {}).get('detected_documents'), list)
        and len((doc.field_data or {}).get('detected_documents') or []) >= 2
    ):
        try:
            from services.seller_workflow import split_listing_package_into_children
            split_listing_package_into_children(doc.id, file_bytes)
        except Exception:
            logger.exception(
                'Listing package split failed for bootstrap doc %s', doc.id,
            )

    if template_slug == 'listing-agreement' or identity_kind == 'listing_agreement':
        actor_id = session.uploader_user_id or session.applied_by_id
        if actor_id:
            try:
                from services.transaction_helpers import (
                    sync_seller_commission_terms_from_listing,
                )
                sync_seller_commission_terms_from_listing(
                    transaction=transaction,
                    field_data=doc.field_data or field_data,
                    user_id=actor_id,
                    org_id=session.organization_id,
                )
            except Exception:
                logger.exception(
                    'Failed to sync seller commission terms from listing bootstrap %s',
                    session.id,
                )

    return doc


def _attach_upload_batch_siblings(
    *,
    primary_session: ContractBootstrapSession,
    transaction: Transaction,
    user_id: int,
) -> list[TransactionDocument]:
    """Link other PDFs from the same inbox batch onto the approved transaction."""
    batch_id = (primary_session.classification or {}).get('upload_batch_id')
    if not batch_id:
        return []

    siblings = [
        s for s in sessions_for_upload_batch(
            org_id=primary_session.organization_id,
            batch_id=batch_id,
        )
        if s.id != primary_session.id
        and s.status not in (
            ContractBootstrapSession.STATUS_APPLIED,
            ContractBootstrapSession.STATUS_CANCELLED,
        )
    ]
    # Listing / purchase / amendment each need their own Review & Apply.
    # Only auto-file supporting forms (IABS, wire fraud, T-47, HOA, etc.).
    competing_kinds = {'listing_agreement', 'purchase_contract', 'amendment'}
    attached: list[TransactionDocument] = []
    for sibling in siblings:
        try:
            identity = (sibling.classification or {}).get('document_identity') or {}
            kind = (identity.get('kind') or '').strip().lower()
            if kind in competing_kinds:
                continue

            sibling.matched_transaction_id = transaction.id
            sibling.match_status = ContractBootstrapSession.MATCH_MATCHED
            doc = _link_document_to_transaction(
                session=sibling,
                transaction=transaction,
            )
            sibling.status = ContractBootstrapSession.STATUS_APPLIED
            sibling.applied_at = datetime.utcnow()
            sibling.applied_by_id = user_id
            classification = dict(sibling.classification or {})
            classification['attached_via_batch_primary'] = primary_session.id
            sibling.classification = classification
            flag_modified(sibling, 'classification')
            if doc:
                attached.append(doc)
        except Exception:
            logger.exception(
                'Failed to attach batch sibling session %s to tx %s',
                sibling.id,
                transaction.id,
            )
    return attached


def approve_selected(
    *,
    session: ContractBootstrapSession,
    user_id: int,
    selected_fields: dict[str, bool],
    corrections: dict[str, Any],
    confirmed_side: str | None = None,
    party_resolutions: list[dict] | None = None,
    destination_choice: str | None = None,
) -> tuple[Transaction, TransactionChangeProposal]:
    """Apply approved fields to create or update transaction."""
    from services.document_identity import DocumentIdentity
    from services.document_routing import (
        ACTION_ATTACH_CONTROLLING_CONTRACT,
        ACTION_ATTACH_LISTING_DOC,
        ACTION_CREATE_AMENDMENT,
        ACTION_CREATE_BUYER_OFFER,
        ACTION_CREATE_INBOUND_OFFER,
        ACTION_CREATE_LISTING,
        ACTION_INVALID,
        ACTION_NEEDS_CONFIRMATION,
        decide_route,
    )

    if session.match_status not in (
        ContractBootstrapSession.MATCH_MATCHED,
        ContractBootstrapSession.MATCH_CREATE_NEW,
    ):
        raise ValueError(
            f'Cannot approve session {session.id}: match not resolved (status={session.match_status})'
        )

    if session.status not in (
        ContractBootstrapSession.STATUS_AWAITING_REVIEW,
        ContractBootstrapSession.STATUS_APPROVED,
    ):
        raise ValueError(
            f'Cannot approve session {session.id}: invalid status {session.status}'
        )

    existing_classification = dict(session.classification or {})
    existing_side = _normalize_side(existing_classification.get('side'))
    side_already_known = (
        existing_classification.get('side_confirmed_by_user')
        or bool(existing_classification.get('side_inferred_from_document'))
        or existing_side in ('buyer', 'seller')
    )
    if not side_already_known and not confirmed_side:
        raise ValueError('Choose which side you represent: buyer or seller.')

    candidates = session.extracted_candidates or {}
    applied_fields = {}
    allowed_field_keys = set(candidates)
    allowed_field_keys.add('property_address')
    for key, selected in selected_fields.items():
        if not selected or key not in allowed_field_keys:
            continue
        if key in corrections:
            applied_fields[key] = corrections[key]
        elif key in candidates:
            applied_fields[key] = candidates[key].get('value')

    # The correction row can be synthetic when extraction found no address.
    # Keep corrections explicit and scoped to known review fields.
    if (
        selected_fields.get('property_address')
        and 'property_address' in corrections
    ):
        applied_fields['property_address'] = corrections['property_address']

    for key in list(applied_fields):
        if key in _DATE_FIELD_KEYS or str(key).endswith('_date'):
            applied_fields[key] = _normalize_date_value(applied_fields[key])

    if confirmed_side is not None and str(confirmed_side).strip():
        side_norm = str(confirmed_side).strip().lower()
        if side_norm not in _VALID_SIDES:
            raise ValueError(
                f'Invalid side: {confirmed_side}. Choose buyer, seller, landlord, or tenant.'
            )
        classification = dict(session.classification or {})
        classification['side'] = side_norm
        classification['side_confirmed_by_user'] = True
        session.classification = classification
        flag_modified(session, 'classification')

    if destination_choice:
        classification = dict(session.classification or {})
        classification['destination_choice'] = str(destination_choice).strip().lower()
        session.classification = classification
        flag_modified(session, 'classification')

    classification = dict(session.classification or {})
    identity = DocumentIdentity.from_dict(classification.get('document_identity'))
    side_for_route = _normalize_side(
        classification.get('side')
        or confirmed_side
    )
    tx_context = _build_transaction_context_for_session(
        session=session,
        side_hint=side_for_route,
    )
    route = decide_route(
        identity=identity,
        representation_side=side_for_route,
        side_confirmed=True,
        transaction=tx_context,
        destination_choice=classification.get('destination_choice'),
    )
    # Legacy sessions without identity: keep prior buyer/seller under-contract path.
    if not classification.get('document_identity') and identity.kind == 'unknown':
        from services.document_routing import (
            ACTION_ATTACH_CONTROLLING_CONTRACT,
            RouteDecision,
        )
        route = RouteDecision(
            action=ACTION_ATTACH_CONTROLLING_CONTRACT,
            destination_scope='contract',
            template_slug=(
                'purchase-contract' if side_for_route == 'buyer' else 'seller-accepted-contract'
            ),
            template_name='Executed Contract',
            transaction_status='under_contract',
            seed_pack_key='buyer_ctc' if side_for_route == 'buyer' else 'seller_ctc',
            reason='Legacy bootstrap session without document identity.',
        )

    if route.action == ACTION_INVALID:
        raise ValueError(route.reason or 'This document cannot be applied for the selected side.')
    if route.action == ACTION_NEEDS_CONFIRMATION:
        raise ValueError(
            route.reason
            or 'Confirm how this document should be filed before applying.'
        )

    classification['route_decision'] = route.to_dict()
    session.classification = classification
    flag_modified(session, 'classification')

    if session.match_status == ContractBootstrapSession.MATCH_CREATE_NEW:
        side_for_create = side_for_route
        if side_for_create not in _VALID_SIDES:
            raise ValueError(
                'Which side are you representing? Confirm seller or buyer before '
                'creating a transaction.'
            )
        classification = dict(session.classification or {})
        classification['side'] = side_for_create
        session.classification = classification
        flag_modified(session, 'classification')

        reviewed_address = (
            applied_fields.get('property_address')
            or applied_fields.get('street_address')
        )
        if not str(reviewed_address or '').strip():
            raise ValueError(
                'Enter and select the property address before creating the transaction.'
            )

        transaction = _create_transaction_from_bootstrap(
            session=session,
            applied_fields=applied_fields,
            user_id=user_id,
            route=route,
        )
        session.matched_transaction_id = transaction.id
        if not party_resolutions:
            party_resolutions = default_party_resolutions(session, user_id)
        _apply_party_resolutions(
            transaction=transaction,
            org_id=session.organization_id,
            user_id=user_id,
            party_resolutions=party_resolutions,
            only_missing_roles=False,
        )
    else:
        transaction = Transaction.query.get(session.matched_transaction_id)
        if not transaction:
            raise ValueError(f'Transaction {session.matched_transaction_id} not found')
        if transaction.organization_id != session.organization_id:
            raise ValueError(f'Transaction {session.matched_transaction_id} not found')
        _apply_fields_to_transaction(
            transaction,
            applied_fields,
            route=route,
        )
        _apply_party_resolutions(
            transaction=transaction,
            org_id=session.organization_id,
            user_id=user_id,
            party_resolutions=party_resolutions,
            only_missing_roles=True,
        )
        # Matched path: seed listing/CTC packs when the route establishes a baseline.
        if route.seed_pack_key:
            try:
                _seed_requirements_for_transaction(
                    transaction=transaction,
                    applied_fields=applied_fields,
                    pack_key_hint=route.seed_pack_key,
                )
            except Exception:
                logger.exception(
                    'Failed to seed requirements for matched transaction %s',
                    transaction.id,
                )

    doc = _link_document_to_transaction(session=session, transaction=transaction)

    offer = None
    if route.action in (ACTION_CREATE_INBOUND_OFFER, ACTION_CREATE_BUYER_OFFER) and doc:
        offer = _attach_bootstrap_document_as_offer(
            session=session,
            transaction=transaction,
            document=doc,
            applied_fields=applied_fields,
            user_id=user_id,
            route=route,
        )

    controlling_contract = None
    # Buyer/seller controlling baseline: approved fields only, never offer_thread.
    if route.action == ACTION_ATTACH_CONTROLLING_CONTRACT and doc:
        from services.controlling_contracts import create_baseline_from_document
        try:
            controlling_contract = create_baseline_from_document(
                transaction=transaction,
                document=doc,
                approved_terms=applied_fields,
                actor_id=user_id,
                offer_id=None,
                position='primary',
                seed_requirements=True,
            )
        except Exception:
            logger.exception(
                'Failed to create controlling baseline for bootstrap session %s',
                session.id,
            )
            raise

    change_type = 'bootstrap_contract_extracted_fields'
    if route.destination_scope == 'listing':
        change_type = 'bootstrap_listing_extracted_fields'
    elif route.action in (ACTION_CREATE_INBOUND_OFFER, ACTION_CREATE_BUYER_OFFER):
        change_type = 'bootstrap_offer_extracted_fields'

    proposal = ProposalService.create_proposal(
        transaction_id=transaction.id,
        organization_id=session.organization_id,
        change_type=change_type,
        proposed_changes=applied_fields,
        rationale=f'Applied fields from uploaded document: {session.original_filename}',
        source_document_id=doc.id if doc else session.document_id,
    )

    ProposalService.approve_proposal(proposal.id, user_id)
    # Offer / listing routes store observations on the document; avoid forcing
    # under-contract proposal apply side effects for non-controlling docs.
    if route.action not in (
        ACTION_CREATE_INBOUND_OFFER,
        ACTION_CREATE_BUYER_OFFER,
        ACTION_CREATE_AMENDMENT,
        ACTION_ATTACH_LISTING_DOC,
        ACTION_CREATE_LISTING,
    ):
        ProposalService.apply_proposal(proposal.id, user_id)

    session.proposal_id = proposal.id
    session.status = ContractBootstrapSession.STATUS_APPLIED
    session.applied_at = datetime.utcnow()
    session.applied_by_id = user_id

    # Multi-PDF inbox: attach sibling forms to the same transaction once the
    # package-defining document is approved.
    sibling_docs = _attach_upload_batch_siblings(
        primary_session=session,
        transaction=transaction,
        user_id=user_id,
    )

    classification = dict(session.classification or {})
    reviewed_keys = {
        field['key']
        for field in build_review_payload(session=session).get('fields', [])
    }
    classification['review_summary'] = {
        'applied_field_count': len(applied_fields),
        'unapplied_field_count': sum(
            1 for key in reviewed_keys if key not in applied_fields
        ),
        'requirement_count': TransactionRequirement.query.filter_by(
            transaction_id=transaction.id,
            organization_id=session.organization_id,
        ).count(),
        'document_count': TransactionDocument.query.filter_by(
            transaction_id=transaction.id,
            organization_id=session.organization_id,
        ).count(),
        'offer_id': offer.id if offer else None,
        'accepted_contract_id': (
            controlling_contract.id if controlling_contract else None
        ),
        'route_action': route.action,
        'batch_sibling_document_ids': [d.id for d in sibling_docs if d],
    }
    session.classification = classification
    flag_modified(session, 'classification')
    db.session.flush()

    AuditEvent.log(
        event_type='contract_bootstrap_applied',
        organization_id=session.organization_id,
        transaction_id=transaction.id,
        document_id=session.document_id,
        actor_id=user_id,
        description=f'Applied contract bootstrap session {session.id}',
        event_data={
            'session_id': session.id,
            'filename': session.original_filename,
            'applied_fields': list(applied_fields.keys()),
            'proposal_id': proposal.id,
            'route_action': route.action,
            'identity_kind': identity.kind,
            'offer_id': offer.id if offer else None,
            'accepted_contract_id': (
                controlling_contract.id if controlling_contract else None
            ),
        },
        source='contract_bootstrap',
    )

    logger.info(
        'ContractBootstrapSession %s applied: tx=%s fields=%d proposal=%s route=%s',
        session.id, transaction.id, len(applied_fields), proposal.id, route.action,
    )
    return transaction, proposal


def _build_transaction_context_for_session(
    *,
    session: ContractBootstrapSession,
    side_hint: str | None = None,
):
    """Load real matched-transaction facts for routing (never defaults when matched)."""
    from services.document_routing import TransactionContext
    from models import SellerAcceptedContract, SellerOffer

    tx_id = session.matched_transaction_id
    if not tx_id:
        return TransactionContext(side=side_hint)

    tx = Transaction.query.filter_by(
        id=tx_id,
        organization_id=session.organization_id,
    ).first()
    if not tx:
        return TransactionContext(side=side_hint)

    side = side_hint
    if tx.transaction_type and tx.transaction_type.name:
        side = (tx.transaction_type.name or side_hint or '').lower()

    from services.controlling_contracts import has_active_primary_contract
    has_primary = has_active_primary_contract(tx.id, session.organization_id)

    has_listing = TransactionDocument.query.filter_by(
        transaction_id=tx.id,
        organization_id=session.organization_id,
        template_slug='listing-agreement',
    ).first() is not None

    active_offers = SellerOffer.query.filter_by(
        transaction_id=tx.id,
        organization_id=session.organization_id,
    ).filter(
        SellerOffer.status.notin_(('withdrawn', 'expired', 'rejected')),
    ).order_by(SellerOffer.id.desc()).limit(20).all()

    return TransactionContext(
        transaction_id=tx.id,
        side=side,
        status=tx.status,
        has_primary_contract=has_primary,
        has_listing_agreement=has_listing,
        active_offer_ids=tuple(o.id for o in active_offers),
    )


def _apply_fields_to_transaction(
    transaction: Transaction,
    applied_fields: dict[str, Any],
    route=None,
) -> None:
    """Best-effort canonical updates for matched transactions (approved path only)."""
    address = applied_fields.get('property_address') or applied_fields.get('street_address')
    if address:
        transaction.street_address = str(address)[:200]

    close_raw = (
        applied_fields.get('closing_date')
        or applied_fields.get('close_date')
        or applied_fields.get('proposed_close_date')
    )
    close_date = _parse_date(close_raw)
    # Listing agreements and inbound offers must not overwrite close / force CTC.
    route_action = getattr(route, 'action', None) if route is not None else None
    listing_or_offer = route_action in (
        'create_or_match_listing',
        'attach_listing_document',
        'create_or_attach_inbound_offer',
        'create_or_attach_buyer_offer',
    )
    if close_date and not listing_or_offer:
        transaction.expected_close_date = close_date

    price = (
        applied_fields.get('sales_price')
        or applied_fields.get('purchase_price')
        or applied_fields.get('offer_price')
        or applied_fields.get('list_price')
    )
    # Inbound/buyer offer threads must not overwrite transaction-level pricing.
    if price is not None and price != '' and not listing_or_offer:
        extra = dict(transaction.extra_data or {})
        if route and getattr(route, 'destination_scope', None) == 'listing':
            extra['list_price'] = price
        else:
            extra['sales_price'] = price
        transaction.extra_data = extra
        flag_modified(transaction, 'extra_data')
    elif (
        price is not None
        and price != ''
        and route
        and getattr(route, 'destination_scope', None) == 'listing'
    ):
        extra = dict(transaction.extra_data or {})
        extra['list_price'] = price
        transaction.extra_data = extra
        flag_modified(transaction, 'extra_data')

    if route is not None and getattr(route, 'transaction_status', None):
        # Only advance into under_contract when the route says so.
        if route.transaction_status == 'under_contract':
            if transaction.status in ('preparing_to_list', 'showing', 'active'):
                transaction.status = 'under_contract'
        elif (
            route.transaction_status == 'preparing_to_list'
            and transaction.status in (None, '', 'preparing_to_list')
        ):
            transaction.status = 'preparing_to_list'
    elif not listing_or_offer and transaction.status in (
        'preparing_to_list', 'showing', 'active',
    ):
        # Legacy path without route metadata.
        transaction.status = 'under_contract'

    db.session.flush()


def _get_or_create_tx_type(org_id: int, type_name: str) -> TransactionType:
    tx_type = TransactionType.query.filter_by(
        organization_id=org_id,
        name=type_name,
    ).first()
    if tx_type:
        return tx_type
    display = {
        'seller': 'Seller Representation',
        'buyer': 'Buyer Representation',
        'landlord': 'Landlord / Lease Listing',
        'tenant': 'Tenant Representation',
    }.get(type_name, type_name.title())
    tx_type = TransactionType(
        organization_id=org_id,
        name=type_name,
        display_name=display,
        is_active=True,
    )
    db.session.add(tx_type)
    db.session.flush()
    return tx_type


def _apply_party_resolutions(
    *,
    transaction: Transaction,
    org_id: int,
    user_id: int,
    party_resolutions: list[dict] | None,
    only_missing_roles: bool = False,
) -> None:
    """Apply per-person link/create/skip resolutions as TransactionParticipants."""
    if not party_resolutions:
        return

    existing_roles_linked: set[str] = set()
    if only_missing_roles:
        for p in TransactionParticipant.query.filter_by(
            transaction_id=transaction.id,
            organization_id=org_id,
        ).all():
            if p.contact_id and p.role:
                existing_roles_linked.add(p.role)

    for resolution in party_resolutions:
        if not isinstance(resolution, dict):
            continue
        action = (resolution.get('action') or '').strip().lower()
        role = (resolution.get('role') or '').strip().lower()
        if not role:
            continue
        if action == 'skip':
            continue
        if only_missing_roles and role in existing_roles_linked:
            continue

        contact = None
        display_name = (resolution.get('full_name') or '').strip()

        if action == 'keep':
            if not display_name:
                raise ValueError(
                    f'Name required to keep party {resolution.get("party_key") or role} on the transaction.'
                )

        elif action == 'link':
            contact_id = resolution.get('contact_id')
            if not contact_id:
                raise ValueError(f'contact_id required to link party {resolution.get("party_key")}')
            contact = Contact.query.filter_by(
                id=int(contact_id),
                organization_id=org_id,
            ).first()
            if not contact:
                raise ValueError(f'Contact {contact_id} not found in organization')
            display_name = f'{contact.first_name} {contact.last_name}'.strip()

        elif action == 'create':
            first_name = (resolution.get('first_name') or '').strip()
            last_name = (resolution.get('last_name') or '').strip()
            if (not first_name or not last_name) and display_name:
                parsed = parse_person_name(display_name)
                first_name = first_name or parsed['first_name']
                last_name = last_name or parsed['last_name']
            if not first_name:
                raise ValueError(
                    f'A name is required to create a contact for '
                    f'{resolution.get("party_key") or display_name or "this person"}'
                )
            email = (resolution.get('email') or '').strip().lower() or None
            if email and '@' not in email:
                email = None
            from utils import format_phone_number
            phone = format_phone_number(resolution.get('phone')) if resolution.get('phone') else None
            existing = _find_contact_by_email(org_id, email)
            if existing:
                contact = existing
                display_name = f'{contact.first_name} {contact.last_name}'.strip()
            else:
                contact = Contact(
                    organization_id=org_id,
                    user_id=user_id,
                    created_by_id=user_id,
                    first_name=first_name[:80],
                    last_name=(last_name or '')[:80],
                    email=email[:120] if email else None,
                    phone=phone,
                    street_address=((resolution.get('street_address') or '').strip() or None),
                    city=((resolution.get('city') or '').strip() or None),
                    state=((resolution.get('state') or '').strip() or None),
                    zip_code=((resolution.get('zip_code') or '').strip() or None),
                )
                if contact.street_address:
                    contact.street_address = contact.street_address[:200]
                if contact.city:
                    contact.city = contact.city[:100]
                if contact.state:
                    contact.state = contact.state[:50]
                if contact.zip_code:
                    contact.zip_code = contact.zip_code[:20]
                group = (
                    ContactGroup.query.filter_by(
                        organization_id=org_id,
                        user_id=user_id,
                        is_active=True,
                    )
                    .order_by(ContactGroup.sort_order, ContactGroup.id)
                    .first()
                )
                db.session.add(contact)
                db.session.flush()
                if group:
                    contact.groups.append(group)
                display_name = f'{first_name} {last_name}'.strip() or first_name
        else:
            raise ValueError(
                f'Invalid party action "{action}" for {resolution.get("party_key")}. '
                'Use keep, link, create, or skip.'
            )

        is_primary = role in ('seller', 'buyer', 'landlord', 'tenant')
        db.session.add(TransactionParticipant(
            organization_id=org_id,
            transaction_id=transaction.id,
            contact_id=contact.id if contact else None,
            role=role,
            name=(display_name or None) and display_name[:200],
            is_primary=is_primary,
        ))
        if contact:
            existing_roles_linked.add(role)

    db.session.flush()


def _create_transaction_from_bootstrap(
    *,
    session: ContractBootstrapSession,
    applied_fields: dict[str, Any],
    user_id: int,
    route=None,
) -> Transaction:
    """Create new transaction from bootstrap session."""
    classification = session.classification or {}
    side = _normalize_side(classification.get('side'))
    if side not in _VALID_SIDES:
        raise ValueError(
            'Which side are you representing? Confirm seller or buyer before '
            'creating a transaction.'
        )

    if side in ('landlord', 'tenant'):
        from models import Organization
        from services.document_privacy import lease_bootstrap_allowed
        org = Organization.query.get(session.organization_id)
        allowed, reason = lease_bootstrap_allowed(
            org=org,
            side=side,
            document_type=classification.get('document_type'),
        )
        if not allowed:
            raise ValueError(
                'Cannot create lease/tenant transaction without privacy controls '
                f'({reason})'
            )

    tx_type = _get_or_create_tx_type(session.organization_id, side)

    address = (
        applied_fields.get('property_address')
        or applied_fields.get('street_address')
    )
    if not str(address or '').strip():
        raise ValueError(
            'Enter and select the property address before creating the transaction.'
        )
    close_date = _parse_date(
        applied_fields.get('closing_date')
        or applied_fields.get('close_date')
        or applied_fields.get('proposed_close_date')
    )
    price = (
        applied_fields.get('sales_price')
        or applied_fields.get('purchase_price')
        or applied_fields.get('offer_price')
        or applied_fields.get('list_price')
    )

    route_action = getattr(route, 'action', None) if route is not None else None
    default_status = (
        getattr(route, 'transaction_status', None)
        if route is not None and getattr(route, 'transaction_status', None)
        else 'under_contract'
    )
    # Seller inbound offers create/match a listing shell, not under-contract.
    if route_action == 'create_or_attach_inbound_offer':
        default_status = 'active'
        close_date = None
    elif route_action in ('create_or_match_listing', 'attach_listing_document'):
        default_status = 'preparing_to_list'
        close_date = None
    elif route_action == 'create_or_attach_buyer_offer':
        default_status = 'showing'
        close_date = None

    extra = {}
    if price is not None and price != '':
        if getattr(route, 'destination_scope', None) == 'listing':
            extra['list_price'] = price
        else:
            extra['sales_price'] = price

    transaction = Transaction(
        organization_id=session.organization_id,
        transaction_type_id=tx_type.id,
        street_address=str(address)[:200],
        city=applied_fields.get('city'),
        state=applied_fields.get('state') or 'TX',
        zip_code=applied_fields.get('zip_code'),
        expected_close_date=close_date,
        status=default_status,
        created_by_id=user_id,
        extra_data=extra,
    )
    db.session.add(transaction)
    db.session.flush()

    assignment = TransactionAssignment(
        organization_id=session.organization_id,
        transaction_id=transaction.id,
        user_id=user_id,
        role='lead_agent',
    )
    db.session.add(assignment)
    db.session.flush()

    # Seed deadline packs only for controlling / listing baselines — not bare offers.
    should_seed = route_action in (
        None,
        'attach_controlling_contract',
        'create_or_match_listing',
        'attach_listing_document',
    )
    if should_seed:
        try:
            _seed_requirements_for_transaction(
                transaction=transaction,
                applied_fields=applied_fields,
                pack_key_hint=getattr(route, 'seed_pack_key', None) if route else None,
            )
        except Exception as e:
            logger.exception(
                'Failed to seed requirements for transaction %s: %s', transaction.id, e,
            )

    logger.info(
        'Created transaction %s from bootstrap session %s: address=%s side=%s status=%s',
        transaction.id, session.id, address, side, default_status,
    )
    return transaction


def _attach_bootstrap_document_as_offer(
    *,
    session: ContractBootstrapSession,
    transaction: Transaction,
    document: TransactionDocument,
    applied_fields: dict[str, Any],
    user_id: int,
    route=None,
):
    """Create an inbound/outbound offer thread from a bootstrap purchase contract."""
    from models import SellerOffer, SellerOfferDocument, SellerOfferVersion
    from services.offer_side import opening_direction_for_side, side_for_transaction
    from services.seller_workflow import create_offer_activity, get_offer_document_type

    # Idempotent: if this document is already on an offer, return that offer.
    existing_link = SellerOfferDocument.query.filter_by(
        organization_id=session.organization_id,
        transaction_id=transaction.id,
        transaction_document_id=document.id,
    ).first()
    if existing_link:
        return SellerOffer.query.filter_by(
            id=existing_link.offer_id,
            organization_id=session.organization_id,
        ).first()

    side = side_for_transaction(transaction) or _normalize_side(
        (session.classification or {}).get('side')
    )
    direction = opening_direction_for_side(side) if side in ('buyer', 'seller') else 'buyer_offer'
    doc_config = get_offer_document_type('buyer_offer')

    offer = SellerOffer(
        organization_id=session.organization_id,
        transaction_id=transaction.id,
        created_by_id=user_id,
        buyer_names=applied_fields.get('buyer_name') or applied_fields.get('buyer_names'),
        received_at=datetime.utcnow(),
        creation_source='bootstrap',
        status='needs_review',
        offer_price=(
            applied_fields.get('offer_price')
            or applied_fields.get('sales_price')
            or applied_fields.get('purchase_price')
        ),
        proposed_close_date=_parse_date(
            applied_fields.get('proposed_close_date')
            or applied_fields.get('closing_date')
        ),
        option_period_days=applied_fields.get('option_period_days'),
        financing_type=applied_fields.get('financing_type'),
        earnest_money=applied_fields.get('earnest_money'),
        option_fee=applied_fields.get('option_fee'),
    )
    db.session.add(offer)
    db.session.flush()

    version = SellerOfferVersion(
        organization_id=session.organization_id,
        transaction_id=transaction.id,
        offer_id=offer.id,
        created_by_id=user_id,
        transaction_document_id=document.id,
        version_number=1,
        direction=doc_config.get('direction') or direction,
        status='submitted',
        submitted_at=datetime.utcnow(),
        terms_data=dict(applied_fields),
    )
    db.session.add(version)
    db.session.flush()
    offer.current_version_id = version.id

    offer_document = SellerOfferDocument(
        organization_id=session.organization_id,
        transaction_id=transaction.id,
        offer_id=offer.id,
        transaction_document_id=document.id,
        offer_version_id=version.id,
        created_by_id=user_id,
        document_type='buyer_offer',
        display_name=(
            getattr(route, 'template_name', None)
            or document.template_name
            or 'Offer Contract'
        ),
        is_primary_terms_document=True,
        extraction_summary={},
    )
    db.session.add(offer_document)
    db.session.flush()

    # Ensure document slug stays offer-scoped (never accepted-contract).
    if document.template_slug in ('seller-accepted-contract', 'completed', 'external'):
        document.template_slug = 'seller-offer-contract'
        document.template_name = document.template_name or 'Offer Contract'

    create_offer_activity(
        offer,
        'document_uploaded',
        'Offer created from uploaded contract',
        actor_id=user_id,
        version_id=version.id,
        document_id=document.id,
        event_data={
            'source': 'bootstrap',
            'session_id': session.id,
            'route_action': getattr(route, 'action', None),
        },
    )
    db.session.flush()

    classification = dict(session.classification or {})
    classification['offer_id'] = offer.id
    session.classification = classification
    flag_modified(session, 'classification')
    return offer


def _seed_requirements_for_transaction(
    *,
    transaction: Transaction,
    applied_fields: dict[str, Any],
    pack_key_hint: str | None = None,
) -> None:
    """Seed requirements from deadline pack when anchor dates exist."""
    anchors: dict[str, date] = {}
    for field_key, anchor_key in (
        ('effective_date', 'effective_date'),
        ('closing_date', 'closing_date'),
        ('close_date', 'closing_date'),
        ('proposed_close_date', 'closing_date'),
        ('option_period_end', 'option_period_end'),
        ('listing_start_date', 'listing_start'),
        ('listing_start', 'listing_start'),
        ('lease_start_date', 'lease_start_date'),
        ('application_opened_date', 'application_opened_date'),
    ):
        parsed = _parse_date(applied_fields.get(field_key))
        if parsed:
            anchors[anchor_key] = parsed
            if anchor_key == 'listing_start':
                anchors['listing_start_date'] = parsed

    if 'option_period_end' not in anchors and anchors.get('effective_date'):
        try:
            option_days = int(applied_fields.get('option_period_days'))
        except (TypeError, ValueError):
            option_days = None
        if option_days is not None:
            from datetime import timedelta

            anchors['option_period_end'] = anchors['effective_date'] + timedelta(days=option_days)

    type_name = (
        transaction.transaction_type.name
        if transaction.transaction_type else 'seller'
    ).lower()

    # Lease/tenant: seed with today anchors when dates are absent.
    if type_name in ('landlord', 'tenant', 'lease') and not anchors:
        today = date.today()
        if type_name == 'landlord':
            anchors['listing_start_date'] = today
            anchors['lease_start_date'] = today
        else:
            anchors['application_opened_date'] = today

    # Listing pack can use listing_start; if missing, use today so prep requirements appear.
    pack_hint = (pack_key_hint or '').strip().lower() or None
    if pack_hint in ('listing', 'listing_v1') and 'listing_start' not in anchors:
        anchors['listing_start'] = date.today()
        anchors['listing_start_date'] = anchors['listing_start']

    if not anchors:
        logger.info('No date inputs for deadline rules, skipping requirement seeding')
        return

    if pack_hint in ('listing', 'listing_v1'):
        pack_key = 'listing'
    elif pack_hint in ('buyer_ctc', 'buyer_ctc_v1'):
        pack_key = 'buyer_ctc'
    elif pack_hint in ('seller_ctc', 'seller_ctc_v1'):
        pack_key = 'seller_ctc'
    else:
        try:
            pack_key, _pack = DeadlineRulesService.resolve_pack_for_transaction(
                transaction, side_hint=type_name,
            )
        except FileNotFoundError:
            logger.info('No deadline pack available for %s', type_name)
            return

    result = DeadlineRulesService.apply_pack_to_transaction(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
        pack_key=pack_key,
        anchors=anchors,
        side=type_name,
        source='deadline_pack',
    )

    logger.info(
        'Seeded %d requirements for transaction %s from deadline pack %s (skipped=%s)',
        result.get('created', 0), transaction.id, pack_key, result.get('skipped', 0),
    )


# Supporting-doc change_type by document kind (Phase 1B Review and Apply).
_SUPPORTING_KIND_RULES: list[tuple[tuple[str, ...], str]] = [
    (('amendment',), 'amendment'),
    (('earnest', 'earnest_receipt', 'earnest-money'), 'earnest_receipt'),
    (('inspection', 'inspection_report'), 'inspection'),
    (('appraisal',), 'appraisal'),
    (('title', 'title_commitment', 'commitment'), 'title'),
    (('clear_to_close', 'clear-to-close', 'ctc', 'clear to close'), 'clear_to_close'),
    (('settlement', 'closing_disclosure', 'cd ', 'hud-1'), 'settlement'),
    (('termination', 'release', 'terminate'), 'termination'),
    (('cda', 'commission_disbursement', 'cda_track'), 'cda_track'),
]


def supporting_document_change_type(
    *,
    document: TransactionDocument,
    field_data: dict[str, Any] | None = None,
) -> str:
    """Resolve change_type for a supporting document from slug / type heuristics."""
    field_data = field_data or {}
    slug = (document.template_slug or '').lower()
    name = (document.template_name or '').lower()
    doc_type = str(
        field_data.get('document_type')
        or field_data.get('detected_document_types')
        or ''
    ).lower()
    haystack = f'{slug} {name} {doc_type}'

    for needles, change_type in _SUPPORTING_KIND_RULES:
        if any(n in haystack for n in needles):
            return change_type

    # Primary executed contract / offer package
    if any(
        token in slug
        for token in (
            'accepted-contract',
            'seller-offer',
            'buyer-offer',
            'residential-contract',
        )
    ):
        return 'extracted_contract_fields'

    return 'supporting_document_other'


def propose_supporting_document_updates(
    *,
    document: TransactionDocument,
    field_data: dict[str, Any],
    extraction_run_id: int | None = None,
    supersede_pending: bool = True,
) -> TransactionChangeProposal | None:
    """
    Create a pending TransactionChangeProposal for extracted supporting-doc
    (or contract) fields. Does not apply — agent Review and Apply only.
    """
    non_proposable_fields = {
        '_meta',
        'document_classification',
        'document_title',
        'document_summary',
        'document_type',
        'form_id',
        'form_revision_date',
        'authoritative_deadlines',
        'sanity_flags',
        'unreadable_pages',
        'detected_document_types',
        'detected_documents',
        'buyer_signature_detected',
        'seller_signature_detected',
        'buyer_signature_present',
        'seller_signature_present',
    }
    proposed_fields = {
        key: value for key, value in (field_data or {}).items()
        if key not in non_proposable_fields and value not in (None, '', [], {})
    }
    if not proposed_fields:
        return None
    if not document.transaction_id:
        return None

    change_type = supporting_document_change_type(
        document=document, field_data=proposed_fields,
    )

    if supersede_pending:
        prior = TransactionChangeProposal.query.filter_by(
            organization_id=document.organization_id,
            transaction_id=document.transaction_id,
            source_document_id=document.id,
            status='pending',
        ).all()
        for p in prior:
            p.status = 'superseded'

    proposal = ProposalService.create_proposal(
        transaction_id=document.transaction_id,
        organization_id=document.organization_id,
        change_type=change_type,
        proposed_changes=proposed_fields,
        rationale=(
            f'Extracted fields from {document.template_name or document.template_slug} '
            f'awaiting Review and Apply'
        ),
        source_extraction_run_id=extraction_run_id,
        source_document_id=document.id,
        target_model='Transaction',
    )
    logger.info(
        'Created %s proposal %s for doc %s (%d fields)',
        change_type, proposal.id, document.id, len(proposed_fields),
    )
    return proposal
