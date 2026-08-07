"""Document identity for Texas transaction PDFs.

Regex/text signals provide a fast first-pass identity for schema selection and
routing before extraction. After OpenAI extraction returns
``detected_documents``, that list is the package source of truth — including
which supporting forms are actually in the PDF. Checklist keyword hits must
not override the model.

``apply_ai_package_authority`` / ``persist_identity_on_field_data`` enforce
that handoff. This module has no OpenAI dependency itself.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Reuse offer-package signal knowledge without inventing a second map.
from services.seller_workflow import (
    OFFER_DOCUMENT_TYPES,
    infer_offer_document_type_from_text,
)

HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.65

KIND_LISTING_AGREEMENT = 'listing_agreement'
KIND_PURCHASE_CONTRACT = 'purchase_contract'
KIND_ADDENDUM = 'addendum'
KIND_AMENDMENT = 'amendment'
KIND_DISCLOSURE = 'disclosure'
KIND_PROOF_OF_FUNDS = 'proof_of_funds'
KIND_OTHER = 'other'
KIND_UNKNOWN = 'unknown'

EXEC_DRAFT = 'draft_proposed'
EXEC_PARTY_SIGNED = 'party_signed'
EXEC_EXECUTED = 'executed'
EXEC_UNKNOWN = 'unknown'

SCOPE_LISTING = 'listing'
SCOPE_OFFER = 'offer'
SCOPE_CONTRACT = 'contract'
SCOPE_AMENDMENT = 'amendment'

# Generic upload slugs that should defer to content identity for schema choice.
GENERIC_TEMPLATE_SLUGS = frozenset({
    'completed',
    'external',
    'custom',
    'upload',
    'uploaded',
    'other',
    '',
})

# Deterministic form → identity maps (title / form-number first).
_FORM_RULES: tuple[tuple[tuple[str, ...], dict[str, Any]], ...] = (
    (
        (
            r'\btxr[-\s]?1101\b',
            r'residential real estate listing agreement',
            r'exclusive right to sell',
        ),
        {
            'kind': KIND_LISTING_AGREEMENT,
            'template_slug': 'listing-agreement',
            'form_number': 'TXR-1101',
            'label': 'Residential Real Estate Listing Agreement',
            'scopes': (SCOPE_LISTING,),
            'base_confidence': 0.95,
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?20[-\s]?\d*\b',
            r'one to four family residential contract',
            r'promulgated by the texas real estate commission.*one to four',
        ),
        {
            'kind': KIND_PURCHASE_CONTRACT,
            'template_slug': 'one-to-four-family-contract',
            'form_number': 'TREC 20',
            'label': 'One to Four Family Residential Contract',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.93,
            'purchase_contract_type': 'resale_one_to_four',
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?30[-\s]?\d*\b',
            r'residential condominium contract',
        ),
        {
            'kind': KIND_PURCHASE_CONTRACT,
            'template_slug': 'condominium-contract',
            'form_number': 'TREC 30',
            'label': 'Residential Condominium Contract',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.92,
            'purchase_contract_type': 'condominium',
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?23[-\s]?\d*\b',
            r'new home contract \(completed construction\)',
            r'new home contract \(complete',
        ),
        {
            'kind': KIND_PURCHASE_CONTRACT,
            'template_slug': 'new-home-completed-construction-contract',
            'form_number': 'TREC 23',
            'label': 'New Home Contract (Completed Construction)',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.9,
            'purchase_contract_type': 'new_construction_complete',
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?24[-\s]?\d*\b',
            r'new home contract \(incomplete construction\)',
        ),
        {
            'kind': KIND_PURCHASE_CONTRACT,
            'template_slug': 'new-home-incomplete-construction-contract',
            'form_number': 'TREC 24',
            'label': 'New Home Contract (Incomplete Construction)',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.9,
            'purchase_contract_type': 'new_construction_incomplete',
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?25[-\s]?\d*\b',
            r'farm and ranch contract',
        ),
        {
            'kind': KIND_PURCHASE_CONTRACT,
            'template_slug': 'farm-and-ranch-contract',
            'form_number': 'TREC 25',
            'label': 'Farm and Ranch Contract',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.9,
            'purchase_contract_type': 'farm_and_ranch',
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?9[-\s]?\d*\b',
            r'unimproved property contract',
        ),
        {
            'kind': KIND_PURCHASE_CONTRACT,
            'template_slug': 'unimproved-property-contract',
            'form_number': 'TREC 9',
            'label': 'Unimproved Property Contract',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.88,
            'purchase_contract_type': 'other',
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?39[-\s]?\d*\b',
            r'\bamendment to contract\b',
            r'\bcontract amendment\b',
        ),
        {
            'kind': KIND_AMENDMENT,
            'template_slug': 'amendment',
            'form_number': 'TREC 39',
            'label': 'Amendment to Contract',
            'scopes': (SCOPE_AMENDMENT, SCOPE_CONTRACT),
            'base_confidence': 0.93,
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?49[-\s]?\d*\b',
            r'\btxr[-\s]?1948\b',
            r'addendum concerning right to terminate due to lender.?s? appraisal',
            r'right to terminate due to lender.?s? appraisal',
        ),
        {
            'kind': KIND_ADDENDUM,
            'template_slug': 'appraisal-termination-addendum',
            'form_number': 'TREC 49',
            'label': "Addendum Concerning Right to Terminate Due to Lender's Appraisal",
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.93,
            'addendum_key': 'appraisal_termination',
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?40[-\s]?\d*\b',
            r'third party financing addendum',
        ),
        {
            'kind': KIND_ADDENDUM,
            'template_slug': 'third-party-financing-addendum',
            'form_number': 'TREC 40',
            'label': 'Third Party Financing Addendum',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.92,
            'addendum_key': 'third_party_financing',
        },
    ),
    (
        (
            r'\btxr[-\s]?2402\b',
            r'compensation agreement between brokers',
            r'broker compensation agreement',
        ),
        {
            'kind': KIND_ADDENDUM,
            'template_slug': 'broker-compensation-agreement',
            'form_number': 'TXR-2402',
            'label': 'Compensation Agreement Between Brokers',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.92,
            'addendum_key': 'broker_compensation',
        },
    ),
    (
        (
            r'\btrec\s*(?:no\.?\s*)?36[-\s]?\d*\b',
            r'addendum for property subject to mandatory membership',
            r'property owners association',
        ),
        {
            'kind': KIND_ADDENDUM,
            'template_slug': 'hoa-addendum',
            'form_number': 'TREC 36',
            'label': 'HOA / POA Addendum',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT, SCOPE_LISTING),
            'base_confidence': 0.9,
            'addendum_key': 'hoa',
        },
    ),
    (
        (
            r'addendum for back[-\s]?up contract',
            r'\bbackup addendum\b',
            r'\bback-up contract\b',
        ),
        {
            'kind': KIND_ADDENDUM,
            'template_slug': 'seller-backup-addendum',
            'form_number': None,
            'label': 'Addendum for Back-Up Contract',
            'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.9,
            'addendum_key': 'backup',
        },
    ),
    (
        (
            r"seller'?s? disclosure notice",
            r'\btxr[-\s]?1406\b',
            r'\bop[-\s]?h\b',
        ),
        {
            'kind': KIND_DISCLOSURE,
            'template_slug': 'sellers-disclosure',
            'form_number': 'TXR-1406',
            'label': "Seller's Disclosure Notice",
            'scopes': (SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.9,
            'addendum_key': 'sellers_disclosure',
        },
    ),
    (
        (
            r'lead[-\s]?based paint',
            r'disclosure of information on lead',
        ),
        {
            'kind': KIND_DISCLOSURE,
            'template_slug': 'lead-paint',
            'form_number': None,
            'label': 'Lead-Based Paint Disclosure',
            'scopes': (SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.88,
            'addendum_key': 'lead_based_paint',
        },
    ),
    (
        (
            r'\bpre[-\s]?approval\b',
            r'\bpre[-\s]?qualification\b',
            r'\bproof of funds\b',
        ),
        {
            'kind': KIND_PROOF_OF_FUNDS,
            'template_slug': 'pre-approval-or-proof-of-funds',
            'form_number': None,
            'label': 'Pre-Approval / Proof of Funds',
            'scopes': (SCOPE_OFFER,),
            'base_confidence': 0.85,
            'addendum_key': 'pre_approval',
        },
    ),
    (
        (
            r'information about brokerage services',
            r'\biabs\b',
            r'\btxr[-\s]?2501\b',
        ),
        {
            'kind': KIND_DISCLOSURE,
            'template_slug': 'iabs',
            'form_number': 'TXR-2501',
            'label': 'Information About Brokerage Services',
            'scopes': (SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT),
            'base_confidence': 0.92,
            'addendum_key': 'iabs',
        },
    ),
    (
        (
            r'wire fraud warning',
            r'wire fraud alert',
            r'\btxr[-\s]?2517\b',
        ),
        {
            'kind': KIND_DISCLOSURE,
            'template_slug': 'wire-fraud-warning',
            'form_number': None,
            'label': 'Wire Fraud Warning',
            'scopes': (SCOPE_LISTING, SCOPE_CONTRACT),
            'base_confidence': 0.9,
            'addendum_key': 'wire_fraud_warning',
        },
    ),
    (
        (
            r't[-\s]?47(?:\.1)?\b',
            r'residential real property affidavit',
            r'declaration in lieu of affidavit',
        ),
        {
            'kind': KIND_DISCLOSURE,
            'template_slug': 't47-affidavit',
            'form_number': 'T-47.1',
            'label': 'T-47.1 Residential Real Property Affidavit',
            'scopes': (SCOPE_LISTING, SCOPE_CONTRACT),
            'base_confidence': 0.9,
            'addendum_key': 't47_affidavit',
        },
    ),
    (
        (
            r"seller'?s? estimated net proceeds",
            r'\bnet proceeds\b',
            r'\btxr[-\s]?2001\b',
        ),
        {
            'kind': KIND_OTHER,
            'template_slug': 'seller-net-proceeds',
            'form_number': None,
            'label': "Seller's Estimated Net Proceeds",
            'scopes': (SCOPE_LISTING,),
            'base_confidence': 0.9,
            'addendum_key': 'seller_net_proceeds',
        },
    ),
    (
        (
            r'special (?:taxing )?district',
            r'municipal utility district',
            r'\bnotice to purchaser of real property located in\b',
        ),
        {
            'kind': KIND_DISCLOSURE,
            'template_slug': 'special-tax-district-notice',
            'form_number': None,
            'label': 'Special Tax District Notice',
            'scopes': (SCOPE_LISTING, SCOPE_CONTRACT),
            'base_confidence': 0.88,
            'addendum_key': 'special_tax_district',
        },
    ),
)

_OFFER_TYPE_TO_KIND = {
    'offer_package': KIND_PURCHASE_CONTRACT,
    'buyer_offer': KIND_PURCHASE_CONTRACT,
    'seller_counter': KIND_PURCHASE_CONTRACT,
    'buyer_counter': KIND_PURCHASE_CONTRACT,
    'final_acceptance': KIND_PURCHASE_CONTRACT,
    'backup_acceptance': KIND_ADDENDUM,
    'sellers_disclosure': KIND_DISCLOSURE,
    'hoa_addendum': KIND_ADDENDUM,
    'pre_approval': KIND_PROOF_OF_FUNDS,
    'third_party_financing': KIND_ADDENDUM,
}


@dataclass(frozen=True)
class DocumentIdentity:
    """Stable typed result for document identity / routing hints."""

    kind: str = KIND_UNKNOWN
    template_slug: Optional[str] = None
    form_number: Optional[str] = None
    label: str = 'Unknown document'
    confidence: float = 0.0
    matched_signals: tuple[str, ...] = ()
    execution_state: str = EXEC_UNKNOWN
    possible_scopes: tuple[str, ...] = ()
    purchase_contract_type: Optional[str] = None
    addendum_key: Optional[str] = None
    offer_document_type: Optional[str] = None
    ambiguous: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= HIGH_CONFIDENCE and not self.ambiguous

    @property
    def is_medium_confidence(self) -> bool:
        return (
            MEDIUM_CONFIDENCE <= self.confidence < HIGH_CONFIDENCE
            and not self.ambiguous
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['matched_signals'] = list(self.matched_signals)
        data['possible_scopes'] = list(self.possible_scopes)
        data['is_high_confidence'] = self.is_high_confidence
        data['is_medium_confidence'] = self.is_medium_confidence
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'DocumentIdentity':
        if not data:
            return cls()
        return cls(
            kind=str(data.get('kind') or KIND_UNKNOWN),
            template_slug=data.get('template_slug'),
            form_number=data.get('form_number'),
            label=str(data.get('label') or 'Unknown document'),
            confidence=float(data.get('confidence') or 0.0),
            matched_signals=tuple(data.get('matched_signals') or ()),
            execution_state=str(data.get('execution_state') or EXEC_UNKNOWN),
            possible_scopes=tuple(data.get('possible_scopes') or ()),
            purchase_contract_type=data.get('purchase_contract_type'),
            addendum_key=data.get('addendum_key'),
            offer_document_type=data.get('offer_document_type'),
            ambiguous=bool(data.get('ambiguous')),
            extras=dict(data.get('extras') or {}),
        )


def normalize_pdf_text(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').lower()).strip()


def extract_pdf_text(file_bytes: bytes, *, max_pages: int = 24) -> str:
    """Extract selectable text from PDF pages. Fail soft to ''."""
    if not file_bytes:
        return ''
    try:
        import fitz

        chunks: list[str] = []
        doc = fitz.open(stream=file_bytes, filetype='pdf')
        try:
            for index, page in enumerate(doc):
                if index >= max_pages:
                    break
                chunks.append(page.get_text('text') or '')
        finally:
            doc.close()
        return '\n'.join(chunks)
    except Exception:
        return ''


# Primary document families — these compete for top-level identity.
_PRIMARY_KINDS = frozenset({
    KIND_LISTING_AGREEMENT,
    KIND_PURCHASE_CONTRACT,
    KIND_AMENDMENT,
})
# Supporting forms that commonly appear inside a contract/listing package.
_SUPPORTING_KINDS = frozenset({
    KIND_ADDENDUM,
    KIND_DISCLOSURE,
    KIND_PROOF_OF_FUNDS,
})


def refresh_execution_state(
    identity: DocumentIdentity,
    *,
    field_hints: dict[str, Any] | None = None,
) -> DocumentIdentity:
    """Update execution_state from signature observations without reclassifying."""
    exec_state, exec_signals = _infer_execution_state('', field_hints=field_hints)
    if exec_state == EXEC_UNKNOWN and not exec_signals:
        return identity
    merged = tuple(
        dict.fromkeys(list(identity.matched_signals) + list(exec_signals))
    )
    return DocumentIdentity(
        kind=identity.kind,
        template_slug=identity.template_slug,
        form_number=identity.form_number,
        label=identity.label,
        confidence=identity.confidence,
        matched_signals=merged,
        execution_state=exec_state,
        possible_scopes=identity.possible_scopes,
        purchase_contract_type=identity.purchase_contract_type,
        addendum_key=identity.addendum_key,
        offer_document_type=identity.offer_document_type,
        ambiguous=identity.ambiguous,
        extras=dict(identity.extras or {}),
    )


def _filename_signals(filename: str) -> list[str]:
    name = normalize_pdf_text(filename or '')
    if not name:
        return []
    signals: list[str] = []
    if 'txr' in name and '1101' in name:
        signals.append('filename:txr-1101')
    if 'listing' in name and 'agreement' in name:
        signals.append('filename:listing-agreement')
    if 'amendment' in name:
        signals.append('filename:amendment')
    if 'purchase' in name or 'contract' in name or 'offer' in name:
        signals.append('filename:contract-or-offer')
    return signals


def _coerce_signature_flag(value: Any) -> bool | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ('true', 'yes', 'y', '1'):
        return True
    if text in ('false', 'no', 'n', '0'):
        return False
    return None


def _infer_execution_state(
    text_norm: str,
    *,
    field_hints: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Execution state from signature observations only — never from form boilerplate.

    Blank promulgated forms contain words like "executed" and "effective date";
    those must not imply the package is signed.
    """
    del text_norm  # reserved for future non-boilerplate signals; unused by design
    hints = field_hints or {}

    buyer_sig = _coerce_signature_flag(hints.get('buyer_signature_present'))
    if buyer_sig is None:
        buyer_sig = _coerce_signature_flag(hints.get('buyer_signature_detected'))
    seller_sig = _coerce_signature_flag(hints.get('seller_signature_present'))
    if seller_sig is None:
        seller_sig = _coerce_signature_flag(hints.get('seller_signature_detected'))

    if buyer_sig is True and seller_sig is True:
        return EXEC_EXECUTED, ['signatures:buyer+seller']
    if buyer_sig is True and seller_sig is False:
        return EXEC_PARTY_SIGNED, ['signatures:buyer_only']
    if seller_sig is True and buyer_sig is False:
        return EXEC_PARTY_SIGNED, ['signatures:seller_only']
    if buyer_sig is False and seller_sig is False:
        return EXEC_DRAFT, ['signatures:both_blank']
    if buyer_sig is True and seller_sig is None:
        return EXEC_PARTY_SIGNED, ['signatures:buyer_only_unknown_seller']
    if seller_sig is True and buyer_sig is None:
        return EXEC_PARTY_SIGNED, ['signatures:seller_only_unknown_buyer']
    return EXEC_UNKNOWN, []


