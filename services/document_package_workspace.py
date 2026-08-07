"""Display-ready grouped document packages for a transaction.

Merges expected-document descriptors with linked docs, identity metadata, and
embedded package components. Labels use expected / may apply / unknown phrasing
— never "legally required". Every transaction document appears in some package.
"""
from __future__ import annotations

from typing import Any, Optional

from flask import url_for

from models import (
    DocumentReviewReport,
    SellerContractAmendment,
    SellerContractAmendmentVersion,
    SellerContractDocument,
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    Transaction,
    TransactionDocument,
)
from services.controlling_contracts import get_active_primary_contract
from services.document_identity import DocumentIdentity, identity_for_slug
from services.expected_documents import (
    APPLICABLE,
    NOT_APPLICABLE,
    OPTIONAL,
    UNKNOWN,
    expected_documents_for_context,
    merge_listing_package_terms,
)
from services.offer_side import labels_for_side, side_for_transaction

STATE_EXPECTED = 'expected'
STATE_UPLOADED = 'uploaded'
STATE_MISSING = 'missing'
STATE_NEEDS_CLASSIFICATION = 'needs_classification'
STATE_NOT_APPLICABLE = 'not_applicable'
STATE_DETECTED_IN_PACKAGE = 'detected_in_package'

_GENERIC_SLUGS = frozenset({
    'completed', 'external', 'custom', 'upload', '',
})


def _applicability_label(applicability: str) -> str:
    mapping = {
        APPLICABLE: 'Expected',
        OPTIONAL: 'May apply',
        UNKNOWN: 'Unknown',
        NOT_APPLICABLE: 'Not applicable',
        'post_execution_only': 'After execution',
    }
    return mapping.get(applicability, 'May apply')


def _identity_from_doc(doc: TransactionDocument) -> DocumentIdentity | None:
    raw = (doc.field_data or {}).get('_document_identity')
    if isinstance(raw, dict):
        try:
            return DocumentIdentity.from_dict(raw)
        except Exception:
            pass
    confirmed = (doc.field_data or {}).get('_classification_confirmation') or {}
    slug = confirmed.get('template_slug') or doc.template_slug
    return identity_for_slug(slug)


def _is_generic_unconfirmed(doc: TransactionDocument) -> bool:
    slug = (doc.template_slug or '').strip().lower()
    if slug in _GENERIC_SLUGS or slug.startswith('custom-') or slug.startswith('custom_'):
        confirmed = (doc.field_data or {}).get('_classification_confirmation')
        return not confirmed
    return False


def _document_has_file(doc: TransactionDocument | None) -> bool:
    """True only when a real PDF is on file — placeholders without a path do not count."""
    if doc is None:
        return False
    return bool(
        getattr(doc, 'signed_file_path', None)
        or getattr(doc, 'source_file_path', None)
    )


def _effective_slug(doc: TransactionDocument | None) -> str:
    if doc is None:
        return ''
    identity = _identity_from_doc(doc)
    if identity and identity.template_slug:
        return str(identity.template_slug).strip().lower()
    confirmed = (doc.field_data or {}).get('_classification_confirmation') or {}
    return (
        (confirmed.get('template_slug') or doc.template_slug or '')
        .strip()
        .lower()
    )


def _doc_state(
    doc: TransactionDocument | None,
    applicability: str,
    *,
    offer_scoped: bool = False,
) -> str:
    if applicability == NOT_APPLICABLE:
        return STATE_NOT_APPLICABLE
    if doc is None or not _document_has_file(doc):
        # Questionnaire placeholders are expected slots, not uploads.
        return STATE_MISSING if applicability == APPLICABLE else STATE_EXPECTED
    # Offer-linked PDFs are already filed on an offer thread — never "Needs filing".
    if offer_scoped:
        return STATE_UPLOADED
    if _is_generic_unconfirmed(doc) and not _effective_slug(doc):
        return STATE_NEEDS_CLASSIFICATION
    if _is_generic_unconfirmed(doc) and _effective_slug(doc) in _GENERIC_SLUGS:
        return STATE_NEEDS_CLASSIFICATION
    # Identity retag pending on slug, but we know what it is — treat as uploaded.
    if _is_generic_unconfirmed(doc) and _effective_slug(doc) not in _GENERIC_SLUGS:
        return STATE_UPLOADED
    return STATE_UPLOADED


