"""Infer which side the uploading agent represents, from the document itself.

Texas forms answer this question more often than not, so the agent should not
have to. Two independent sources of truth, in priority order:

1. Form type. Some forms only exist for one side of the deal. A Residential
   Real Estate Listing Agreement (TXR-1101) is an engagement between the seller
   and the listing broker, so whoever uploads it represents the seller. A
   Buyer/Tenant Representation Agreement (TXR-1501 / 1507) is the mirror image.
2. Broker block. A purchase contract is signed by both sides, so the form type
   proves nothing — but the Broker Information page names the Listing Broker and
   the Other Broker (the buyer's broker) with license numbers and representation
   checkboxes. Matching the uploading agent's own name, license, or brokerage
   against those two columns identifies their side.

When neither source is decisive (blank broker page, intermediary, unreadable
scan) this returns no side and the caller asks the agent. AI reads the fields;
this module decides. It never guesses.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from services.document_identity import (
    HIGH_CONFIDENCE,
    KIND_LISTING_AGREEMENT,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
)

VALID_SIDES = frozenset({'buyer', 'seller'})

BASIS_FORM_TYPE = 'form_type'
BASIS_BROKER_BLOCK = 'broker_block'
BASIS_NONE = 'undetermined'

# Forms that only a listing (seller-side) broker signs.
_SELLER_FORM_PATTERNS: tuple[str, ...] = (
    r'\btxr[-\s]?1101\b',
    r'residential real estate listing agreement',
    r'exclusive right to sell',
    r'\btxr[-\s]?1201\b',
    r'farm and ranch real estate listing agreement',
)
# Forms that only a buyer's broker signs.
_BUYER_FORM_PATTERNS: tuple[str, ...] = (
    r'\btxr[-\s]?1501\b',
    r'\btxr[-\s]?1507\b',
    r'\btxr[-\s]?1502\b',
    r'buyer\s*/\s*tenant representation agreement',
    r"buyer'?s? representation agreement",
)

# Broker Information field names, per side of the contract.
_LISTING_SIDE_FIELDS: tuple[str, ...] = (
    'listing_broker_firm',
    'listing_broker_license_no',
    'listing_associate_name',
    'listing_associate_license_no',
    'selling_associate_name',
)
_BUYER_SIDE_FIELDS: tuple[str, ...] = (
    'other_broker_firm',
    'other_broker_license_no',
    'other_broker_associate_name',
    'other_broker_associate_license_no',
)

# Role labels as they appear in the Broker Information block, for the raw-text
# fallback. "Selling Associate" sits in the Listing Broker column on TREC forms.
_LISTING_ROLE_LABELS: tuple[str, ...] = (
    'listing broker',
    'listing associate',
    'selling associate',
    "seller's agent",
    'sellers agent',
)
_BUYER_ROLE_LABELS: tuple[str, ...] = (
    'other broker',
    "buyer's agent",
    'buyers agent',
    "buyer's broker",
)
# How far back from a name/license hit we look for the role label that owns it.
_ROLE_WINDOW = 400


@dataclass(frozen=True)
class AgentProfile:
    """Identifiers for the uploading agent and their brokerage."""

    names: tuple[str, ...] = ()
    licenses: tuple[str, ...] = ()
    brokerages: tuple[str, ...] = ()

    @property
    def is_usable(self) -> bool:
        return bool(self.names or self.licenses or self.brokerages)


@dataclass(frozen=True)
class RepresentationInference:
    """What the document says about the uploading agent's side."""

    side: Optional[str] = None
    confidence: float = 0.0
    basis: str = BASIS_NONE
    evidence: tuple[str, ...] = ()
    summary: str = ''
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def is_confident(self) -> bool:
        return self.side in VALID_SIDES and self.confidence >= HIGH_CONFIDENCE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['evidence'] = list(self.evidence)
        data['is_confident'] = self.is_confident
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'RepresentationInference':
        if not data:
            return cls()
        return cls(
            side=data.get('side'),
            confidence=float(data.get('confidence') or 0.0),
            basis=str(data.get('basis') or BASIS_NONE),
            evidence=tuple(data.get('evidence') or ()),
            summary=str(data.get('summary') or ''),
            extras=dict(data.get('extras') or {}),
        )


