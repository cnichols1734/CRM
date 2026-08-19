"""Deterministic listing-package page classification and split planning.

Texas listing uploads are often a single PDF of mixed TAR/TREC forms.
AI page ranges help, but they cannot be the only authority — a missed
label used to leave Seller's Estimated Net Proceeds inside the listing
agreement file.

This module classifies every page from form numbers and titles, then
reconciles that map with any AI ``detected_documents`` list. Fingerprints
win on known forms. AI fills gaps and names leftover pages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import fitz

from services.pdf_splitter import SplitSegment, get_pdf_page_count, normalize_segments


LISTING_AGREEMENT_TYPE = 'listing_agreement'
UNKNOWN_TYPE = 'unknown'

# type → (template_slug, display name). Slugs match intake document_rules / YAML.
LISTING_PACKET_FILING: dict[str, tuple[str, str]] = {
    LISTING_AGREEMENT_TYPE: (
        'listing-agreement',
        'Residential Real Estate Listing Agreement',
    ),
    'iabs': ('iabs', 'Information About Brokerage Services'),
    'sellers_disclosure': ('sellers-disclosure', "Seller's Disclosure Notice"),
    'lead_based_paint': ('lead-paint', 'Lead-Based Paint Addendum'),
    'hoa_addendum': ('hoa-addendum', 'HOA Addendum'),
    'wire_fraud_warning': ('wire-fraud-warning', 'Wire Fraud Warning'),
    'seller_estimated_net_proceeds': (
        'seller-net-proceeds',
        "Seller's Estimated Net Proceeds",
    ),
    'flood_hazard': ('flood-hazard', 'Flood Hazard Information'),
    't47_affidavit': ('t47-affidavit', 'T-47 Residential Real Property Affidavit'),
    'special_tax_district_notice': (
        'special-tax-district-notice',
        'Special Tax District Notice',
    ),
    'sewer_facility': ('sewer-facility', 'On-Site Sewer Facility Notice'),
    'referral_agreement': ('referral-agreement', 'Referral Agreement'),
    UNKNOWN_TYPE: ('supporting-document', 'Supporting Document'),
}

_TYPE_ALIASES: dict[str, str] = {
    'listing_agreement': LISTING_AGREEMENT_TYPE,
    'listing_agreement': LISTING_AGREEMENT_TYPE,
    'residential_listing_agreement': LISTING_AGREEMENT_TYPE,
    'txr_1101': LISTING_AGREEMENT_TYPE,
    'iabs': 'iabs',
    'iabs': 'iabs',
    'information_about_brokerage_services': 'iabs',
    'information_about_brokerage_services': 'iabs',
    'sellers_disclosure': 'sellers_disclosure',
    'seller_disclosure': 'sellers_disclosure',
    'sellers_disclosure_notice': 'sellers_disclosure',
    'seller_disclosure_notice': 'sellers_disclosure',
    'txr_1406': 'sellers_disclosure',
    'lead_based_paint': 'lead_based_paint',
    'lead_paint': 'lead_based_paint',
    'lead_based_paint_addendum': 'lead_based_paint',
    'hoa_addendum': 'hoa_addendum',
    'poa_addendum': 'hoa_addendum',
    'property_owners_association': 'hoa_addendum',
    'property_owners_association_addendum': 'hoa_addendum',
    'trec_36': 'hoa_addendum',
    'wire_fraud_warning': 'wire_fraud_warning',
    'wire_fraud_warning': 'wire_fraud_warning',
    'wire_fraud': 'wire_fraud_warning',
    'wire_fraud_alert': 'wire_fraud_warning',
    'txr_2517': 'wire_fraud_warning',
    'seller_estimated_net_proceeds': 'seller_estimated_net_proceeds',
    'sellers_estimated_net_proceeds': 'seller_estimated_net_proceeds',
    'seller_net_proceeds': 'seller_estimated_net_proceeds',
    'sellers_net_proceeds': 'seller_estimated_net_proceeds',
    'estimated_net_proceeds': 'seller_estimated_net_proceeds',
    'seller_estimated_net': 'seller_estimated_net_proceeds',
    'txr_1935': 'seller_estimated_net_proceeds',
    'txr_2001': 'seller_estimated_net_proceeds',
    'flood_hazard': 'flood_hazard',
    'flood_hazard': 'flood_hazard',
    't47_affidavit': 't47_affidavit',
    't47_affidavit': 't47_affidavit',
    'special_tax_district_notice': 'special_tax_district_notice',
    'special_tax_district_notice': 'special_tax_district_notice',
    'sewer_facility': 'sewer_facility',
    'sewer_facility': 'sewer_facility',
    'referral_agreement': 'referral_agreement',
    'referral_agreement': 'referral_agreement',
    'other': UNKNOWN_TYPE,
    'unknown': UNKNOWN_TYPE,
    'supporting': UNKNOWN_TYPE,
    'supporting_document': UNKNOWN_TYPE,
}

_FORM_NUMBER_TO_TYPE: dict[str, str] = {
    'txr-1101': LISTING_AGREEMENT_TYPE,
    'txr-1935': 'seller_estimated_net_proceeds',
    'txr-2001': 'seller_estimated_net_proceeds',
    'txr-2501': 'iabs',
    'iabs': 'iabs',
    'txr-1406': 'sellers_disclosure',
    'txr-1906': 'lead_based_paint',
    'txr-1922': 'hoa_addendum',
    'trec-36': 'hoa_addendum',
    'txr-2517': 'wire_fraud_warning',
    'txr-1407': 'flood_hazard',
    't-47': 't47_affidavit',
    't-47.1': 't47_affidavit',
}

_FORM_NUMBER_RE = re.compile(
    r'\bTXR[-\s]?(\d{3,4})\b'
    r'|\bTAR[-\s]?(\d{3,4})\b'
    r'|\bTREC\s*(?:NO\.?\s*)?(\d{1,2})(?:[-\s](\d{1,2}))?\b'
    r'|\bIABS\s*(\d)[-\s]?(\d)\b'
    r'|\bT[-\s]?47(?:\.1)?\b',
    re.IGNORECASE,
)

_TITLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        LISTING_AGREEMENT_TYPE,
        re.compile(
            r'residential real estate listing agreement|exclusive right to sell',
            re.I,
        ),
    ),
    (
        'seller_estimated_net_proceeds',
        re.compile(r"seller'?s? estimated net proceeds", re.I),
    ),
    (
        'iabs',
        re.compile(
            r'information about brokerage services|'
            r'information about brokerage services',
            re.I,
        ),
    ),
    (
        'sellers_disclosure',
        re.compile(r"seller'?s? disclosure (?:notice|statement)", re.I),
    ),
    (
        'lead_based_paint',
        re.compile(r'lead[-\s]?based paint', re.I),
    ),
    (
        'hoa_addendum',
        re.compile(
            r'addendum for property subject to mandatory membership|'
            r'mandatory membership in a property owners association',
            re.I,
        ),
    ),
    (
        'wire_fraud_warning',
        re.compile(r'wire fraud (?:warning|alert)', re.I),
    ),
    (
        't47_affidavit',
        re.compile(r'residential real property affidavit|t[-\s]?47(?:\.1)?', re.I),
    ),
    (
        'flood_hazard',
        re.compile(r'flood hazard', re.I),
    ),
    (
        'sewer_facility',
        re.compile(r'on[-\s]?site sewer facility', re.I),
    ),
    (
        'special_tax_district_notice',
        re.compile(
            r'special (?:taxing )?district|municipal utility district',
            re.I,
        ),
    ),
    (
        'referral_agreement',
        re.compile(r'referral (?:fee )?agreement', re.I),
    ),
)

_FORM_START_RE = re.compile(
    r'use of this form by persons who are not members|'
    r'promulgated by the texas real estate commission|'
    r'information about brokerage services|'
    r'information about brokerage services',
    re.I,
)

_DETECTED_KEYS = (
    'detected_documents',
    'detected_documents',
    'detected_documents',
)


@dataclass(frozen=True)
class PacketSegment:
    """A classified run of pages inside a listing packet."""

    document_type: str
    start_page: int
    end_page: int
    title: Optional[str] = None
    source: str = 'fingerprint'


def canonicalize_packet_type(
    raw_type: str | None,
    *,
    title: str | None = None,
) -> Optional[str]:
    """Map an AI/extractor label (or title) onto a canonical packet type."""
    key = re.sub(r'[\s\-]+', '_', (raw_type or '').strip().lower())
    mapped = _TYPE_ALIASES.get(key)
    if mapped and mapped != UNKNOWN_TYPE:
        return mapped
    blob = f'{raw_type or ""} {title or ""}'.strip()
    if blob:
        titled = _type_from_title(blob)
        if titled:
            return titled
    if mapped == UNKNOWN_TYPE:
        return UNKNOWN_TYPE
    if key:
        return UNKNOWN_TYPE
    return None


def filing_for_type(document_type: str | None) -> tuple[str, str]:
    """Return (template_slug, display name) for a canonical packet type."""
    canonical = canonicalize_packet_type(document_type) or UNKNOWN_TYPE
    return LISTING_PACKET_FILING.get(
        canonical,
        LISTING_PACKET_FILING[UNKNOWN_TYPE],
    )


def detected_documents_from_field_data(field_data: dict | None) -> list:
    """Read AI package segments from any of the historical field-data keys."""
    if not isinstance(field_data, dict):
        return []
    for key in _DETECTED_KEYS:
        value = field_data.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def extract_page_texts(file_data: bytes) -> list[str]:
    """Return selectable text for each PDF page (1-based index = list index + 1)."""
    if not file_data:
        return []
    doc = fitz.open(stream=file_data, filetype='pdf')
    try:
        return [(page.get_text('text') or '') for page in doc]
    finally:
        doc.close()


def classify_page_text(text: str) -> Optional[str]:
    """Classify a single page. Prefers the footer form number, then titles."""
    form_type = _type_from_primary_form_number(text)
    if form_type:
        return form_type
    return _type_from_title(text)


def looks_like_form_start(text: str) -> bool:
    """True when the top of the page looks like a new TAR/TREC cover."""
    return bool(_FORM_START_RE.search(text[:900]))


def classify_listing_packet_pages(page_texts: Iterable[str]) -> list[PacketSegment]:
    """Group consecutive pages into document segments using fingerprints."""
    pages = list(page_texts)
    if not pages:
        return []

    assigned: list[Optional[str]] = []
    for text in pages:
        hit = classify_page_text(text)
        new_form = looks_like_form_start(text)
        previous = assigned[-1] if assigned else None
        if hit:
            assigned.append(hit)
        elif new_form:
            assigned.append(UNKNOWN_TYPE)
        elif previous:
            assigned.append(previous)
        else:
            assigned.append(UNKNOWN_TYPE)

    return _collapse_page_types(assigned, pages)


# How a page earned its document type. Only the first two are positive
# identifications; the rest are guesses we must not split on unreviewed.
SOURCE_FINGERPRINT = 'fingerprint'
SOURCE_AI = 'ai'
SOURCE_CARRY = 'carry'
SOURCE_UNRESOLVED = 'unresolved'

_ATTRIBUTED_SOURCES = frozenset({SOURCE_FINGERPRINT, SOURCE_AI})


@dataclass(frozen=True)
class PacketPlan:
    """A split plan plus how much of it we can actually stand behind.

    The fingerprint table only covers forms we have enumerated. Anything else
    — a survey, a lender letter, an out-of-state form — has no fingerprint and
    never will, so a plan built from fingerprints alone is incomplete by
    construction. ``page_sources`` records who identified each page so callers
    can decide whether to split now or wait for AI to cover the gaps.
    """

    segments: list[PacketSegment]
    page_sources: list[str]
    total_pages: int

    def pages_with_source(self, source: str) -> list[int]:
        return [i + 1 for i, s in enumerate(self.page_sources) if s == source]

    @property
    def unresolved_pages(self) -> list[int]:
        return self.pages_with_source(SOURCE_UNRESOLVED)

    @property
    def guessed_pages(self) -> list[int]:
        """Pages inherited from the previous page rather than identified."""
        return self.pages_with_source(SOURCE_CARRY)

    @property
    def is_confident(self) -> bool:
        """True only when every page was positively identified by someone."""
        return bool(self.page_sources) and all(
            source in _ATTRIBUTED_SOURCES for source in self.page_sources
        )

    def coverage_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for source in self.page_sources:
            counts[source] = counts.get(source, 0) + 1
        return {
            'total_pages': self.total_pages,
            'by_source': counts,
            'unresolved_pages': self.unresolved_pages,
            'guessed_pages': self.guessed_pages,
            'confident': self.is_confident,
        }


def resolve_packet_pages(
    page_texts: list[str],
    ai_segments: Iterable[dict] | None = None,
) -> tuple[list[str], list[Optional[str]], list[str]]:
    """Assign a document type to every page and record who assigned it.

    Order of authority: a page's own form fingerprint, then an AI page range,
    then continuation of the document already in progress. A page that looks
    like the cover of a new form nobody recognized ends the run instead of
    being absorbed into it.
    """
    total_pages = len(page_texts)
    page_types: list[Optional[str]] = [None] * total_pages
    titles: list[Optional[str]] = [None] * total_pages
    sources: list[Optional[str]] = [None] * total_pages

    for idx, text in enumerate(page_texts):
        hit = classify_page_text(text)
        if hit:
            page_types[idx] = hit
            sources[idx] = SOURCE_FINGERPRINT

    for seg in normalize_segments(ai_segments or [], total_pages=total_pages):
        canonical = canonicalize_packet_type(seg.document_type, title=seg.title)
        if not canonical:
            continue
        for page in range(seg.start_page, seg.end_page + 1):
            idx = page - 1
            if page_types[idx] is not None:
                continue
            resolved = canonical
            if canonical == UNKNOWN_TYPE:
                # AI said "other". Its title may still name a form we know.
                resolved = (
                    _type_from_title(seg.title or '')
                    or _type_from_title(page_texts[idx])
                    or UNKNOWN_TYPE
                )
            page_types[idx] = resolved
            titles[idx] = seg.title
            sources[idx] = SOURCE_AI

    previous: Optional[str] = None
    for idx, text in enumerate(page_texts):
        if page_types[idx] is not None:
            previous = page_types[idx]
            continue
        if looks_like_form_start(text):
            # A new form began here and nobody could name it. Do not let the
            # previous document swallow it.
            page_types[idx] = UNKNOWN_TYPE
            sources[idx] = SOURCE_UNRESOLVED
            previous = None
            continue
        if previous:
            page_types[idx] = previous
            sources[idx] = SOURCE_CARRY
            continue
        page_types[idx] = UNKNOWN_TYPE
        sources[idx] = SOURCE_UNRESOLVED

    return (
        [t or UNKNOWN_TYPE for t in page_types],
        titles,
        [s or SOURCE_UNRESOLVED for s in sources],
    )


def reconcile_listing_packet_segments(
    *,
    page_texts: list[str],
    ai_segments: Iterable[dict] | None = None,
) -> list[PacketSegment]:
    """Fingerprint pages, then fill gaps from AI detected_documents."""
    if not page_texts:
        return []
    page_types, titles, sources = resolve_packet_pages(page_texts, ai_segments)
    return _collapse_page_types(
        page_types, page_texts, titles=titles, sources=sources,
    )


def build_listing_packet_plan(
    file_data: bytes,
    *,
    ai_segments: Iterable[dict] | None = None,
) -> PacketPlan:
    """Plan the split and report how well every page is accounted for."""
    page_texts = extract_page_texts(file_data)
    if not page_texts:
        return PacketPlan(segments=[], page_sources=[], total_pages=0)
    page_types, titles, sources = resolve_packet_pages(page_texts, ai_segments)
    segments = _collapse_page_types(
        page_types, page_texts, titles=titles, sources=sources,
    )
    return PacketPlan(
        segments=segments,
        page_sources=sources,
        total_pages=len(page_texts),
    )


def plan_listing_packet_split(
    file_data: bytes,
    *,
    ai_segments: Iterable[dict] | None = None,
) -> list[PacketSegment]:
    """Return the split plan for a listing-package PDF."""
    return build_listing_packet_plan(file_data, ai_segments=ai_segments).segments


def packet_segments_as_split_segments(
    segments: Iterable[PacketSegment],
) -> list[SplitSegment]:
    return [
        SplitSegment(
            start_page=seg.start_page,
            end_page=seg.end_page,
            document_type=seg.document_type,
            title=seg.title,
            notes=seg.source,
        )
        for seg in segments
    ]


def packet_segments_as_detected_documents(
    segments: Iterable[PacketSegment],
) -> list[dict[str, Any]]:
    return [
        {
            'document_type': seg.document_type,
            'start_page': seg.start_page,
            'end_page': seg.end_page,
            'title': seg.title,
            'source': seg.source,
        }
        for seg in segments
    ]


def _normalize_form_token(match: re.Match[str]) -> Optional[str]:
    raw = match.group(0)
    upper = raw.upper()
    if upper.startswith('TXR') or upper.startswith('TAR'):
        digits = re.sub(r'\D', '', raw)
        return f'txr-{digits}' if digits else None
    if upper.startswith('TREC'):
        numbers = re.findall(r'\d+', raw)
        return f'trec-{numbers[0]}' if numbers else None
    if upper.startswith('IABS'):
        return 'iabs'
    if re.search(r'T[-\s]?47', upper):
        return 't-47.1' if '.1' in upper else 't-47'
    return None


def extract_form_numbers(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _FORM_NUMBER_RE.finditer(text or ''):
        token = _normalize_form_token(match)
        if token:
            tokens.append(token)
    return tokens


def _type_from_primary_form_number(text: str) -> Optional[str]:
    tokens = extract_form_numbers(text)
    if not tokens:
        return None
    # Footer form number is almost always the last TXR/TREC/IABS on the page.
    # Body text may cite other forms (TXR-1412 inside a listing agreement).
    for token in reversed(tokens):
        mapped = _FORM_NUMBER_TO_TYPE.get(token)
        if mapped:
            return mapped
    return None


def _type_from_title(text: str) -> Optional[str]:
    if not text:
        return None
    head = text[:1500]
    for doc_type, pattern in _TITLE_RULES:
        if pattern.search(head) or pattern.search(text):
            return doc_type
    return None


def _collapse_page_types(
    page_types: list[Optional[str]],
    page_texts: list[str],
    *,
    titles: list[Optional[str]] | None = None,
    sources: list[str] | None = None,
) -> list[PacketSegment]:
    segments: list[PacketSegment] = []
    current_type: Optional[str] = None
    start = 1
    for index, doc_type in enumerate(page_types):
        page_no = index + 1
        label = doc_type or UNKNOWN_TYPE
        if current_type is None:
            current_type = label
            start = page_no
            continue
        if label != current_type:
            segments.append(
                PacketSegment(
                    document_type=current_type,
                    start_page=start,
                    end_page=page_no - 1,
                    title=_segment_title(
                        current_type, start, page_no - 1, page_texts, titles,
                    ),
                    source=_segment_source(start, page_no - 1, sources),
                )
            )
            current_type = label
            start = page_no
    if current_type is not None:
        segments.append(
            PacketSegment(
                document_type=current_type,
                start_page=start,
                end_page=len(page_types),
                title=_segment_title(
                    current_type, start, len(page_types), page_texts, titles,
                ),
                source=_segment_source(start, len(page_types), sources),
            )
        )
    return segments


def _segment_title(
    doc_type: str,
    start: int,
    end: int,
    page_texts: list[str],
    titles: list[Optional[str]] | None,
) -> str:
    if titles:
        for page in range(start, end + 1):
            value = titles[page - 1]
            if value:
                return value
    # "Supporting Document" tells the agent nothing. For a form we could not
    # identify, the heading printed on the page is the most useful name we have.
    if doc_type != UNKNOWN_TYPE:
        filing = LISTING_PACKET_FILING.get(doc_type)
        if filing:
            return filing[1]
    first = (page_texts[start - 1] if start - 1 < len(page_texts) else '') or ''
    for line in first.splitlines():
        stripped = line.strip()
        if len(stripped) >= 8 and not _FORM_START_RE.search(stripped):
            return stripped[:120]
    if doc_type == UNKNOWN_TYPE:
        return ''
    return doc_type.replace('_', ' ').title()


def _segment_source(start: int, end: int, sources: list[str] | None) -> str:
    if not sources:
        return 'fingerprint'
    chunk = sources[start - 1:end]
    if chunk and all(item == 'ai' for item in chunk):
        return 'ai'
    if chunk and any(item == 'ai' for item in chunk):
        return 'fingerprint+ai'
    return 'fingerprint'


def listing_packet_page_count(file_data: bytes) -> int:
    return get_pdf_page_count(file_data)