def _doc_urls(transaction_id: int, doc: TransactionDocument | None) -> dict[str, Optional[str]]:
    if not doc or not _document_has_file(doc):
        return {'doc_url': None, 'review_url': None}
    return {
        'doc_url': url_for(
            'transactions.view_document_pdf',
            id=transaction_id,
            doc_id=doc.id,
        ),
        'review_url': url_for(
            'transactions.document_review_workspace',
            id=transaction_id,
            doc_id=doc.id,
        ),
    }


def _row_from_expected(
    *,
    expected,
    matched_doc: TransactionDocument | None,
    scope: str,
    scope_id: int | None,
    transaction_id: int,
    detected_parent: TransactionDocument | None = None,
    has_review_findings: bool = False,
) -> dict[str, Any]:
    offer_scoped = scope == 'offer'
    if matched_doc is not None:
        state = _doc_state(
            matched_doc,
            expected.applicability,
            offer_scoped=offer_scoped,
        )
        urls = _doc_urls(transaction_id, matched_doc)
        has_file = _document_has_file(matched_doc)
        return {
            'key': expected.key,
            'label': expected.label,
            'canonical_slug': expected.template_slug,
            'form_number': expected.form_number,
            'scope': scope,
            'scope_id': scope_id,
            'applicability': expected.applicability,
            'applicability_label': _applicability_label(expected.applicability),
            'reason': expected.reason if not has_file else (
                expected.reason if state != STATE_UPLOADED else None
            ),
            'source': expected.source,
            'state': state,
            # Keep placeholder ids so Upload can fulfill that slot in place.
            'document_id': matched_doc.id,
            'is_placeholder': bool(
                getattr(matched_doc, 'is_placeholder', False) and not has_file
            ),
            'template_slug': matched_doc.template_slug,
            'template_name': matched_doc.template_name or expected.label,
            'detected_in_package': False,
            'parent_document_id': None,
            'has_review_findings': has_review_findings,
            **urls,
        }

    if detected_parent is not None and expected.applicability != NOT_APPLICABLE:
        parent_urls = _doc_urls(transaction_id, detected_parent)
        return {
            'key': expected.key,
            'label': expected.label,
            'canonical_slug': expected.template_slug,
            'form_number': expected.form_number,
            'scope': scope,
            'scope_id': scope_id,
            'applicability': expected.applicability,
            'applicability_label': _applicability_label(expected.applicability),
            'reason': (
                f'Detected inside package PDF '
                f'“{detected_parent.template_name or detected_parent.template_slug}” '
                f'— not a separate uploaded file.'
            ),
            'source': 'embedded_component',
            'state': STATE_DETECTED_IN_PACKAGE,
            'document_id': None,
            'template_slug': expected.template_slug,
            'template_name': expected.label,
            'detected_in_package': True,
            'parent_document_id': detected_parent.id,
            'has_review_findings': False,
            'doc_url': parent_urls['doc_url'],
            'review_url': parent_urls['review_url'],
        }

    state = _doc_state(None, expected.applicability)
    return {
        'key': expected.key,
        'label': expected.label,
        'canonical_slug': expected.template_slug,
        'form_number': expected.form_number,
        'scope': scope,
        'scope_id': scope_id,
        'applicability': expected.applicability,
        'applicability_label': _applicability_label(expected.applicability),
        'reason': expected.reason,
        'source': expected.source,
        'state': state,
        'document_id': None,
        'template_slug': expected.template_slug,
        'template_name': expected.label,
        'detected_in_package': False,
        'parent_document_id': None,
        'doc_url': None,
        'review_url': None,
    }


def _match_doc(
    docs_by_slug: dict[str, list[TransactionDocument]],
    slug: str | None,
    used_ids: set[int],
) -> TransactionDocument | None:
    if not slug:
        return None
    for doc in docs_by_slug.get(slug, []):
        if doc.id not in used_ids:
            used_ids.add(doc.id)
            return doc
    return None


