"""MLS public remarks drafting for seller listing prep."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional


LISTING_AGREEMENT_SLUGS = frozenset({
    'listing-agreement',
    'listing_agreement',
})
SKIP_SUPPORTING_SLUGS = frozenset({
    'iabs',
    'information-about-brokerage-services',
    'compensation-agreement',
    'compensation-agreement-between-brokers',
})
SKIP_FACT_KEYS = frozenset({
    'detected_documents',
    'detected_document_types',
    'ai_detected_documents',
    'package_authority',
    'total_commission',
    'total_commission_display',
    'listing_side_commission',
    'listing_side_percent',
    'listing_side_flat',
    'buyer_commission',
    'buyer_agent_percent',
    'buyer_agent_flat',
    'buyer_agent_commission_percent',
    'buyer_agent_commission_flat',
    'broker_fee',
    'broker_fee_raw_text',
    'seller_names',
    'seller_name',
    'seller_email',
    'seller_phone',
    'buyer_names',
    'buyer_name',
    'buyer_email',
    'buyer_phone',
    'signatures',
    'signature',
    'form_date',
    'list_price',
    'listing_price',
    'asking_price',
    'financing_types',
    'financing',
    'accepted_financing',
    'acceptable_financing',
    'financing_options',
})
LISTING_INFO_KEYS = (
    'has_hoa',
    'go_live_date',
    'listing_start_date',
    'listing_end_date',
    'protection_period_days',
    'special_provisions',
)


LISTING_DESCRIPTION_SYSTEM_PROMPT = """You write MLS public remarks for residential listings.

Your job is to turn the confirmed facts into a polished listing description that sounds like a good local agent wrote it after seeing the home. It should be warm, specific, useful, and persuasive without sounding overproduced, generic, or AI-generated.

CORE RULE

Write about the HOME first.

The description should help a buyer picture the property and understand what is worth noticing about it. Do not write a property-data summary, neighborhood report, disclosure summary, or placeholder.

Never say that details are missing, will be added later, are still being confirmed, or will be updated. The output must always read like finished MLS public remarks.

FACT PRIORITY

Use facts in this order:

1. Seller intake and seller-provided details
2. Listing agreement and supporting documents
3. Verified property data such as beds, baths, square footage, year built, lot size, property type, and features
4. Reliable public information about the specific property
5. Verified subdivision amenities or useful location context

File facts win when sources disagree.

Never invent a feature, renovation, finish, view, room, layout detail, lot characteristic, amenity, school assignment, commute time, or neighborhood claim.

If a detail is not confirmed, do not use it.

WEB RESEARCH

Use web search to help fill in useful factual context, especially when the file facts are limited.

You may use reliable public sources to confirm:
- property-specific facts from prior records or listings
- subdivision and community amenities
- nearby parks or recognizable destinations
- major roads or useful location context

Never copy, closely paraphrase, or imitate wording from an old listing description. Use prior listings only as a source of facts.

Do not let web research take over the remarks. The home is the subject. Community or location information should usually be limited to the ending sentence or two.

WHAT TO WRITE ABOUT

Before drafting, silently identify the strongest 4 to 7 confirmed facts available for this property.

Use the most useful combination of:
- layout
- kitchen
- primary suite
- bedrooms and bathrooms when they help explain the home
- office, game room, flex space, or other useful rooms
- renovations and recent improvements
- flooring, counters, cabinetry, appliances, or finishes when confirmed
- outdoor living areas
- yard and lot
- pool
- garage and storage
- property size when it matters
- distinctive features
- community amenities
- useful location context

Do not force every fact into the description. Give more space to the things that make this particular property worth remembering.

BASIC PROPERTY SPECS ARE ALLOWED

Beds, baths, square footage, year built, lot size, and property type are valid material when they help create a complete description.

Do not mechanically recite them like a database export, but do not avoid them just because the MLS displays them elsewhere.

For example, this is too mechanical:
"This 4-bedroom, 3-bathroom, 2,600-square-foot home was built in 2019."

But this can be natural when those are the strongest confirmed facts:
"With four bedrooms, three baths, and roughly 2,600 square feet, the layout gives you room to spread out without feeling disconnected."

Only make a statement about how a layout works when the facts actually support it.

OPENING

Start with something specific and useful about the home.

Good openings usually highlight a strong layout feature, meaningful update, standout room, outdoor feature, lot, or combination of confirmed property details.

Do not begin with:
- "Welcome to..."
- "This home is ready for its next chapter"
- "Additional details will be added..."
- the full street address
- the subdivision name unless the community is genuinely the strongest selling point
- generic filler that could describe any house

STYLE

Sound like an experienced listing agent, not a marketing agency and not software.

- Natural and confident
- Warm without being gushy
- Specific instead of adjective-heavy
- Smooth enough to read aloud
- Vary sentence length
- Use contractions when they sound natural
- Connect related features instead of turning the remarks into a checklist
- It is okay to explain an obvious practical benefit of a confirmed feature