def identify_from_text(
    text: str,
    *,
    filename: str | None = None,
    field_hints: dict[str, Any] | None = None,
) -> DocumentIdentity:
    """Classify a document from extracted text (unit-test friendly)."""
    text_norm = normalize_pdf_text(text)
    filename_norm = normalize_pdf_text(filename or '')
    haystack = f'{text_norm} {filename_norm}'.strip()
    signals: list[str] = []
    candidates: list[DocumentIdentity] = []

    for patterns, meta in _FORM_RULES:
        matched = []
        for pattern in patterns:
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                matched.append(pattern)
        if not matched:
            continue
        conf = float(meta['base_confidence'])
        # Filename-only matches are weaker when body text is empty/short,
        # except clear form-number hits (e.g. TXR-1101 in the filename).
        if not text_norm and filename_norm:
            form_no = (meta.get('form_number') or '').lower().replace(' ', '')
            form_compact = re.sub(r'[^a-z0-9]+', '', form_no)
            file_compact = re.sub(r'[^a-z0-9]+', '', filename_norm)
            if form_compact and form_compact in file_compact:
                conf = max(conf, 0.88)
            else:
                conf = min(conf, 0.72)
        candidates.append(
            DocumentIdentity(
                kind=meta['kind'],
                template_slug=meta.get('template_slug'),
                form_number=meta.get('form_number'),
                label=meta['label'],
                confidence=conf,
                matched_signals=tuple(f'form:{p}' for p in matched[:3]),
                possible_scopes=tuple(meta.get('scopes') or ()),
                purchase_contract_type=meta.get('purchase_contract_type'),
                addendum_key=meta.get('addendum_key'),
            )
        )

    # Fall back to offer-package vocabulary — never the bare "buyer_offer"
    # default. That catch-all turns wire fraud / IABS / net sheets into fake
    # "Offer Contract" identities when no TREC 20 text is present.
    offer_type = infer_offer_document_type_from_text(
        text=text,
        filename=filename or '',
        explicit_type=None,
    )
    contract_text_present = bool(
        re.search(
            r'one to four family residential contract|'
            r'trec\s*(?:no\.?\s*)?20\b|'
            r'residential condominium contract|'
            r'new home contract|'
            r'farm and ranch contract|'
            r'unimproved property contract',
            text_norm,
        )
    )
    if offer_type == 'buyer_offer' and not contract_text_present:
        offer_type = None
    if offer_type == 'offer_package' and not contract_text_present:
        offer_type = None
    if offer_type and offer_type in OFFER_DOCUMENT_TYPES:
        kind = _OFFER_TYPE_TO_KIND.get(offer_type, KIND_OTHER)
        cfg = OFFER_DOCUMENT_TYPES[offer_type]
        # Only promote offer-classifier hits when form rules found nothing strong.
        if not candidates or max(c.confidence for c in candidates) < HIGH_CONFIDENCE:
            offer_conf = 0.8 if text_norm else 0.55
            candidates.append(
                DocumentIdentity(
                    kind=kind,
                    template_slug=cfg.get('template_slug'),
                    form_number=None,
                    label=cfg.get('label') or offer_type,
                    confidence=offer_conf,
                    matched_signals=('offer_classifier:' + offer_type,),
                    possible_scopes=(
                        (SCOPE_OFFER, SCOPE_CONTRACT)
                        if kind == KIND_PURCHASE_CONTRACT
                        else (SCOPE_OFFER,)
                    ),
                    offer_document_type=offer_type,
                    addendum_key=(
                        offer_type
                        if kind in (KIND_ADDENDUM, KIND_DISCLOSURE, KIND_PROOF_OF_FUNDS)
                        else None
                    ),
                )
            )

    signals.extend(_filename_signals(filename or ''))

    if not candidates:
        exec_state, exec_signals = _infer_execution_state(
            text_norm, field_hints=field_hints,
        )
        return DocumentIdentity(
            kind=KIND_UNKNOWN,
            label='Unknown document',
            confidence=0.2 if text_norm else 0.0,
            matched_signals=tuple(signals + exec_signals),
            execution_state=exec_state,
            possible_scopes=(),
            ambiguous=True,
        )

    # Deduplicate by (kind, template_slug), keep strongest of each.
    by_key: dict[tuple[str, str | None], DocumentIdentity] = {}
    for cand in candidates:
        key = (cand.kind, cand.template_slug)
        prior = by_key.get(key)
        if prior is None or cand.confidence > prior.confidence:
            by_key[key] = cand
    unique = list(by_key.values())

    primary = [c for c in unique if c.kind in _PRIMARY_KINDS]
    supporting = [c for c in unique if c.kind in _SUPPORTING_KINDS]
    primary.sort(key=lambda c: c.confidence, reverse=True)
    supporting.sort(key=lambda c: c.confidence, reverse=True)

    # A strong primary contract/listing/amendment outranks embedded addenda.
    if primary:
        top = primary[0]
        ambiguous = False
        # Competing primary identities (e.g. listing vs purchase, or amendment vs contract)
        if len(primary) > 1:
            second = primary[1]
            if (
                top.kind != second.kind
                and abs(top.confidence - second.confidence) < 0.08
                and second.confidence >= MEDIUM_CONFIDENCE
            ):
                ambiguous = True
                signals.append(f'ambiguous:{top.kind}|{second.kind}')
    else:
        # No primary — supporting form stands alone.
        unique.sort(key=lambda c: c.confidence, reverse=True)
        top = unique[0]
        ambiguous = False
        if len(unique) > 1:
            second = unique[1]
            if (
                top.kind != second.kind
                and abs(top.confidence - second.confidence) < 0.08
                and second.confidence >= MEDIUM_CONFIDENCE
            ):
                ambiguous = True
                signals.append(f'ambiguous:{top.kind}|{second.kind}')
        supporting = [c for c in unique[1:] if c.kind in _SUPPORTING_KINDS]

    embedded = [
        {
            'kind': c.kind,
            'template_slug': c.template_slug,
            'form_number': c.form_number,
            'label': c.label,
            'addendum_key': c.addendum_key,
            'confidence': c.confidence,
        }
        for c in supporting
        if c.kind != top.kind or c.template_slug != top.template_slug
    ]

    # TXR-1101 Section 19 always names IABS, Seller's Disclosure, lead paint,
    # flood, MUD, etc. as checkboxes — those keyword hits are form boilerplate,
    # not proof those documents are physically in the PDF. Real listing packages
    # are split from AI detected_documents page ranges, not from this list.
    if top.kind == KIND_LISTING_AGREEMENT and top.confidence >= HIGH_CONFIDENCE:
        embedded = []

    exec_state, exec_signals = _infer_execution_state(
        text_norm, field_hints=field_hints,
    )

    merged_signals = tuple(
        dict.fromkeys(list(top.matched_signals) + signals + exec_signals)
    )
    extras: dict[str, Any] = {
        'candidate_kinds': [c.kind for c in ([top] + supporting)[:6]],
        'embedded_components': embedded,
    }
    if primary and len(primary) > 1:
        extras['primary_candidates'] = [
            {'kind': c.kind, 'template_slug': c.template_slug, 'confidence': c.confidence}
            for c in primary[:3]
        ]

    return DocumentIdentity(
        kind=top.kind,
        template_slug=top.template_slug,
        form_number=top.form_number,
        label=top.label,
        confidence=top.confidence,
        matched_signals=merged_signals,
        execution_state=exec_state,
        possible_scopes=top.possible_scopes,
        purchase_contract_type=top.purchase_contract_type,
        addendum_key=top.addendum_key,
        offer_document_type=top.offer_document_type or (
            'buyer_offer' if top.kind == KIND_PURCHASE_CONTRACT else None
        ),
        ambiguous=ambiguous,
        extras=extras,
    )


