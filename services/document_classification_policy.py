"""Canonical kind/slug/scope compatibility for classification confirmation."""
from __future__ import annotations

from typing import Any, Optional

from services.offer_side import side_for_transaction
from services.scoped_document_intake import (
    SCOPE_AMENDMENT,
    SCOPE_CONTRACT,
    SCOPE_LISTING,
    SCOPE_OFFER,
    SCOPE_OTHER,
    VALID_SCOPES,
)

KIND_LISTING = 'listing_agreement'
KIND_PURCHASE = 'purchase_contract'
KIND_AMENDMENT = 'amendment'
KIND_ADDENDUM = 'addendum'
KIND_DISCLOSURE = 'disclosure'
KIND_POF = 'proof_of_funds'
KIND_UNKNOWN = 'unknown'
KIND_OTHER = 'other'

VALID_KINDS = frozenset({
    KIND_LISTING,
    KIND_PURCHASE,
    KIND_AMENDMENT,
    KIND_ADDENDUM,
    KIND_DISCLOSURE,
    KIND_POF,
    KIND_UNKNOWN,
    KIND_OTHER,
})

LISTING_SLUGS = frozenset({'listing-agreement'})
PURCHASE_SLUGS = frozenset({
    'one-to-four-family-contract',
    'condominium-contract',
    'new-home-completed-construction-contract',
    'new-home-incomplete-construction-contract',
    'farm-and-ranch-contract',
    'unimproved-property-contract',
    'purchase-contract',
    'seller-offer-contract',
    'seller-accepted-contract',
})
AMENDMENT_SLUGS = frozenset({'amendment'})
ADDENDUM_SLUGS = frozenset({
    'third-party-financing-addendum',
    'hoa-addendum',
    'seller-backup-addendum',
})
DISCLOSURE_SLUGS = frozenset({'sellers-disclosure', 'lead-paint'})
POF_SLUGS = frozenset({'pre-approval-or-proof-of-funds'})
GENERIC_SLUGS = frozenset({'completed', 'external', 'custom'})

KIND_TO_SLUGS = {
    KIND_LISTING: LISTING_SLUGS,
    KIND_PURCHASE: PURCHASE_SLUGS,
    KIND_AMENDMENT: AMENDMENT_SLUGS,
    KIND_ADDENDUM: ADDENDUM_SLUGS,
    KIND_DISCLOSURE: DISCLOSURE_SLUGS,
    KIND_POF: POF_SLUGS,
    KIND_UNKNOWN: GENERIC_SLUGS,
    KIND_OTHER: GENERIC_SLUGS | frozenset(),
}

# Default scopes allowed per kind (before side/stage filters).
KIND_ALLOWED_SCOPES = {
    KIND_LISTING: frozenset({SCOPE_LISTING}),
    KIND_PURCHASE: frozenset({SCOPE_OFFER, SCOPE_CONTRACT}),
    KIND_AMENDMENT: frozenset({SCOPE_AMENDMENT, SCOPE_CONTRACT}),
    KIND_ADDENDUM: frozenset({SCOPE_OFFER, SCOPE_CONTRACT, SCOPE_LISTING}),
    KIND_DISCLOSURE: frozenset({SCOPE_OFFER, SCOPE_CONTRACT, SCOPE_LISTING}),
    KIND_POF: frozenset({SCOPE_OFFER, SCOPE_CONTRACT}),
    KIND_UNKNOWN: frozenset({SCOPE_OTHER, SCOPE_LISTING, SCOPE_OFFER, SCOPE_CONTRACT}),
    KIND_OTHER: frozenset({SCOPE_OTHER, SCOPE_LISTING}),
}