def agent_profile_for_user(user: Any, organization: Any = None) -> AgentProfile:
    """Collect the uploader's own name, license, and brokerage identifiers."""
    names: list[str] = []
    licenses: list[str] = []
    brokerages: list[str] = []

    def add(bucket: list[str], value: Any) -> None:
        text = str(value or '').strip()
        if text:
            bucket.append(text)

    if user is not None:
        first = str(getattr(user, 'first_name', '') or '').strip()
        last = str(getattr(user, 'last_name', '') or '').strip()
        if first and last:
            add(names, f'{first} {last}')
            add(names, f'{last}, {first}')
        add(names, getattr(user, 'full_name', None))
        add(licenses, getattr(user, 'license_number', None))

    org = organization
    if org is None and user is not None:
        org = getattr(user, 'organization', None)
    if org is not None:
        add(brokerages, getattr(org, 'broker_name', None))
        add(brokerages, getattr(org, 'name', None))
        add(licenses, getattr(org, 'broker_license_number', None))

    return AgentProfile(
        names=tuple(dict.fromkeys(names)),
        licenses=tuple(dict.fromkeys(licenses)),
        brokerages=tuple(dict.fromkeys(brokerages)),
    )


def _norm(value: Any) -> str:
    text = re.sub(r'[^a-z0-9 ]+', ' ', str(value or '').lower())
    return re.sub(r'\s+', ' ', text).strip()


def _norm_license(value: Any) -> str:
    return re.sub(r'\D+', '', str(value or ''))


def _matches_identifier(haystack: str, profile: AgentProfile) -> Optional[str]:
    """Return a short evidence label when the agent appears in this text."""
    text = _norm(haystack)
    if not text:
        return None
    for name in profile.names:
        candidate = _norm(name)
        if len(candidate) >= 5 and candidate in text:
            return f'name:{name}'
    for brokerage in profile.brokerages:
        candidate = _norm(brokerage)
        if len(candidate) >= 4 and candidate in text:
            return f'brokerage:{brokerage}'
    digits = _norm_license(haystack)
    if digits:
        for license_no in profile.licenses:
            candidate = _norm_license(license_no)
            if len(candidate) >= 4 and candidate in digits:
                return f'license:{license_no}'
    return None


def _side_from_broker_fields(
    field_data: dict[str, Any],
    profile: AgentProfile,
) -> tuple[Optional[str], list[str]]:
    """Match the agent against the extracted Listing / Other Broker columns."""
    listing_hits: list[str] = []
    buyer_hits: list[str] = []

    for key in _LISTING_SIDE_FIELDS:
        hit = _matches_identifier(field_data.get(key), profile)
        if hit:
            listing_hits.append(f'{key} {hit}')
    for key in _BUYER_SIDE_FIELDS:
        hit = _matches_identifier(field_data.get(key), profile)
        if hit:
            buyer_hits.append(f'{key} {hit}')

    if listing_hits and not buyer_hits:
        return 'seller', listing_hits
    if buyer_hits and not listing_hits:
        return 'buyer', buyer_hits
    if listing_hits and buyer_hits:
        # Same brokerage in both columns is an intermediary situation; the agent
        # has to tell us which client is theirs.
        return None, listing_hits + buyer_hits
    return None, []