def _index_docs(docs: list[TransactionDocument]) -> dict[str, list[TransactionDocument]]:
    by_slug: dict[str, list[TransactionDocument]] = {}
    for doc in docs:
        slug = (doc.template_slug or '').strip().lower()
        by_slug.setdefault(slug, []).append(doc)
        confirmed = (doc.field_data or {}).get('_classification_confirmation') or {}
        cslug = (confirmed.get('template_slug') or '').strip().lower()
        if cslug and cslug != slug:
            by_slug.setdefault(cslug, []).append(doc)
        identity_slug = _effective_slug(doc)
        if identity_slug and identity_slug != slug:
            by_slug.setdefault(identity_slug, []).append(doc)
        # Purchase contracts share one expected slot.
        if identity_slug in {
            'seller-offer-contract',
            'one-to-four-family-contract',
            'purchase-contract',
            'condominium-contract',
        } or slug in {
            'seller-offer-contract',
            'one-to-four-family-contract',
            'purchase-contract',
            'condominium-contract',
        }:
            by_slug.setdefault('purchase-contract', []).append(doc)
            by_slug.setdefault('seller-offer-contract', []).append(doc)
            by_slug.setdefault('one-to-four-family-contract', []).append(doc)
    # Prefer real files over empty questionnaire placeholders when matching.
    for slug, items in by_slug.items():
        items.sort(key=lambda d: (0 if _document_has_file(d) else 1, d.id or 0))
    return by_slug


def _embedded_by_slug(
    docs: list[TransactionDocument],
) -> dict[str, TransactionDocument]:
    """Map embedded component slug → parent document (first wins).

    AI ``detected_documents`` is authoritative when present (including an empty
    list — that means the model saw no supporting forms). Regex
    ``embedded_components`` are only used when extraction never produced
    ``detected_documents``, and never for high-confidence listing agreements.
    """
    out: dict[str, TransactionDocument] = {}
    for doc in docs:
        field_data = doc.field_data if isinstance(doc.field_data, dict) else {}
        identity = _identity_from_doc(doc)
        authority = (
            (identity.extras or {}).get('package_authority')
            if identity
            else None
        )
        detected = field_data.get('detected_documents')
        ai_spoke = (
            'detected_documents' in field_data
            or authority == 'ai_detected_documents'
        )

        if ai_spoke:
            # Prefer embeds already normalized by apply_ai_package_authority.
            ai_embeds = (
                (identity.extras or {}).get('embedded_components')
                if identity
                else None
            )
            if isinstance(ai_embeds, list) and ai_embeds:
                for component in ai_embeds:
                    if not isinstance(component, dict):
                        continue
                    slug = (
                        component.get('template_slug')
                        or component.get('slug')
                        or ''
                    ).strip().lower()
                    if slug and slug not in out:
                        out[slug] = doc
                continue

            segments = detected if isinstance(detected, list) else (
                (identity.extras or {}).get('ai_detected_documents')
                if identity
                else None
            )
            if not isinstance(segments, list) or not segments:
                continue
            from services.document_identity import template_slug_for_detected_type
            for item in segments:
                if not isinstance(item, dict):
                    continue
                seg_type = (
                    item.get('document_type') or item.get('type') or ''
                ).strip().lower()
                if seg_type in (
                    'listing_agreement',
                    'buyer_offer',
                    'residential_contract',
                    '',
                ):
                    continue
                slug = template_slug_for_detected_type(seg_type)
                if slug and slug not in out:
                    out[slug] = doc
            continue

        if not identity:
            continue
        # No AI package map yet: never promote listing checklist keyword hits.
        if (
            identity.kind == 'listing_agreement'
            and identity.confidence >= 0.85
        ):
            continue
        for component in (identity.extras or {}).get('embedded_components') or []:
            if not isinstance(component, dict):
                continue
            slug = (
                component.get('template_slug')
                or component.get('slug')
                or ''
            ).strip().lower()
            if slug and slug not in out:
                out[slug] = doc
    return out