def identify_from_pdf(
    file_bytes: bytes,
    *,
    filename: str | None = None,
    field_hints: dict[str, Any] | None = None,
) -> DocumentIdentity:
    """Classify a PDF using selectable text, then filename fallback."""
    text = extract_pdf_text(file_bytes)
    return identify_from_text(
        text,
        filename=filename,
        field_hints=field_hints,
    )


def identity_for_slug(template_slug: str | None) -> DocumentIdentity | None:
    """Map a known template slug back to a coarse identity (no text)."""
    slug = (template_slug or '').strip().lower()
    if not slug or slug in GENERIC_TEMPLATE_SLUGS:
        return None
    if slug == 'listing-agreement':
        return DocumentIdentity(
            kind=KIND_LISTING_AGREEMENT,
            template_slug=slug,
            form_number='TXR-1101',
            label='Residential Real Estate Listing Agreement',
            confidence=0.7,
            matched_signals=('slug:listing-agreement',),
            possible_scopes=(SCOPE_LISTING,),
        )
    if slug == 'amendment':
        return DocumentIdentity(
            kind=KIND_AMENDMENT,
            template_slug=slug,
            label='Amendment to Contract',
            confidence=0.7,
            matched_signals=('slug:amendment',),
            possible_scopes=(SCOPE_AMENDMENT, SCOPE_CONTRACT),
        )
    if 'contract' in slug or slug.startswith('seller-offer') or slug.startswith('seller-accepted'):
        return DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug=slug,
            label='Purchase Contract',
            confidence=0.65,
            matched_signals=(f'slug:{slug}',),
            possible_scopes=(SCOPE_OFFER, SCOPE_CONTRACT),
            offer_document_type='buyer_offer',
        )
    return None