def _side_from_text(
    text: str,
    profile: AgentProfile,
) -> tuple[Optional[str], list[str]]:
    """Fallback: attribute agent mentions to the nearest preceding role label."""
    normalized = _norm(text)
    if not normalized:
        return None, []

    identifiers: list[tuple[str, str]] = []
    for name in profile.names:
        candidate = _norm(name)
        if len(candidate) >= 5:
            identifiers.append((candidate, f'name:{name}'))
    for brokerage in profile.brokerages:
        candidate = _norm(brokerage)
        if len(candidate) >= 4:
            identifiers.append((candidate, f'brokerage:{brokerage}'))
    for license_no in profile.licenses:
        candidate = _norm_license(license_no)
        if len(candidate) >= 6:
            identifiers.append((candidate, f'license:{license_no}'))
    if not identifiers:
        return None, []

    listing_hits: list[str] = []
    buyer_hits: list[str] = []

    for needle, label in identifiers:
        start = normalized.find(needle)
        while start != -1:
            window = normalized[max(0, start - _ROLE_WINDOW):start]
            listing_at = max(
                (window.rfind(role) for role in _LISTING_ROLE_LABELS), default=-1,
            )
            buyer_at = max(
                (window.rfind(role) for role in _BUYER_ROLE_LABELS), default=-1,
            )
            if listing_at > buyer_at:
                listing_hits.append(f'listing column {label}')
            elif buyer_at > listing_at:
                buyer_hits.append(f'other broker column {label}')
            start = normalized.find(needle, start + 1)

    if listing_hits and not buyer_hits:
        return 'seller', listing_hits[:3]
    if buyer_hits and not listing_hits:
        return 'buyer', buyer_hits[:3]
    return None, []


def _first_pattern_match(text: str, patterns: tuple[str, ...]) -> Optional[str]:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return None


def infer_representation(
    *,
    identity: DocumentIdentity | None = None,
    text: str = '',
    field_data: dict[str, Any] | None = None,
    profile: AgentProfile | None = None,
) -> RepresentationInference:
    """Infer the uploading agent's representation side from the document."""
    haystack = re.sub(r'\s+', ' ', text or '')
    fields = field_data or {}
    profile = profile or AgentProfile()

    # --- 1. Forms that exist for exactly one side ---
    seller_form = _first_pattern_match(haystack, _SELLER_FORM_PATTERNS)
    buyer_form = _first_pattern_match(haystack, _BUYER_FORM_PATTERNS)

    listing_identity = (
        identity is not None
        and identity.kind == KIND_LISTING_AGREEMENT
        and not identity.ambiguous
        and identity.confidence >= HIGH_CONFIDENCE
    )

    if listing_identity and not buyer_form:
        label = (identity.form_number or 'listing agreement') if identity else 'listing agreement'
        return RepresentationInference(
            side='seller',
            confidence=0.95,
            basis=BASIS_FORM_TYPE,
            evidence=(f'form:{label}',),
            summary=(
                f'A {identity.label if identity else "listing agreement"} is an '
                'agreement between the seller and the listing broker, so this is '
                'seller representation.'
            ),
        )

    if buyer_form and not seller_form:
        return RepresentationInference(
            side='buyer',
            confidence=0.93,
            basis=BASIS_FORM_TYPE,
            evidence=(f'form:{buyer_form}',),
            summary=(
                'A buyer/tenant representation agreement is signed by the buyer '
                'and their broker, so this is buyer representation.'
            ),
        )

    if seller_form and buyer_form:
        return RepresentationInference(
            summary=(
                'This PDF contains both seller-side and buyer-side agreements. '
                'Tell us which client is yours.'
            ),
        )

    # --- 2. Broker Information block on a two-sided contract ---
    if profile.is_usable:
        side, hits = _side_from_broker_fields(fields, profile)
        if side is None and hits:
            return RepresentationInference(
                summary=(
                    'Your brokerage appears as both the listing broker and the '
                    'other broker. Tell us which client is yours.'
                ),
                evidence=tuple(hits[:4]),
                extras={'intermediary_suspected': True},
            )
        if side is None:
            side, hits = _side_from_text(haystack, profile)
        if side is not None:
            column = 'Listing Broker' if side == 'seller' else 'Other Broker'
            return RepresentationInference(
                side=side,
                confidence=0.9,
                basis=BASIS_BROKER_BLOCK,
                evidence=tuple(hits[:4]),
                summary=(
                    f'You are named as the {column} on this contract, which is '
                    f'{side} representation.'
                ),
            )

    contract = identity is not None and identity.kind == KIND_PURCHASE_CONTRACT
    return RepresentationInference(
        summary=(
            'The broker section of this contract does not name you, so we cannot '
            'tell which side you are on.'
            if contract
            else 'This form does not say which side you represent.'
        ),
    )