def _append_unmatched_docs(
    *,
    rows: list[dict[str, Any]],
    docs: list[TransactionDocument],
    used_ids: set[int],
    scope: str,
    scope_id: int | None,
    transaction_id: int,
    review_doc_ids: set[int] | None = None,
) -> None:
    offer_scoped = scope == 'offer'
    review_doc_ids = review_doc_ids or set()
    for doc in docs:
        if doc.id in used_ids:
            continue
        used_ids.add(doc.id)
        has_file = _document_has_file(doc)
        # Questionnaire placeholders without a PDF are needed slots, not uploads.
        if not has_file:
            rows.append({
                'key': f'placeholder-{doc.id}',
                'label': doc.template_name or doc.template_slug or 'Required document',
                'canonical_slug': doc.template_slug,
                'form_number': None,
                'scope': scope,
                'scope_id': scope_id,
                'applicability': APPLICABLE,
                'applicability_label': 'Expected',
                'reason': doc.included_reason or 'Needed for this file.',
                'source': 'placeholder',
                'state': STATE_MISSING,
                'document_id': doc.id,
                'is_placeholder': True,
                'template_slug': doc.template_slug,
                'template_name': doc.template_name,
                'detected_in_package': False,
                'parent_document_id': None,
                'has_review_findings': False,
                'doc_url': None,
                'review_url': None,
            })
            continue
        state = _doc_state(doc, OPTIONAL, offer_scoped=offer_scoped)
        urls = _doc_urls(transaction_id, doc)
        rows.append({
            'key': f'uploaded-{doc.id}',
            'label': doc.template_name or doc.template_slug or 'Uploaded document',
            'canonical_slug': _effective_slug(doc) or doc.template_slug,
            'form_number': None,
            'scope': scope,
            'scope_id': scope_id,
            'applicability': OPTIONAL,
            'applicability_label': 'Supporting' if offer_scoped else 'May apply',
            'reason': (
                'Supporting upload in this offer package.'
                if offer_scoped
                else (
                    'Uploaded document awaiting classification.'
                    if state == STATE_NEEDS_CLASSIFICATION
                    else 'Uploaded document in this package.'
                )
            ),
            'source': 'uploaded',
            'state': state,
            'document_id': doc.id,
            'is_placeholder': False,
            'template_slug': doc.template_slug,
            'template_name': doc.template_name,
            'detected_in_package': False,
            'parent_document_id': None,
            'has_review_findings': doc.id in review_doc_ids,
            **urls,
        })