def should_content_classify_slug(template_slug: str | None) -> bool:
    """True when upload slug is generic and content identity should drive schema."""
    slug = (template_slug or '').strip().lower()
    if slug in GENERIC_TEMPLATE_SLUGS:
        return True
    # Generated custom placeholders: custom-<uuid> / custom_<uuid>
    if slug.startswith('custom-') or slug.startswith('custom_'):
        return True
    return False


def resolve_upload_identity_for_extraction(
    *,
    template_slug: str | None,
    file_bytes: bytes,
    filename: str | None = None,
    transaction_side: str | None = None,
    is_offer_scoped: bool = False,
) -> tuple[Optional[str], DocumentIdentity, bool]:
    """Pick extraction schema slug from content when the upload slug is generic.

    Returns (schema_slug, identity, retagged).
    Retags only on high-confidence, safe cases. Never auto-creates offers here.
    Preserves the original user-facing template_name at the caller.
    """
    identity = identify_from_pdf(file_bytes, filename=filename)
    original = (template_slug or '').strip().lower()

    # Explicit offer/contract schemas from the upload route stay put.
    # Generic slots (completed/external/custom) still need content-based schemas
    # so TREC-20 / TPF / HOA / compensation forms extract their real fields.
    if is_offer_scoped and original and not should_content_classify_slug(original):
        return original, identity, False

    if not should_content_classify_slug(original) and original not in {
        'completed', 'external',
    }:
        # Still return identity metadata for persistence, but do not retag
        # explicitly chosen slugs.
        return original or identity.template_slug, identity, False

    if not identity.is_high_confidence:
        return original or None, identity, False

    side = (transaction_side or '').strip().lower()
    if identity.kind == KIND_LISTING_AGREEMENT and side in ('seller', '', None):
        # High-confidence listing agreement on seller (or unknown side) → listing schema.
        if side == 'buyer':
            return original or None, identity, False
        return 'listing-agreement', identity, True

    if identity.kind == KIND_AMENDMENT:
        return 'amendment', identity, True

    if identity.kind == KIND_PURCHASE_CONTRACT:
        # Use the purchase/offer schema for extraction. Retag generic offer-package
        # uploads so sync/UI see the real form; leave non-offer generic uploads
        # unretagged so confirmation still owns offer creation.
        schema_slug = identity.template_slug or 'seller-offer-contract'
        if is_offer_scoped and should_content_classify_slug(original):
            return schema_slug, identity, True
        return schema_slug, identity, False

    if identity.kind in (KIND_ADDENDUM, KIND_DISCLOSURE, KIND_PROOF_OF_FUNDS):
        if identity.template_slug:
            return identity.template_slug, identity, True

    return original or identity.template_slug, identity, False


