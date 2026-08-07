"""Deterministic expected-document descriptors from terms + identities.

Produces scope-aware expected slots for listing, offer, and contract packages.
Applicability is encoded with reason/source — this never claims a Texas form is
universally legally required.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional

from services.document_identity import (
    KIND_LISTING_AGREEMENT,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
)

APPLICABLE = 'applicable'
OPTIONAL = 'optional'
NOT_APPLICABLE = 'not_applicable'
UNKNOWN = 'unknown'
POST_EXECUTION = 'post_execution_only'

SCOPE_LISTING = 'listing'
SCOPE_OFFER = 'offer'
SCOPE_CONTRACT = 'contract'

# Canonical intake / registry slug for lead-paint disclosure.
LEAD_PAINT_SLUG = 'lead-paint'


@dataclass(frozen=True)
class ExpectedDocument:
    """One expected document slot (descriptor only — not a legal mandate)."""

    key: str
    label: str
    scope: str
    template_slug: Optional[str]
    applicability: str
    reason: str
    source: str
    form_number: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _truthy(value: Any) -> bool | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ('true', 'yes', 'y', '1', 'is'):
        return True
    if text in ('false', 'no', 'n', '0', 'is not'):
        return False
    return None


def _first_present(terms: dict[str, Any], *keys: str) -> Any:
    """Return the first explicitly present key value (preserves False/0)."""
    for key in keys:
        if key in terms:
            return terms[key]
    return None


# Intake keys that decide listing-package applicability in Texas seller files.
_LISTING_INTAKE_TERM_KEYS = (
    'built_before_1978',
    'has_hoa',
    'hoa_applicable',
    'flood_hazard',
    'special_districts',
    'has_septic',
    'referral_fee',
    'has_survey',
    'seller_disclosure_required',
    'sellers_disclosure_required',
    'lead_based_paint_required',
)

# Ownership paths where TXR-1406 is not treated as the default expectation.
_DISCLOSURE_EXEMPT_OWNERSHIP = frozenset({
    'reo',
    'foreclosure',
    'bankruptcy',
    'builder',
    'new_construction',
})


def merge_listing_package_terms(
    *,
    listing_field_data: dict[str, Any] | None = None,
    intake_data: dict[str, Any] | None = None,
    ownership_status: str | None = None,
) -> dict[str, Any]:
    """Build listing-package terms from extraction + questionnaire.

    Questionnaire answers are human-confirmed and win over extraction gaps for
    applicability keys. For conventional Texas residential listings, Seller's
    Disclosure (TXR-1406 / Prop. Code §5.008) is expected once intake exists
    unless ownership is an exemption path (REO, builder, etc.).
    """
    terms: dict[str, Any] = {}
    if isinstance(listing_field_data, dict):
        terms.update(
            {
                key: value
                for key, value in listing_field_data.items()
                if not str(key).startswith('_')
            }
        )

    intake = intake_data if isinstance(intake_data, dict) else {}
    for key in _LISTING_INTAKE_TERM_KEYS:
        if key in intake and intake[key] not in (None, ''):
            terms[key] = intake[key]

    ownership = (ownership_status or 'conventional').strip().lower()
    disclosure_set = (
        'seller_disclosure_required' in terms
        or 'sellers_disclosure_required' in terms
    )
    if (
        intake
        and not disclosure_set
        and ownership not in _DISCLOSURE_EXEMPT_OWNERSHIP
    ):
        # Conventional / short-sale residential listings answer this via the
        # questionnaire pack (always include TXR-1406), not a free-text field.
        terms['seller_disclosure_required'] = True

    return terms


def _financing_suggests_tpf(terms: dict[str, Any]) -> bool | None:
    financing = str(
        _first_present(terms, 'financing_type', 'loan_type') or ''
    ).strip().lower()
    if not financing:
        return None
    if financing in ('cash', 'none', 'n/a', 'na'):
        return False
    if any(
        token in financing
        for token in ('conventional', 'fha', 'va', 'usda', 'financing', 'mortgage')
    ):
        return True
    return None


def _purchase_form_from_identity(
    identities: Iterable[DocumentIdentity] | None,
) -> tuple[Optional[str], Optional[str]]:
    """Return (template_slug, form_number) from a purchase-contract identity."""
    for identity in identities or ():
        if identity.kind == KIND_PURCHASE_CONTRACT:
            return identity.template_slug, identity.form_number
    return None, None


def expected_documents_for_context(
    *,
    scope: str,
    terms: dict[str, Any] | None = None,
    identities: Iterable[DocumentIdentity] | None = None,
    has_controlling_contract: bool = False,
) -> list[ExpectedDocument]:
    """Build expected document descriptors for a package scope.

    ``terms`` may be listing-agreement or purchase-contract extracted fields.
    ``identities`` are documents already recognized in the package.
    """
    terms = dict(terms or {})
    identity_list = list(identities or [])
    seen_kinds = {i.kind for i in identity_list}
    seen_keys = {
        (i.addendum_key or i.template_slug or i.kind)
        for i in identity_list
    }

    def _mark_seen(kind: Any, key: Any) -> None:
        if kind:
            seen_kinds.add(kind)
        if key:
            seen_keys.add(key)

    # AI package map wins when present on extracted terms or identity extras.
    detected = terms.get('detected_documents')
    ai_authority = isinstance(detected, list) or any(
        (i.extras or {}).get('package_authority') == 'ai_detected_documents'
        for i in identity_list
    )
    if ai_authority:
        if isinstance(detected, list):
            for item in detected:
                if not isinstance(item, dict):
                    continue
                seg = (
                    item.get('document_type') or item.get('type') or ''
                ).strip().lower()
                if not seg:
                    continue
                _mark_seen(seg, seg.replace('_', '-'))
        for identity in identity_list:
            if (identity.extras or {}).get('package_authority') != 'ai_detected_documents':
                continue
            for component in (identity.extras or {}).get('embedded_components') or []:
                if isinstance(component, dict):
                    _mark_seen(
                        component.get('kind'),
                        component.get('addendum_key')
                        or component.get('template_slug')
                        or component.get('kind'),
                    )
    else:
        # Pre-extraction / legacy: fold regex identity embeds.
        for identity in identity_list:
            for component in (identity.extras or {}).get('embedded_components') or []:
                if isinstance(component, dict):
                    _mark_seen(
                        component.get('kind'),
                        component.get('addendum_key')
                        or component.get('template_slug')
                        or component.get('kind'),
                    )

    scope_norm = (scope or '').strip().lower()
    out: list[ExpectedDocument] = []

    def add(item: ExpectedDocument) -> None:
        out.append(item)

    if scope_norm == SCOPE_LISTING:
        add(ExpectedDocument(
            key='listing_agreement',
            label='Listing Agreement',
            scope=SCOPE_LISTING,
            template_slug='listing-agreement',
            applicability=APPLICABLE if KIND_LISTING_AGREEMENT not in seen_kinds else OPTIONAL,
            reason='Listing package is anchored by an exclusive listing agreement when one is in use.',
            source='listing_pack',
            form_number='TXR-1101',
        ))
        hoa = _truthy(_first_present(terms, 'has_hoa', 'hoa_applicable'))
        add(ExpectedDocument(
            key='hoa_addendum',
            label='HOA / POA Information',
            scope=SCOPE_LISTING,
            template_slug='hoa-addendum',
            applicability=(
                APPLICABLE if hoa is True
                else NOT_APPLICABLE if hoa is False
                else UNKNOWN
            ),
            reason=(
                'Listing terms indicate a mandatory owners association.'
                if hoa is True
                else 'Listing terms indicate no mandatory owners association.'
                if hoa is False
                else 'HOA applicability not shown in extracted listing terms.'
            ),
            source='listing_terms',
            form_number='TREC 36',
        ))
        disclosure = _truthy(
            _first_present(terms, 'seller_disclosure_required', 'sellers_disclosure_required')
        )
        add(ExpectedDocument(
            key='sellers_disclosure',
            label="Seller's Disclosure Notice",
            scope=SCOPE_LISTING,
            template_slug='sellers-disclosure',
            applicability=(
                APPLICABLE if disclosure is True
                else NOT_APPLICABLE if disclosure is False
                else UNKNOWN
            ),
            reason=(
                "Seller's Disclosure Notice (TXR-1406) is expected for this "
                'residential listing (Texas Property Code §5.008).'
                if disclosure is True
                else 'Seller disclosure marked not required for this ownership path.'
                if disclosure is False
                else 'Seller disclosure applicability not shown; confirm for this property.'
            ),
            source='listing_terms',
            form_number='TXR-1406',
        ))
        lead = _truthy(
            _first_present(terms, 'built_before_1978', 'lead_based_paint_required')
        )
        add(ExpectedDocument(
            key='lead_based_paint',
            label='Lead-Based Paint Disclosure',
            scope=SCOPE_LISTING,
            template_slug=LEAD_PAINT_SLUG,
            applicability=(
                APPLICABLE if lead is True
                else NOT_APPLICABLE if lead is False
                else UNKNOWN
            ),
            reason=(
                'Property built before 1978 — federal lead-based paint disclosure applies.'
                if lead is True
                else 'Property not built before 1978 — lead-based paint disclosure does not apply.'
                if lead is False
                else 'Year-built / lead-paint applicability not shown.'
            ),
            source='listing_terms',
        ))
        return out

    # Offer + contract share most purchase addenda expectations.
    if scope_norm in (SCOPE_OFFER, SCOPE_CONTRACT):
        purchase_slug, purchase_form = _purchase_form_from_identity(identity_list)
        add(ExpectedDocument(
            key='purchase_contract',
            label='Residential Purchase Contract',
            scope=scope_norm,
            template_slug=purchase_slug or 'purchase-contract',
            applicability=APPLICABLE if KIND_PURCHASE_CONTRACT not in seen_kinds else OPTIONAL,
            reason='Offer/contract package is anchored by a residential purchase contract form.',
            source='intake_schema',
            form_number=purchase_form,
        ))

        tpf = _truthy(_first_present(terms, 'third_party_financing'))
        if tpf is None:
            tpf = _financing_suggests_tpf(terms)
        add(ExpectedDocument(
            key='third_party_financing',
            label='Third Party Financing Addendum',
            scope=scope_norm,
            template_slug='third-party-financing-addendum',
            applicability=(
                APPLICABLE if tpf is True
                else NOT_APPLICABLE if tpf is False
                else UNKNOWN
            ),
            reason=(
                'Financing type indicates third-party financing may apply.'
                if tpf is True
                else 'Cash / no third-party financing indicated.'
                if tpf is False
                else 'Financing type not shown; TPF applicability unknown.'
            ),
            source='contract_terms',
            form_number='TREC 40',
        ))

        hoa = _truthy(_first_present(terms, 'hoa_applicable', 'has_hoa'))
        add(ExpectedDocument(
            key='hoa_addendum',
            label='HOA / POA Addendum',
            scope=scope_norm,
            template_slug='hoa-addendum',
            applicability=(
                APPLICABLE if hoa is True
                else NOT_APPLICABLE if hoa is False
                else UNKNOWN
            ),
            reason=(
                'Contract terms indicate a mandatory owners association.'
                if hoa is True
                else 'Contract terms indicate no mandatory owners association.'
                if hoa is False
                else 'HOA applicability not shown in extracted contract terms.'
            ),
            source='contract_terms',
            form_number='TREC 36',
        ))

        disclosure_req = _truthy(
            _first_present(terms, 'seller_disclosure_required', 'sellers_disclosure_required')
        )
        add(ExpectedDocument(
            key='sellers_disclosure',
            label="Seller's Disclosure Notice",
            scope=scope_norm,
            template_slug='sellers-disclosure',
            applicability=(
                APPLICABLE if disclosure_req is True
                else NOT_APPLICABLE if disclosure_req is False
                else UNKNOWN
            ),
            reason=(
                'Seller disclosure flagged as required in extracted terms.'
                if disclosure_req is True
                else 'Seller disclosure flagged as not required in extracted terms.'
                if disclosure_req is False
                else 'Seller disclosure requirement not shown in extracted terms.'
            ),
            source='contract_terms',
            form_number='TXR-1406',
        ))

        lead = _truthy(
            _first_present(terms, 'lead_based_paint_required', 'built_before_1978')
        )
        add(ExpectedDocument(
            key='lead_based_paint',
            label='Lead-Based Paint Disclosure',
            scope=scope_norm,
            template_slug=LEAD_PAINT_SLUG,
            applicability=(
                APPLICABLE if lead is True
                else NOT_APPLICABLE if lead is False
                else UNKNOWN
            ),
            reason=(
                'Lead-based paint disclosure indicated by extracted terms.'
                if lead is True
                else 'Lead-based paint indicated as not applicable.'
                if lead is False
                else 'Lead-paint applicability not shown.'
            ),
            source='contract_terms',
        ))

        backup = _truthy(_first_present(terms, 'backup_contract', 'is_backup'))
        if backup is True or 'backup' in seen_keys:
            add(ExpectedDocument(
                key='backup_addendum',
                label='Addendum for Back-Up Contract',
                scope=scope_norm,
                template_slug='seller-backup-addendum',
                applicability=APPLICABLE,
                reason='Backup contract indicated for this offer.',
                source='contract_terms',
            ))
        else:
            add(ExpectedDocument(
                key='backup_addendum',
                label='Addendum for Back-Up Contract',
                scope=scope_norm,
                template_slug='seller-backup-addendum',
                applicability=OPTIONAL,
                reason='Only applicable when the offer is accepted as backup.',
                source='offer_lifecycle',
            ))

        # Cash often needs proof of funds; financed offers may use pre-approval.
        # Keep OPTIONAL unless stronger explicit terms appear later.
        financing = _financing_suggests_tpf(terms)
        if financing is False:
            pof_reason = (
                'Cash offers commonly include proof of funds; not treated as universally required.'
            )
        elif financing is True:
            pof_reason = (
                'Financed offers commonly include a pre-approval letter; not treated as universally required.'
            )
        else:
            pof_reason = (
                'Pre-approval or proof of funds is often requested with an offer; '
                'not treated as universally required.'
            )
        add(ExpectedDocument(
            key='pre_approval_or_pof',
            label='Pre-Approval / Proof of Funds',
            scope=SCOPE_OFFER if scope_norm == SCOPE_OFFER else scope_norm,
            template_slug='pre-approval-or-proof-of-funds',
            applicability=OPTIONAL,
            reason=pof_reason,
            source='offer_practice',
        ))

        if scope_norm == SCOPE_OFFER:
            add(ExpectedDocument(
                key='appraisal_termination',
                label="Addendum Concerning Right to Terminate Due to Lender's Appraisal",
                scope=SCOPE_OFFER,
                template_slug='appraisal-termination-addendum',
                applicability=OPTIONAL,
                reason='Often included with financed offers; present when uploaded with the package.',
                source='offer_practice',
                form_number='TREC 49',
            ))
            add(ExpectedDocument(
                key='broker_compensation',
                label='Compensation Agreement Between Brokers',
                scope=SCOPE_OFFER,
                template_slug='broker-compensation-agreement',
                applicability=OPTIONAL,
                reason='Broker-to-broker compensation agreements may accompany an offer package.',
                source='offer_practice',
                form_number='TXR 2402',
            ))

        add(ExpectedDocument(
            key='amendment',
            label='Amendment to Contract',
            scope=SCOPE_CONTRACT,
            template_slug='amendment',
            applicability=(
                POST_EXECUTION if has_controlling_contract else NOT_APPLICABLE
            ),
            reason=(
                'Amendments apply after a controlling executed contract exists.'
                if has_controlling_contract
                else 'No controlling contract yet — amendments are not in scope.'
            ),
            source='contract_lifecycle',
            form_number='TREC 39',
        ))

    return out