def _build_package_rows(
    *,
    expected_list,
    docs: list[TransactionDocument],
    scope: str,
    scope_id: int | None,
    transaction_id: int,
    review_doc_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    by_slug = _index_docs(docs)
    embedded = _embedded_by_slug(docs)
    used: set[int] = set()
    rows = []
    review_doc_ids = review_doc_ids or set()
    for exp in expected_list:
        matched = _match_doc(by_slug, exp.template_slug, used)
        # Not applicable = not a slot. Hide empty placeholders; still consume
        # them so they do not reappear as "Needed" unmatched rows. Keep a row
        # only when a real PDF was already filed under that slug.
        if exp.applicability == NOT_APPLICABLE:
            if matched is None or not _document_has_file(matched):
                continue
        # Prefer a real linked file over "Detected in package" for the same slot.
        parent = None if matched else embedded.get((exp.template_slug or '').strip().lower())
        row = _row_from_expected(
            expected=exp,
            matched_doc=matched,
            scope=scope,
            scope_id=scope_id,
            transaction_id=transaction_id,
            detected_parent=parent,
            has_review_findings=bool(matched and matched.id in review_doc_ids),
        )
        rows.append(row)
    _append_unmatched_docs(
        rows=rows,
        docs=docs,
        used_ids=used,
        scope=scope,
        scope_id=scope_id,
        transaction_id=transaction_id,
        review_doc_ids=review_doc_ids,
    )
    return rows


def _tx_type_name(transaction: Transaction) -> str | None:
    tx_type = getattr(transaction, 'transaction_type', None)
    name = getattr(tx_type, 'name', None) if tx_type else None
    return (name or '').strip().lower() or None


def _representation_package_meta(side: str | None, type_name: str | None) -> dict[str, str]:
    """Side-aware primary package key/label/scope (never seller-biased for buyers)."""
    if side == 'seller':
        return {
            'key': 'listing_package',
            'scope': 'listing',
            'label': 'Listing package',
        }
    if side == 'buyer':
        return {
            'key': 'buyer_transaction_package',
            'scope': 'buyer_transaction',
            'label': 'Buyer transaction package',
        }
    # landlord / tenant / referral / unknown
    label = 'Transaction documents'
    if type_name in ('landlord', 'tenant', 'referral'):
        label = f'{type_name.replace("_", " ").title()} documents'
    return {
        'key': 'transaction_documents_package',
        'scope': 'transaction_documents',
        'label': label,
    }


def build_document_packages(transaction: Transaction) -> dict[str, Any]:
    """Build side-aware document packages for a transaction.

    Seller: listing package + offers + controlling contract + amendments.
    Buyer: buyer transaction package + submitted offers + controlling contract
    + amendments (no listing package / label).
    Other sides: neutral transaction_documents package.
    Unconfirmed/generic docs always land in ``unfiled_documents``.
    """
    org_id = transaction.organization_id
    tx_id = transaction.id
    side = side_for_transaction(transaction)
    type_name = _tx_type_name(transaction)
    try:
        side_labels = labels_for_side(side) if side else {}
    except ValueError:
        side_labels = {}

    all_docs = (
        TransactionDocument.query.filter_by(
            transaction_id=tx_id,
            organization_id=org_id,
        )
        .order_by(TransactionDocument.id.asc())
        .all()
    )
    docs_by_id = {d.id: d for d in all_docs}

    offer_links = SellerOfferDocument.query.filter_by(
        transaction_id=tx_id, organization_id=org_id,
    ).all()
    contract_links = SellerContractDocument.query.filter_by(
        transaction_id=tx_id, organization_id=org_id,
    ).all()

    offer_doc_ids: dict[int, list[int]] = {}
    for link in offer_links:
        offer_doc_ids.setdefault(link.offer_id, []).append(link.transaction_document_id)

    contract_doc_ids: dict[int, list[int]] = {}
    for link in contract_links:
        contract_doc_ids.setdefault(
            link.accepted_contract_id, [],
        ).append(link.transaction_document_id)

    linked_offer_doc_ids = {link.transaction_document_id for link in offer_links}
    linked_contract_doc_ids = {link.transaction_document_id for link in contract_links}

    amendments = (
        SellerContractAmendment.query.filter_by(
            transaction_id=tx_id, organization_id=org_id,
        )
        .order_by(SellerContractAmendment.created_at.desc())
        .all()
    )
    amendment_doc_ids: set[int] = set()
    for amendment in amendments:
        if amendment.current_version_id:
            version = SellerContractAmendmentVersion.query.filter_by(
                id=amendment.current_version_id,
                amendment_id=amendment.id,
                organization_id=org_id,
            ).first()
            if version and version.transaction_document_id:
                amendment_doc_ids.add(version.transaction_document_id)

    accounted_ids = (
        set(linked_offer_doc_ids) | set(linked_contract_doc_ids) | set(amendment_doc_ids)
    )

    # Split remaining docs: unfiled (generic/unconfirmed) vs representation-level.
    remaining = [
        d for d in all_docs
        if d.id not in accounted_ids
    ]
    unfiled_docs = [d for d in remaining if _is_generic_unconfirmed(d)]
    representation_docs = [d for d in remaining if d.id not in {u.id for u in unfiled_docs}]

    rep_meta = _representation_package_meta(side, type_name)
    listing_package = None
    buyer_transaction_package = None
    transaction_documents_package = None
    representation_rows: list[dict[str, Any]] = []

    if side == 'seller':
        listing_field_data = {}
        for d in representation_docs:
            if d.template_slug == 'listing-agreement' and isinstance(d.field_data, dict):
                listing_field_data = d.field_data
                break
        # Questionnaire answers decide Texas applicability (lead paint, HOA,
        # disclosure) — do not rely on listing PDF extraction alone.
        listing_terms = merge_listing_package_terms(
            listing_field_data=listing_field_data,
            intake_data=getattr(transaction, 'intake_data', None),
            ownership_status=getattr(transaction, 'ownership_status', None),
        )
        listing_identities = [
            i for i in (_identity_from_doc(d) for d in representation_docs) if i
        ]
        listing_expected = expected_documents_for_context(
            scope='listing',
            terms=listing_terms,
            identities=listing_identities,
            has_controlling_contract=get_active_primary_contract(tx_id, org_id) is not None,
        )
        representation_rows = _build_package_rows(
            expected_list=listing_expected,
            docs=representation_docs,
            scope='listing',
            scope_id=tx_id,
            transaction_id=tx_id,
        )
        listing_package = {
            'key': 'listing_package',
            'scope': 'listing',
            'scope_id': tx_id,
            'label': 'Listing package',
            'documents': representation_rows,
        }
    elif side == 'buyer':
        # No seller listing expected set — known buyer tx-level docs only.
        representation_rows = _build_package_rows(
            expected_list=[],
            docs=representation_docs,
            scope='buyer_transaction',
            scope_id=tx_id,
            transaction_id=tx_id,
        )
        buyer_transaction_package = {
            'key': 'buyer_transaction_package',
            'scope': 'buyer_transaction',
            'scope_id': tx_id,
            'label': 'Buyer transaction package',
            'documents': representation_rows,
        }
    else:
        representation_rows = _build_package_rows(
            expected_list=[],
            docs=representation_docs,
            scope=rep_meta['scope'],
            scope_id=tx_id,
            transaction_id=tx_id,
        )
        transaction_documents_package = {
            'key': rep_meta['key'],
            'scope': rep_meta['scope'],
            'scope_id': tx_id,
            'label': rep_meta['label'],
            'documents': representation_rows,
        }

    accounted_ids |= {r['document_id'] for r in representation_rows if r.get('document_id')}

    offers = (
        SellerOffer.query.filter_by(transaction_id=tx_id, organization_id=org_id)
        .filter(SellerOffer.status.notin_(('withdrawn', 'expired')))
        .order_by(SellerOffer.id.desc())
        .all()
    )
    version_ids = [o.current_version_id for o in offers if o.current_version_id]
    versions_by_id = {}
    if version_ids:
        for v in SellerOfferVersion.query.filter(
            SellerOfferVersion.id.in_(version_ids),
            SellerOfferVersion.organization_id == org_id,
        ).all():
            versions_by_id[v.id] = v

    open_review_doc_ids = {
        row.document_id
        for row in DocumentReviewReport.query.filter_by(
            transaction_id=tx_id,
            organization_id=org_id,
            status=DocumentReviewReport.STATUS_OPEN,
        ).filter(
            DocumentReviewReport.severity.in_((
                DocumentReviewReport.SEVERITY_ATTENTION,
                DocumentReviewReport.SEVERITY_CRITICAL,
            )),
            DocumentReviewReport.document_id.isnot(None),
        ).all()
        if row.document_id
    }

    offer_packages = []
    for offer in offers:
        version = versions_by_id.get(offer.current_version_id)
        terms = dict(version.terms_data or {}) if version else {}
        for key, attr in (
            ('offer_price', 'offer_price'),
            ('financing_type', 'financing_type'),
            ('earnest_money', 'earnest_money'),
            ('option_period_days', 'option_period_days'),
            ('proposed_close_date', 'proposed_close_date'),
        ):
            if key not in terms and getattr(offer, attr, None) is not None:
                terms[key] = getattr(offer, attr)

        o_docs = [
            docs_by_id[did]
            for did in offer_doc_ids.get(offer.id, [])
            if did in docs_by_id
        ]
        identities = [i for i in (_identity_from_doc(d) for d in o_docs) if i]
        # Prefer HOA true when an HOA addendum is already on the offer.
        if any(
            (_effective_slug(d) == 'hoa-addendum' or d.template_slug == 'hoa-addendum')
            for d in o_docs
        ):
            terms.setdefault('hoa_applicable', True)
        if any(
            (_effective_slug(d) == 'third-party-financing-addendum'
             or d.template_slug == 'third-party-financing-addendum')
            for d in o_docs
        ):
            terms.setdefault('third_party_financing', True)
            terms.setdefault('financing_type', terms.get('financing_type') or 'conventional')

        expected = expected_documents_for_context(
            scope='offer',
            terms=terms,
            identities=identities,
            has_controlling_contract=False,
        )
        review_ids = {d.id for d in o_docs if d.id in open_review_doc_ids}
        rows = _build_package_rows(
            expected_list=expected,
            docs=o_docs,
            scope='offer',
            scope_id=offer.id,
            transaction_id=tx_id,
            review_doc_ids=review_ids,
        )
        accounted_ids |= {r['document_id'] for r in rows if r.get('document_id')}

        primary_doc = next(
            (
                d for d in o_docs
                if (_identity_from_doc(d) and _identity_from_doc(d).kind == 'purchase_contract')
                or (d.template_slug or '') in {
                    'seller-offer-contract',
                    'one-to-four-family-contract',
                    'purchase-contract',
                }
            ),
            None,
        )
        if primary_doc is None and o_docs:
            primary_doc = o_docs[0]
        from services.offer_package_review import offer_package_review_url
        package_review_url = offer_package_review_url(tx_id, offer.id)
        # Offer-scoped rows open the package review page, not per-doc filing.
        for row in rows:
            if row.get('document_id') or row.get('state') in (
                STATE_UPLOADED, STATE_NEEDS_CLASSIFICATION, STATE_DETECTED_IN_PACKAGE,
            ):
                row['review_url'] = package_review_url
                row['offer_package_review'] = True

        counterparty = side_labels.get('counterparty_label', 'Offer')
        offer_packages.append({
            'scope': 'offer',
            'scope_id': offer.id,
            'label': (
                f"{counterparty}: {offer.buyer_names or f'Offer #{offer.id}'}"
            ),
            'status': offer.status,
            'side': side,
            'buyer_names': offer.buyer_names,
            'offer_price': float(offer.offer_price) if offer.offer_price is not None else None,
            'documents': rows,
            'primary_document_id': primary_doc.id if primary_doc else None,
            'review_url': package_review_url,
            'has_review_findings': bool(review_ids),
            'document_count': len(o_docs),
        })

    contract = get_active_primary_contract(tx_id, org_id)
    contract_package = None
    if contract:
        c_docs = [
            docs_by_id[did]
            for did in contract_doc_ids.get(contract.id, [])
            if did in docs_by_id
        ]
        terms = dict(contract.frozen_terms or {})
        identities = [i for i in (_identity_from_doc(d) for d in c_docs) if i]
        expected = expected_documents_for_context(
            scope='contract',
            terms=terms,
            identities=identities,
            has_controlling_contract=True,
        )
        rows = _build_package_rows(
            expected_list=expected,
            docs=c_docs,
            scope='contract',
            scope_id=contract.id,
            transaction_id=tx_id,
        )
        accounted_ids |= {r['document_id'] for r in rows if r.get('document_id')}
        contract_package = {
            'scope': 'contract',
            'scope_id': contract.id,
            'label': 'Controlling contract',
            'status': contract.status,
            'position': contract.position,
            'closing_date': (
                contract.closing_date.isoformat() if contract.closing_date else None
            ),
            'side': side,
            'documents': rows,
        }

    amendment_rows = []
    for amendment in amendments:
        version = None
        if amendment.current_version_id:
            version = SellerContractAmendmentVersion.query.filter_by(
                id=amendment.current_version_id,
                amendment_id=amendment.id,
                organization_id=org_id,
            ).first()
        doc = None
        if version and version.transaction_document_id:
            doc = docs_by_id.get(version.transaction_document_id)
            if doc:
                accounted_ids.add(doc.id)
        urls = _doc_urls(tx_id, doc)
        review_url = url_for(
            'transactions.amendment_review',
            id=tx_id,
            amendment_id=amendment.id,
        )
        amendment_rows.append({
            'key': f'amendment-{amendment.id}',
            'label': amendment.summary or amendment.amendment_type or 'Amendment',
            'canonical_slug': 'amendment',
            'scope': 'amendment',
            'scope_id': amendment.id,
            'applicability': APPLICABLE,
            'applicability_label': 'Expected',
            'reason': 'Amendment thread under the controlling contract.',
            'source': 'amendment',
            'state': STATE_UPLOADED if doc else STATE_EXPECTED,
            'document_id': doc.id if doc else None,
            'status': amendment.status,
            'direction': version.direction if version else None,
            'detected_in_package': False,
            'parent_document_id': None,
            'doc_url': urls['doc_url'],
            'review_url': review_url,
        })

    # Safety net: anything still unaccounted goes to unfiled (never mislabeled listing).
    stray = [d for d in all_docs if d.id not in accounted_ids]
    for d in stray:
        if d.id not in {u.id for u in unfiled_docs}:
            unfiled_docs.append(d)

    unfiled_rows: list[dict[str, Any]] = []
    _append_unmatched_docs(
        rows=unfiled_rows,
        docs=unfiled_docs,
        used_ids=set(),
        scope='unfiled',
        scope_id=tx_id,
        transaction_id=tx_id,
    )
    for row in unfiled_rows:
        if row.get('state') == STATE_NEEDS_CLASSIFICATION:
            row['reason'] = 'Uploaded document awaiting classification.'
        else:
            row['reason'] = 'Uploaded document not yet filed to a package.'

    unfiled_package = {
        'key': 'unfiled_documents',
        'scope': 'unfiled',
        'scope_id': tx_id,
        'label': 'Unfiled documents',
        'documents': unfiled_rows,
    }

    return {
        'transaction_id': tx_id,
        'side': side,
        'transaction_type': type_name,
        'listing_package': listing_package,
        'buyer_transaction_package': buyer_transaction_package,
        'transaction_documents_package': transaction_documents_package,
        'unfiled_documents': unfiled_package,
        'offer_packages': offer_packages,
        'controlling_contract_package': contract_package,
        'amendments': amendment_rows,
    }