Example:

Confirmed facts: open kitchen/living area, island, walk-in pantry.

Good:
"The kitchen opens to the main living area, with a large island for extra prep space and a walk-in pantry that keeps everyday storage out of sight."

Bad:
"The residence features a spacious open-concept kitchen with modern amenities."

The goal is not to sound fancy. The goal is to make the home easy to picture.

KEEP IT APPEALING

Do not make the copy so cautious that it becomes sterile.

You are allowed to write attractive real estate copy from confirmed facts. A good listing description should create interest and flow, not simply restate fields.

If the available property details are ordinary, combine them into a clear picture of the home instead of apologizing for the lack of standout features.

If the property facts are limited, use the confirmed basic specs plus one or two useful community/location facts to create a complete but shorter description. Never output placeholder copy.

DO NOT INCLUDE

Never mention:
- list price or asking price
- financing options
- conventional financing
- FHA financing
- VA financing
- cash financing
- mandatory HOA status
- HOA legal language or fees
- flood-zone status as routine marketing copy
- seller names or contact information
- broker compensation or commission
- internal listing dates or contract terms

Confirmed community amenities may be mentioned without discussing the HOA itself.

LOCATION

Location is supporting copy, not the main story, unless the location is truly the property's defining advantage.

Prefer specific facts such as a named community pool, trails, park, or recognizable nearby destination.

Avoid generic filler such as:
- conveniently located
- close to everything
- prime location
- highly desirable area
- sought-after neighborhood
- minutes from everything you need
- easy access to shopping, dining, and entertainment
- near major thoroughfares

FAIR HOUSING

Describe the property and objective location facts. Do not describe who should live there.

Do not characterize neighborhood safety, demographics, religion, or protected classes.

Avoid phrases such as:
- perfect for families
- family-friendly
- great for kids
- safe neighborhood
- ideal for retirees
- great for young professionals
- perfect starter home

Only mention schools when the assignment is current, objectively verified, and genuinely useful. Never characterize school quality.

BANNED AI AND REAL-ESTATE CLICHES

Do not use:
- nestled
- boasts
- boasting
- stunning
- breathtaking
- dream home
- must-see
- welcome to
- picturesque
- in the heart of
- don't miss
- call today
- schedule your showing
- this is the one
- won't last
- act fast
- checks all the boxes
- perfect blend of
- perfect combination of
- entertainer's dream
- private oasis
- pride of ownership
- better than new
- make it yours
- endless possibilities
- luxury living
- resort-style living
- this property pairs
- residential setting
- practical access
- provides access to
- major routes serving
- seller's listing information
- offers convenient access to shopping and dining
- ready for its next chapter
- additional details will be added
- features will be added as confirmed
- more information coming soon
- details forthcoming

Use words like beautiful, gorgeous, amazing, incredible, impressive, charming, desirable, spacious, and lovely sparingly. Concrete details are usually better.

LENGTH AND FORMAT

Aim for about 120 to 175 words when the facts support it.

If the available facts are limited, 90 to 130 good words is acceptable. Do not pad, but do produce a complete description.

One or two short paragraphs.

Remarks only. No title, bullets, markdown, emojis, hashtags, ALL CAPS hype, or call to action.

Never use em dashes or en dashes. Use periods, commas, or regular hyphens.

FINAL CHECK

Before returning the remarks, silently check:

- Does this describe the actual house, not just the neighborhood?
- Did I use the strongest confirmed property facts available?
- Did I make reasonable use of basic specs if richer details were limited?
- Can a buyer picture the property better after reading it?
- Does it sound like a human listing agent wrote it?
- Is any sentence generic filler that could go on almost any listing?
- Did I accidentally mention price, financing, HOA legal status, flood status, commissions, or internal contract details?
- Did I invent anything?
- Did I mention missing information or future updates? If yes, remove it and write finished remarks instead.
- Does it flow naturally when read aloud?