# AI detected_documents labels → identity kind / slug / form metadata.
# Regex may propose a first-pass identity for schema selection; after extraction,
# detected_documents is the package source of truth.
_AI_SEGMENT_TO_IDENTITY: dict[str, dict[str, Any]] = {
    'listing_agreement': {
        'kind': KIND_LISTING_AGREEMENT,
        'template_slug': 'listing-agreement',
        'form_number': 'TXR-1101',
        'label': 'Residential Real Estate Listing Agreement',
        'scopes': (SCOPE_LISTING,),
    },
    'iabs': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'iabs',
        'label': 'Information About Brokerage Services',
        'addendum_key': 'iabs',
        'scopes': (SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'sellers_disclosure': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'sellers-disclosure',
        'form_number': 'TXR-1406',
        'label': "Seller's Disclosure Notice",
        'addendum_key': 'sellers_disclosure',
        'scopes': (SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'seller_disclosure': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'sellers-disclosure',
        'form_number': 'TXR-1406',
        'label': "Seller's Disclosure Notice",
        'addendum_key': 'sellers_disclosure',
        'scopes': (SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'lead_based_paint': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'lead-paint',
        'label': 'Lead-Based Paint Disclosure',
        'addendum_key': 'lead_based_paint',
        'scopes': (SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'lead_paint': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'lead-paint',
        'label': 'Lead-Based Paint Disclosure',
        'addendum_key': 'lead_based_paint',
        'scopes': (SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'hoa_addendum': {
        'kind': KIND_ADDENDUM,
        'template_slug': 'hoa-addendum',
        'form_number': 'TREC 36',
        'label': 'HOA / POA Addendum',
        'addendum_key': 'hoa',
        'scopes': (SCOPE_OFFER, SCOPE_CONTRACT, SCOPE_LISTING),
    },
    'third_party_financing': {
        'kind': KIND_ADDENDUM,
        'template_slug': 'third-party-financing-addendum',
        'form_number': 'TREC 40',
        'label': 'Third Party Financing Addendum',
        'addendum_key': 'third_party_financing',
        'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'third_party_financing_addendum': {
        'kind': KIND_ADDENDUM,
        'template_slug': 'third-party-financing-addendum',
        'form_number': 'TREC 40',
        'label': 'Third Party Financing Addendum',
        'addendum_key': 'third_party_financing',
        'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'appraisal_termination': {
        'kind': KIND_ADDENDUM,
        'template_slug': 'appraisal-termination-addendum',
        'form_number': 'TREC 49',
        'label': "Addendum Concerning Right to Terminate Due to Lender's Appraisal",
        'addendum_key': 'appraisal_termination',
        'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'appraisal_termination_addendum': {
        'kind': KIND_ADDENDUM,
        'template_slug': 'appraisal-termination-addendum',
        'form_number': 'TREC 49',
        'label': "Addendum Concerning Right to Terminate Due to Lender's Appraisal",
        'addendum_key': 'appraisal_termination',
        'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'wire_fraud_warning': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'wire-fraud-warning',
        'label': 'Wire Fraud Warning',
        'addendum_key': 'wire_fraud_warning',
        'scopes': (SCOPE_LISTING, SCOPE_CONTRACT),
    },
    'flood_hazard': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'flood-hazard',
        'label': 'Flood Hazard Information',
        'addendum_key': 'flood_hazard',
        'scopes': (SCOPE_LISTING, SCOPE_CONTRACT),
    },
    't47_affidavit': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 't47-affidavit',
        'label': 'T-47 Residential Real Property Affidavit',
        'addendum_key': 't47_affidavit',
        'scopes': (SCOPE_LISTING, SCOPE_CONTRACT),
    },
    'special_tax_district_notice': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'special-tax-district-notice',
        'label': 'Special Tax District Notice',
        'addendum_key': 'special_tax_district',
        'scopes': (SCOPE_LISTING, SCOPE_CONTRACT),
    },
    'sewer_facility': {
        'kind': KIND_DISCLOSURE,
        'template_slug': 'sewer-facility',
        'label': 'On-Site Sewer Facility Notice',
        'addendum_key': 'sewer_facility',
        'scopes': (SCOPE_LISTING, SCOPE_CONTRACT),
    },
    'referral_agreement': {
        'kind': KIND_OTHER,
        'template_slug': 'referral-agreement',
        'label': 'Referral Agreement',
        'addendum_key': 'referral_agreement',
        'scopes': (SCOPE_LISTING,),
    },
    'residential_contract': {
        'kind': KIND_PURCHASE_CONTRACT,
        'template_slug': 'one-to-four-family-contract',
        'form_number': 'TREC 20',
        'label': 'One to Four Family Residential Contract',
        'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
        'purchase_contract_type': 'resale_one_to_four',
    },
    'buyer_offer': {
        'kind': KIND_PURCHASE_CONTRACT,
        'template_slug': 'seller-offer-contract',
        'label': 'Purchase Contract',
        'scopes': (SCOPE_OFFER, SCOPE_CONTRACT),
    },
    'amendment': {
        'kind': KIND_AMENDMENT,
        'template_slug': 'amendment',
        'form_number': 'TREC 39',
        'label': 'Amendment to Contract',
        'scopes': (SCOPE_AMENDMENT, SCOPE_CONTRACT),
    },
}


def template_slug_for_detected_type(document_type: str | None) -> Optional[str]:
    """Map an AI ``detected_documents`` label to a canonical template slug."""
    key = (document_type or '').strip().lower()
    if not key:
        return None
    meta = _AI_SEGMENT_TO_IDENTITY.get(key)
    if meta and meta.get('template_slug'):
        return meta['template_slug']
    return key.replace('_', '-')


def _normalize_detected_segments(detected: Any) -> list[dict[str, Any]]:
    if not isinstance(detected, list):
        return []
    out: list[dict[str, Any]] = []
    for item in detected:
        if not isinstance(item, dict):
            continue
        seg_type = (item.get('document_type') or item.get('type') or '').strip().lower()
        if not seg_type:
            continue
        out.append({
            'document_type': seg_type,
            'start_page': item.get('start_page'),
            'end_page': item.get('end_page'),
            'title': item.get('title') or item.get('label'),
        })
    return out


def apply_ai_package_authority(
    identity: DocumentIdentity | None,
    field_data: dict[str, Any] | None,
) -> DocumentIdentity:
    """Make AI ``detected_documents`` the package / identity source of truth.

    Regex identity is a fast first pass for schema selection. Once extraction
    returns ``detected_documents``, that list owns:
      - which supporting forms are actually in the PDF
      - embedded_components used by the package UI
      - primary kind when the first segment is a known primary form

    Checklist / boilerplate keyword hits from regex are discarded.
    """
    base = identity or DocumentIdentity()
    data = field_data or {}
    extras = dict(base.extras or {})

    if 'detected_documents' not in data:
        # AI package detection did not run. Never promote listing checklist
        # keyword hits into "detected in package".
        if base.kind == KIND_LISTING_AGREEMENT and base.confidence >= HIGH_CONFIDENCE:
            extras['embedded_components'] = []

        # Extraction often returns a top-level document_type without
        # detected_documents (tests and thin AI payloads). Prefer that over a
        # weak/ambiguous regex identity so approve isn't blocked on stubs.
        doc_type = (
            str(
                data.get('document_type')
                or data.get('document_classification')
                or ''
            ).strip().lower()
        )
        type_meta = _AI_SEGMENT_TO_IDENTITY.get(doc_type) if doc_type else None
        weak_base = (
            base.kind in (KIND_UNKNOWN, KIND_OTHER, '')
            or base.ambiguous
            or base.confidence < HIGH_CONFIDENCE
        )
        if type_meta and weak_base:
            extras['package_authority'] = 'ai_document_type'
            return DocumentIdentity(
                kind=type_meta['kind'],
                template_slug=type_meta.get('template_slug') or base.template_slug,
                form_number=type_meta.get('form_number') or base.form_number,
                label=type_meta.get('label') or base.label,
                confidence=max(base.confidence, 0.9),
                matched_signals=tuple(
                    dict.fromkeys(
                        list(base.matched_signals) + ['authority:ai_document_type', f'type:{doc_type}']
                    )
                ),
                execution_state=base.execution_state,
                possible_scopes=tuple(type_meta.get('scopes') or base.possible_scopes),
                purchase_contract_type=(
                    type_meta.get('purchase_contract_type')
                    or base.purchase_contract_type
                ),
                addendum_key=type_meta.get('addendum_key') or base.addendum_key,
                offer_document_type=(
                    base.offer_document_type
                    or (
                        'buyer_offer'
                        if type_meta['kind'] == KIND_PURCHASE_CONTRACT
                        else None
                    )
                ),
                ambiguous=False,
                extras=extras,
            )

        extras['package_authority'] = 'regex_pending_ai'
        return DocumentIdentity(
            kind=base.kind,
            template_slug=base.template_slug,
            form_number=base.form_number,
            label=base.label,
            confidence=base.confidence,
            matched_signals=base.matched_signals + ('authority:regex_pending_ai',),
            execution_state=base.execution_state,
            possible_scopes=base.possible_scopes,
            purchase_contract_type=base.purchase_contract_type,
            addendum_key=base.addendum_key,
            offer_document_type=base.offer_document_type,
            ambiguous=base.ambiguous,
            extras=extras,
        )

    segments = _normalize_detected_segments(data.get('detected_documents'))
    extras['package_authority'] = 'ai_detected_documents'
    extras['ai_detected_documents'] = segments

    if not segments:
        extras['embedded_components'] = []
        return DocumentIdentity(
            kind=base.kind,
            template_slug=base.template_slug,
            form_number=base.form_number,
            label=base.label,
            confidence=base.confidence,
            matched_signals=base.matched_signals + ('authority:ai_empty_package',),
            execution_state=base.execution_state,
            possible_scopes=base.possible_scopes,
            purchase_contract_type=base.purchase_contract_type,
            addendum_key=base.addendum_key,
            offer_document_type=base.offer_document_type,
            ambiguous=base.ambiguous,
            extras=extras,
        )

    primary_meta = None
    embedded: list[dict[str, Any]] = []
    for index, seg in enumerate(segments):
        seg_type = seg['document_type']
        meta = _AI_SEGMENT_TO_IDENTITY.get(seg_type)
        if meta is None:
            if index == 0:
                continue
            embedded.append({
                'kind': KIND_OTHER,
                'template_slug': seg_type.replace('_', '-'),
                'form_number': None,
                'label': seg.get('title') or seg_type.replace('_', ' ').title(),
                'addendum_key': seg_type,
                'confidence': 0.9,
                'start_page': seg.get('start_page'),
                'end_page': seg.get('end_page'),
                'source': 'ai_detected_documents',
            })
            continue
        payload = {
            'kind': meta['kind'],
            'template_slug': meta.get('template_slug'),
            'form_number': meta.get('form_number'),
            'label': seg.get('title') or meta.get('label'),
            'addendum_key': meta.get('addendum_key'),
            'confidence': 0.95,
            'start_page': seg.get('start_page'),
            'end_page': seg.get('end_page'),
            'source': 'ai_detected_documents',
        }
        if meta['kind'] in _PRIMARY_KINDS:
            # First primary segment owns top-level identity; later primaries
            # are package noise (e.g. duplicate covers), not embeds.
            if index == 0 or primary_meta is None:
                primary_meta = meta
            continue
        embedded.append(payload)

    extras['embedded_components'] = embedded

    if primary_meta is not None:
        return DocumentIdentity(
            kind=primary_meta['kind'],
            template_slug=primary_meta.get('template_slug') or base.template_slug,
            form_number=primary_meta.get('form_number') or base.form_number,
            label=primary_meta.get('label') or base.label,
            confidence=max(base.confidence, 0.95),
            matched_signals=tuple(
                dict.fromkeys(
                    list(base.matched_signals) + ['authority:ai_detected_documents']
                )
            ),
            execution_state=base.execution_state,
            possible_scopes=tuple(primary_meta.get('scopes') or base.possible_scopes),
            purchase_contract_type=(
                primary_meta.get('purchase_contract_type')
                or base.purchase_contract_type
            ),
            addendum_key=base.addendum_key,
            offer_document_type=base.offer_document_type,
            ambiguous=False,
            extras=extras,
        )

    return DocumentIdentity(
        kind=base.kind,
        template_slug=base.template_slug,
        form_number=base.form_number,
        label=base.label,
        confidence=base.confidence,
        matched_signals=tuple(
            dict.fromkeys(
                list(base.matched_signals) + ['authority:ai_detected_documents']
            )
        ),
        execution_state=base.execution_state,
        possible_scopes=base.possible_scopes,
        purchase_contract_type=base.purchase_contract_type,
        addendum_key=base.addendum_key,
        offer_document_type=base.offer_document_type,
        ambiguous=base.ambiguous,
        extras=extras,
    )


def persist_identity_on_field_data(
    field_data: dict[str, Any] | None,
    identity: DocumentIdentity,
    *,
    retagged: bool = False,
    original_slug: str | None = None,
) -> dict[str, Any]:
    """Attach identity metadata under reserved keys in field_data.

    When AI ``detected_documents`` is present, package membership is rewritten
    from that list before persistence so regex checklist hits cannot win.
    """
    data = dict(field_data or {})
    authoritative = apply_ai_package_authority(identity, data)
    data['_document_identity'] = authoritative.to_dict()
    data['_document_identity']['retagged'] = retagged
    if original_slug is not None:
        data['_document_identity']['original_template_slug'] = original_slug
    return data