class ClassificationPolicyError(ValueError):
    def __init__(self, message: str, *, code: str = 'invalid_classification', status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def parse_strict_bool(value: Any, *, field_name: str = 'flag') -> bool:
    """Parse JSON booleans strictly — string 'false' must not become True."""
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ('true', '1', 'yes', 'on'):
            return True
        if text in ('false', '0', 'no', 'off', ''):
            return False
    raise ClassificationPolicyError(
        f'Invalid boolean for {field_name}.',
        code='invalid_boolean',
    )


def normalize_selected_field_flags(
    selected: dict[str, Any] | None,
) -> dict[str, bool]:
    """Strictly normalize proposal selected-field maps (reject non-booleans)."""
    if not isinstance(selected, dict):
        raise ClassificationPolicyError(
            'selected must be an object',
            code='invalid_selected_fields',
        )
    out: dict[str, bool] = {}
    for key, value in selected.items():
        out[str(key)] = parse_strict_bool(value, field_name=f'selected.{key}')
    return out


def kind_for_slug(slug: str | None) -> str | None:
    text = (slug or '').strip().lower()
    if not text:
        return None
    if text.startswith('custom-') or text.startswith('custom_'):
        return KIND_OTHER
    for kind, slugs in KIND_TO_SLUGS.items():
        if text in slugs:
            return kind
    return None


def validate_kind_slug_scope(
    *,
    kind: str,
    template_slug: str,
    scope: str,
    transaction,
    has_primary_contract: bool,
    explicit_controlling_confirmation: bool = False,
) -> None:
    """Raise ClassificationPolicyError when kind/slug/scope/side are incompatible."""
    kind_norm = (kind or '').strip().lower()
    slug = (template_slug or '').strip().lower()
    scope_norm = (scope or '').strip().lower()

    if scope_norm not in VALID_SCOPES:
        raise ClassificationPolicyError(
            'Invalid scope. Choose listing, offer, contract, amendment, or other.',
            code='invalid_scope',
        )
    if kind_norm not in VALID_KINDS:
        raise ClassificationPolicyError(
            'Invalid document kind.',
            code='invalid_kind',
        )

    inferred = kind_for_slug(slug)
    if inferred is None and not (
        slug.startswith('custom-') or slug.startswith('custom_') or slug in GENERIC_SLUGS
    ):
        raise ClassificationPolicyError(
            'Choose a supported document type.',
            code='invalid_slug',
        )

    # Kind must match slug group (unknown/other may accept generic).
    allowed_slugs = KIND_TO_SLUGS.get(kind_norm, frozenset())
    slug_ok = (
        slug in allowed_slugs
        or (kind_norm in (KIND_UNKNOWN, KIND_OTHER) and (
            slug in GENERIC_SLUGS
            or slug.startswith('custom-')
            or slug.startswith('custom_')
        ))
    )
    if not slug_ok:
        raise ClassificationPolicyError(
            'Document kind does not match the selected document type.',
            code='kind_slug_mismatch',
        )

    allowed_scopes = KIND_ALLOWED_SCOPES.get(kind_norm, frozenset())
    if scope_norm not in allowed_scopes:
        raise ClassificationPolicyError(
            'That destination is not valid for this document type.',
            code='scope_incompatible',
        )

    side = side_for_transaction(transaction)

    # Buyer + listing agreement rejected.
    if kind_norm == KIND_LISTING or slug in LISTING_SLUGS:
        if side == 'buyer':
            raise ClassificationPolicyError(
                'Listing agreements apply to seller representation only.',
                code='side_mismatch_listing',
            )
        if scope_norm in (SCOPE_OFFER, SCOPE_CONTRACT, SCOPE_AMENDMENT):
            raise ClassificationPolicyError(
                'Listing agreements cannot be filed to an offer or contract package.',
                code='scope_incompatible',
            )

    # Amendment requires active baseline; prefer amendment scope.
    if kind_norm == KIND_AMENDMENT or slug in AMENDMENT_SLUGS:
        if not has_primary_contract:
            raise ClassificationPolicyError(
                'Amendments require an active controlling contract.',
                code='no_controlling_contract',
            )
        if scope_norm not in (SCOPE_AMENDMENT, SCOPE_CONTRACT):
            raise ClassificationPolicyError(
                'Amendments must target amendment or contract scope.',
                code='scope_incompatible',
            )

    # Seller purchase → contract needs explicit controlling confirmation.
    # Buyer scope=contract is itself the destination confirmation.
    if kind_norm == KIND_PURCHASE or slug in PURCHASE_SLUGS:
        if scope_norm == SCOPE_CONTRACT and side == 'seller':
            if not explicit_controlling_confirmation:
                raise ClassificationPolicyError(
                    'Seller purchase contracts normally become offer threads. '
                    'Confirm controlling-contract filing explicitly.',
                    code='seller_controlling_unconfirmed',
                )
        if scope_norm == SCOPE_LISTING:
            raise ClassificationPolicyError(
                'Purchase contracts cannot be filed to the listing package.',
                code='scope_incompatible',
            )