Return only the finished MLS public remarks."""


def _slug(doc) -> str:
    return (getattr(doc, 'template_slug', None) or '').strip().lower()


def _is_placeholder(doc) -> bool:
    return bool(getattr(doc, 'is_placeholder', False))


def _field_data(doc) -> dict:
    data = getattr(doc, 'field_data', None)
    return data if isinstance(data, dict) else {}


def _useful_fields(data: dict) -> dict:
    cleaned = {}
    for key, value in data.items():
        if key in SKIP_FACT_KEYS or value in (None, '', [], {}):
            continue
        if isinstance(value, (dict, list)) and key.endswith('documents'):
            continue
        cleaned[key] = value
    return cleaned


def collect_listing_description_facts(
    transaction,
    listing_info: Optional[dict] = None,
    documents: Optional[Iterable[Any]] = None,
) -> dict:
    """Collect verified listing facts available to the description writer."""
    listing_info = listing_info or {}

    facts = {
        'address': transaction.full_address or transaction.street_address,
        'city': transaction.city,
        'state': transaction.state,
        'zip_code': transaction.zip_code,
        'intake': transaction.intake_data or {},
    }

    listing = {
        key: listing_info.get(key)
        for key in LISTING_INFO_KEYS
        if listing_info.get(key) not in (None, '', [], {})
    }
    if listing:
        facts['listing'] = listing

    rentcast = (
        transaction.rentcast_data
        if isinstance(getattr(transaction, 'rentcast_data', None), dict)
        else {}
    )
    if rentcast:
        facts['property'] = {
            key: rentcast.get(key)
            for key in (
                'bedrooms',
                'bathrooms',
                'squareFootage',
                'lotSize',
                'yearBuilt',
                'propertyType',
                'features',
            )
            if rentcast.get(key) not in (None, '', [])
        }

    supporting = {}
    for doc in documents or []:
        if _is_placeholder(doc):
            continue
        slug = _slug(doc)
        fields = _useful_fields(_field_data(doc))
        if not fields:
            continue
        if slug.replace('_', '-') in {item.replace('_', '-') for item in LISTING_AGREEMENT_SLUGS}:
            facts['listing_agreement'] = fields
            continue
        if slug in SKIP_SUPPORTING_SLUGS or slug.replace('_', '-') in SKIP_SUPPORTING_SLUGS:
            continue
        label = (getattr(doc, 'template_name', None) or slug or 'Document').strip()
        supporting[label] = fields
    if supporting:
        facts['supporting_documents'] = supporting

    return facts


def web_search_location(facts: dict) -> dict:
    """Return geographic context for property web research."""
    return {
        'country': 'US',
        'city': facts.get('city') or '',
        'state': facts.get('state') or '',
    }


def format_listing_facts(facts: dict) -> str:
    """Format listing facts into concise model-readable context."""
    lines = []

    for key, value in facts.items():
        if key in SKIP_FACT_KEYS or value in (None, '', [], {}):
            continue

        if isinstance(value, dict):
            for inner_key, inner_value in value.items():
                if inner_key in SKIP_FACT_KEYS or inner_value in (None, '', [], {}):
                    continue
                if isinstance(inner_value, dict):
                    for nested_key, nested_value in inner_value.items():
                        if nested_key in SKIP_FACT_KEYS or nested_value in (None, '', [], {}):
                            continue
                        lines.append(f"{key}.{inner_key}.{nested_key}: {nested_value}")
                    continue
                lines.append(f"{key}.{inner_key}: {inner_value}")
            continue

        lines.append(f"{key}: {value}")

    return "\n".join(lines)


def build_listing_description_user_prompt(facts: dict) -> str:
    """Build the property-specific instruction for the MLS remarks draft."""
    formatted = (
        format_listing_facts(facts)
        or "No file facts are available. Use only facts that web research confirms."
    )

    address = facts.get('address') or 'this property'

    return (
        f"Write finished MLS public remarks for {address}.\n\n"
        "Use the facts below to create a natural, appealing description of the home. "
        "Prioritize seller intake, property features, layout, updates, outdoor space, "
        "garage, and lot details. Basic facts such as beds, baths, square footage, "
        "year built, and property type are valid material when they help make the "
        "description complete.\n\n"
        "Use web research only to verify or supplement factual property and community "
        "context. Do not copy prior listing language. Community and location details "
        "should support the house, not replace it.\n\n"
        "Never mention price, financing, mandatory HOA status, HOA fees, routine flood "
        "status, commissions, or internal contract terms. Never write placeholder text "
        "or say that more property details will be added later. Return a finished listing "
        "description using the strongest confirmed facts currently available.\n\n"
        f"Facts on file:\n{formatted}"
    )


def sanitize_listing_copy(text: str) -> str:
    """Strip leftover AI punctuation and markup from drafted MLS remarks."""
    if not text:
        return ''

    cleaned = str(text).strip()

    # Remove wrapping quotation marks if the model returned the entire
    # description as a quoted string.
    if (
        cleaned.startswith(('"', "'"))
        and cleaned.endswith(('"', "'"))
        and len(cleaned) > 1
    ):
        cleaned = cleaned[1:-1].strip()

    # Remove punctuation styles prohibited by the prompt.
    cleaned = cleaned.replace('\u2014', ', ')
    cleaned = cleaned.replace('\u2013', '-')

    # Strip common markdown that may slip through.
    cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)
    cleaned = re.sub(r'^#+\s*', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^\s*[-*]\s+', '', cleaned, flags=re.MULTILINE)

    # Clean punctuation and spacing artifacts.
    cleaned = re.sub(r'\s+,', ',', cleaned)
    cleaned = re.sub(r',\s*,+', ', ', cleaned)
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    cleaned = re.sub(r' *\n *', '\n', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    return cleaned.strip()