"""Post-upload document review: observe, compare, alert — never auto-write CRM.

After extraction stores field_data, this module runs deterministic checks against
the transaction / CRM / prior docs, persists a DocumentReviewReport, and fans
out to the notification bell, optional Telegram, and UI toast/banner surfaces.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional

from models import (
    DocumentReviewReport,
    Notification,
    SellerListingProfile,
    SellerOfferDocument,
    Transaction,
    TransactionAssignment,
    TransactionDocument,
    TransactionParticipant,
    User,
    db,
)
from services.notification_service import create_notification
from services.offer_side import side_for_transaction

logger = logging.getLogger(__name__)

_SCALE_SANITY_CODES = frozenset({
    'sales_price_format_anomaly',
    'amount_scale_anomaly',
    'decimal_placement',
    'price_scale',
    'price_format_anomaly',
    'amount_format_check',
})
_SCALE_MESSAGE_HINTS = (
    'digits only',
    'decimal',
    'overstate',
    'scale',
    'factor of 100',
    'inflated',
    'two decimal',
)
_SELLER_SIG_CODE_HINTS = (
    'seller_signature',
    'missing_seller_signature',
    'seller_signatures',
    'unsigned_seller',
)
_BUYER_SIG_CODE_HINTS = (
    'buyer_signature',
    'missing_buyer_signature',
    'buyer_signatures',
    'unsigned_buyer',
    'buyer_acknowledgement',
)
_PARTY_CRM_HINTS = (
    'crm contact',
    'crm party',
    'party records',
    'differs from the crm',
)


def _is_inbound_seller_offer_document(
    transaction: Transaction,
    document: TransactionDocument,
) -> bool:
    """True when this PDF is on a seller inbound offer (not controlling yet)."""
    if side_for_transaction(transaction) != 'seller':
        return False
    try:
        link = SellerOfferDocument.query.filter_by(
            organization_id=transaction.organization_id,
            transaction_id=transaction.id,
            transaction_document_id=document.id,
        ).first()
    except Exception:
        return False
    return link is not None


def _sanity_is_scale_noise(flag: dict) -> bool:
    code = str(flag.get('code') or '').strip().lower()
    if code in _SCALE_SANITY_CODES or 'scale' in code or 'decimal' in code:
        return True
    message = str(flag.get('message') or '').lower()
    return any(hint in message for hint in _SCALE_MESSAGE_HINTS)


def _sanity_is_buyer_signature_noise(flag: dict) -> bool:
    """Buyer signature/ack lines are blank on listing-stage paperwork."""
    code = str(flag.get('code') or '').strip().lower()
    field_key = str(flag.get('field_key') or '').strip().lower()
    message = str(flag.get('message') or '').strip().lower()
    if any(hint in code for hint in _BUYER_SIG_CODE_HINTS):
        return True
    if 'buyer_signature' in field_key or 'buyer_acknowledg' in field_key:
        return True
    if 'buyer' not in message:
        return False
    if 'signature' not in message and 'sign' not in message and 'acknowledg' not in message:
        return False
    return any(
        token in message
        for token in (
            'blank',
            'unsigned',
            'not signed',
            'not confirm',
            'could not confirm',
            'missing',
            'not present',
            'no buyer',
        )
    )


def _sanity_is_seller_signature_noise(flag: dict) -> bool:
    """Blank/unsigned seller lines are normal on inbound buyer-offer packages."""
    code = str(flag.get('code') or '').strip().lower()
    field_key = str(flag.get('field_key') or '').strip().lower()
    message = str(flag.get('message') or '').strip().lower()
    if any(hint in code for hint in _SELLER_SIG_CODE_HINTS):
        return True
    if 'seller_signature' in field_key:
        return True
    if 'seller' not in message:
        return False
    if 'signature' not in message and 'sign' not in message:
        return False
    return any(
        token in message
        for token in (
            'blank',
            'unsigned',
            'not signed',
            'not confirm',
            'could not confirm',
            'missing',
            'appear present',
            'are present but',
        )
    )


def _sanity_is_party_crm_noise(flag: dict) -> bool:
    code = str(flag.get('code') or '').strip().lower()
    if code in ('party_mismatch', 'buyer_party_mismatch', 'party_name_mismatch'):
        return True
    message = str(flag.get('message') or '').strip().lower()
    return any(hint in message for hint in _PARTY_CRM_HINTS)


def _money_normalizes_cleanly(value: Any) -> bool:
    """True when scoped normalizer can reconcile a blown-up digits-only amount."""
    try:
        from services.scoped_document_intake import _normalize_offer_price
        raw = str(value).replace(',', '').replace('$', '').strip()
        if not raw:
            return False
        normalized = _normalize_offer_price(raw)
        if normalized is None:
            return False
        # Blowup case: raw ~100x normalized.
        from decimal import Decimal
        original = Decimal(raw)
        return original >= Decimal('10000') and original >= (normalized * Decimal('50'))
    except Exception:
        return False


def _parse_anchor_date(value: Any) -> date | None:
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


def _seed_listing_deadline_pack(transaction: Transaction, document: TransactionDocument) -> None:
    """Seed listing_v1 requirements after a successful listing-agreement extraction."""
    type_name = (
        transaction.transaction_type.name
        if transaction.transaction_type else ''
    ).lower()
    if type_name != 'seller':
        return
    if document.template_slug != 'listing-agreement':
        return

    field_data = document.field_data if isinstance(document.field_data, dict) else {}
    anchors = {}

    listing_start = _parse_anchor_date(
        field_data.get('listing_start_date') or field_data.get('listing_start')
    )
    if listing_start:
        anchors['listing_start'] = listing_start
        anchors['listing_start_date'] = listing_start

    profile = SellerListingProfile.query.filter_by(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
    ).first()
    if profile and profile.go_live_date:
        go_live = _parse_anchor_date(profile.go_live_date)
        if go_live:
            anchors['go_live_date'] = go_live

    if not anchors:
        return

    from services.deadline_rules import DeadlineRulesService

    DeadlineRulesService.apply_pack_to_transaction(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
        pack_key='listing_v1',
        anchors=anchors,
        side='seller',
        source='deadline_pack',
    )

# Common extracted field keys → human labels for conflict copy.
_DATE_FIELDS = {
    'close_date': 'closing date',
    'closing_date': 'closing date',
    'effective_date': 'effective date',
    'option_period_end': 'option-period end',
    'option_fee_deadline': 'option-fee deadline',
    'earnest_money_deadline': 'earnest-money deadline',
}
_AMOUNT_FIELDS = {
    'sales_price': 'sales price',
    'purchase_price': 'purchase price',
    'earnest_money': 'earnest money',
    'option_fee': 'option fee',
}
_PARTY_FIELDS = {
    'buyer_name': 'buyer name',
    'buyer_names': 'buyer names',
    'seller_name': 'seller name',
    'seller_names': 'seller names',
    'buyer_last_name': "buyer's last name",
    'seller_last_name': "seller's last name",
}
_ADDRESS_FIELDS = ('property_address', 'street_address', 'address')


def _norm_str(value: Any) -> str:
    if value is None:
        return ''
    return re.sub(r'\s+', ' ', str(value).strip().lower())


def _norm_date(value: Any) -> Optional[str]:
    if value is None or value == '':
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()[:10]
    text = str(value).strip()
    # Accept ISO-ish and common US forms lightly.
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', text)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', text)
    if m:
        return f'{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}'
    return text.lower()


def _finding(
    code: str,
    message: str,
    *,
    severity: str = DocumentReviewReport.SEVERITY_ATTENTION,
    field_key: str | None = None,
    page: int | None = None,
    quote: str | None = None,
    crm_value: Any = None,
    extracted_value: Any = None,
) -> dict:
    item = {
        'code': code,
        'severity': severity,
        'message': message,
    }
    if field_key is not None:
        item['field_key'] = field_key
    if page is not None:
        item['page'] = page
    if quote:
        item['quote'] = quote
    if crm_value is not None:
        item['crm_value'] = crm_value
    if extracted_value is not None:
        item['extracted_value'] = extracted_value
    return item


def _page_hint(field_meta: dict | None, key: str) -> int | None:
    if not field_meta:
        return None
    meta = field_meta.get(key) or {}
    page = meta.get('page')
    return int(page) if page is not None else None


def _quote_hint(field_meta: dict | None, key: str) -> str | None:
    if not field_meta:
        return None
    quote = (field_meta.get(key) or {}).get('quote')
    return quote if isinstance(quote, str) and quote.strip() else None


def build_findings(
    *,
    transaction: Transaction,
    document: TransactionDocument,
    field_data: dict | None,
    extraction_failed: bool = False,
    extraction_error: str | None = None,
    manual_review_reason: str | None = None,
) -> tuple[list[dict], int]:
    """Deterministic operational checks. Returns (findings, field_count)."""
    findings: list[dict] = []
    data = field_data or {}
    review_only_fields = {
        '_meta', 'document_classification', 'document_title', 'document_summary',
        'form_id', 'form_revision_date', 'authoritative_deadlines', 'sanity_flags',
        'unreadable_pages', 'buyer_signature_detected', 'seller_signature_detected',
    }
    field_count = len([
        k for k, v in data.items()
        if k not in review_only_fields and v not in (None, '', [], {})
    ])
    field_meta = (document.field_data or {}).get('_meta') if isinstance(document.field_data, dict) else None
    # Prefer top-level keys; ignore internal meta key for count.
    if '_meta' in data:
        field_count = len([
            k for k, v in data.items()
            if k not in review_only_fields and v not in (None, '', [], {})
        ])

    if manual_review_reason:
        findings.append(_finding(
            'manual_review_required',
            manual_review_reason,
            severity=DocumentReviewReport.SEVERITY_ATTENTION,
        ))
        return findings, field_count

    if extraction_failed:
        findings.append(_finding(
            'processing_failed',
            f'Document review could not finish'
            + (f': {extraction_error}' if extraction_error else '.')
            + ' The original file is stored unchanged.',
            severity=DocumentReviewReport.SEVERITY_CRITICAL,
        ))
        return findings, field_count

    inbound_offer = _is_inbound_seller_offer_document(transaction, document)
    from services.document_signature_policy import signature_expectations
    expectations = signature_expectations(transaction, document)

    # AI-observed operational flags must be concrete, page-cited when possible,
    # and remain suggestions. Deterministic CRM comparisons below still run.
    for raw_flag in data.get('sanity_flags') or []:
        if not isinstance(raw_flag, dict):
            continue
        message = str(raw_flag.get('message') or '').strip()
        if not message:
            continue
        field_key = str(raw_flag.get('field_key') or '')
        if not expectations.expect_buyer and _sanity_is_buyer_signature_noise(raw_flag):
            continue
        if not expectations.expect_seller and _sanity_is_seller_signature_noise(raw_flag):
            continue
        if inbound_offer:
            # Blank/unsigned seller lines and buyer≠listing-party CRM mismatches
            # are noise on inbound offer packages (buyers are new to the deal).
            if _sanity_is_seller_signature_noise(raw_flag):
                continue
            if _sanity_is_party_crm_noise(raw_flag):
                continue
            if _sanity_is_scale_noise(raw_flag):
                amount = (
                    data.get(field_key)
                    if field_key
                    else data.get('sales_price') or data.get('first_mortgage_amount')
                )
                # Drop money-format nitpicks on support addenda; keep only when
                # the primary contract price cannot be normalized.
                if not field_key or field_key in (
                    'sales_price', 'offer_price', 'purchase_price',
                    'first_mortgage_amount', 'appraisal_threshold',
                    'appraisal_amount',
                ):
                    continue
                if _money_normalizes_cleanly(amount):
                    continue
        severity = str(raw_flag.get('severity') or '').lower()
        if severity not in (
            DocumentReviewReport.SEVERITY_ATTENTION,
            DocumentReviewReport.SEVERITY_CRITICAL,
        ):
            severity = DocumentReviewReport.SEVERITY_ATTENTION
        raw_page = raw_flag.get('page')
        try:
            page = int(raw_page) if raw_page not in (None, '') else None
        except (TypeError, ValueError):
            page = None
        findings.append(_finding(
            str(raw_flag.get('code') or 'document_sanity_issue')[:80],
            message,
            severity=severity,
            field_key=str(raw_flag.get('field_key'))[:100] if raw_flag.get('field_key') else None,
            page=page,
        ))

    # Wrong / mismatched property address
    for key in _ADDRESS_FIELDS:
        extracted = data.get(key)
        if not extracted:
            continue
        tx_addr = _norm_str(transaction.street_address)
        ext_addr = _norm_str(extracted)
        if tx_addr and ext_addr and tx_addr not in ext_addr and ext_addr not in tx_addr:
            # Compare first token / house number + street fragment
            tx_num = tx_addr.split(' ')[0]
            ext_num = ext_addr.split(' ')[0]
            if tx_num and ext_num and tx_num != ext_num:
                findings.append(_finding(
                    'address_mismatch',
                    (
                        f'The property address in the document ({extracted}) '
                        f'does not match this transaction ({transaction.street_address}). '
                        'This may be the wrong transaction.'
                    ),
                    severity=DocumentReviewReport.SEVERITY_CRITICAL,
                    field_key=key,
                    page=_page_hint(field_meta, key),
                    quote=_quote_hint(field_meta, key),
                    crm_value=transaction.street_address,
                    extracted_value=extracted,
                ))
                break

    # Closing / effective date vs CRM expected_close_date
    crm_close = _norm_date(transaction.expected_close_date)
    for key, label in _DATE_FIELDS.items():
        extracted = data.get(key)
        if extracted in (None, ''):
            continue
        ext_d = _norm_date(extracted)
        page = _page_hint(field_meta, key)
        page_bit = f' on page {page}' if page else ''
        if key in ('close_date', 'closing_date') and crm_close and ext_d and ext_d != crm_close:
            findings.append(_finding(
                'date_conflict',
                (
                    f'The {label}{page_bit} is {extracted}. '
                    f'The CRM currently says {transaction.expected_close_date}.'
                ),
                field_key=key,
                page=page,
                quote=_quote_hint(field_meta, key),
                crm_value=str(transaction.expected_close_date),
                extracted_value=str(extracted),
            ))

    # Missing critical amounts when document looks like a contract/offer
    slug = (document.template_slug or '').lower()
    name = (document.template_name or '').lower()
    looks_contractish = any(
        token in slug or token in name
        for token in ('contract', 'offer', 'purchase', 'amendment', 'addendum')
    )
    if looks_contractish:
        for key, label in _AMOUNT_FIELDS.items():
            if key in data and data.get(key) in (None, '', 0, '0'):
                findings.append(_finding(
                    'missing_amount',
                    f'The {label} field could not be read confidently.',
                    field_key=key,
                    page=_page_hint(field_meta, key),
                    quote=_quote_hint(field_meta, key),
                ))
        # Option period commonly critical
        if 'option_period_end' in data and data.get('option_period_end') in (None, ''):
            findings.append(_finding(
                'low_confidence_critical',
                'The option-period field could not be read confidently.',
                field_key='option_period_end',
                page=_page_hint(field_meta, 'option_period_end'),
            ))
        if (
            not inbound_offer
            and not data.get('title_company_email')
            and 'title_company' in {str(k).lower() for k in data.keys()}
        ):
            findings.append(_finding(
                'missing_title_email',
                'I found no title-company email.',
                field_key='title_company_email',
            ))

    # Party name mismatches vs participants (skip buyer mismatch on inbound offers —
    # listing parties are sellers/agents; inbound buyers are new).
    if not inbound_offer:
        participants = TransactionParticipant.query.filter_by(
            transaction_id=transaction.id,
            organization_id=transaction.organization_id,
        ).all()
        party_blob = ' '.join(
            _norm_str(getattr(p, 'display_name', None) or '')
            for p in participants
        )
        for key, label in _PARTY_FIELDS.items():
            extracted = data.get(key)
            if not extracted or not party_blob:
                continue
            extracted_names = extracted if isinstance(extracted, list) else [extracted]
            unmatched = []
            for extracted_name in extracted_names:
                ext = _norm_str(extracted_name)
                last = ext.split(' ')[-1] if ext else ''
                if last and last not in party_blob:
                    unmatched.append(str(extracted_name))
            if unmatched:
                findings.append(_finding(
                    'party_mismatch',
                    f'The {label} ({", ".join(unmatched)}) differs from the CRM contact/party records.',
                    field_key=key,
                    page=_page_hint(field_meta, key),
                    quote=_quote_hint(field_meta, key),
                    extracted_value=str(extracted),
                ))

    # Duplicate / supersede by filename or content hash if present
    siblings = TransactionDocument.query.filter(
        TransactionDocument.transaction_id == transaction.id,
        TransactionDocument.organization_id == transaction.organization_id,
        TransactionDocument.id != document.id,
    ).all()
    doc_name = _norm_str(document.template_name or document.template_slug or '')
    for sib in siblings:
        sib_name = _norm_str(sib.template_name or sib.template_slug or '')
        if doc_name and sib_name and doc_name == sib_name:
            findings.append(_finding(
                'possible_duplicate',
                (
                    f'This looks similar to an existing document '
                    f'“{sib.template_name or sib.template_slug}” already on the file. '
                    'Confirm whether it is a duplicate or a newer version.'
                ),
                severity=DocumentReviewReport.SEVERITY_ATTENTION,
            ))
            break
        if (
            getattr(document, 'parent_document_id', None)
            and sib.id == document.parent_document_id
        ):
            continue

    # Signature / checkbox — careful wording only if extractor provided signals
    for key, value in data.items():
        if not key.endswith('_signature_present') and key not in (
            'buyer_signature_detected', 'seller_signature_detected',
        ):
            continue
        if value in (False, 'false', 'no', 'absent', 0, '0'):
            role = 'buyer' if 'buyer' in key else 'seller' if 'seller' in key else 'party'
            # Listing-stage files have no buyer; inbound offers have no seller execution yet.
            if role == 'buyer' and not expectations.expect_buyer:
                continue
            if role == 'seller' and not expectations.expect_seller:
                continue
            if inbound_offer and role == 'seller':
                continue
            page = _page_hint(field_meta, key)
            page_bit = f' on page {page}' if page else ''
            if inbound_offer and role == 'buyer':
                message = f'Buyer signature not confirmed{page_bit}.'
            elif inbound_offer:
                message = f'I could not confirm a {role} signature{page_bit}.'
            else:
                message = f'I could not confirm a {role} signature{page_bit}.'
            findings.append(_finding(
                'signature_unconfirmed',
                message,
                field_key=key,
                page=page,
                quote=_quote_hint(field_meta, key),
                severity=DocumentReviewReport.SEVERITY_ATTENTION,
            ))

    # Wrong document type heuristic: slug placeholder vs extracted classification
    classified = data.get('document_classification') or data.get('document_type')
    identity = (data.get('_document_identity') or {}) if isinstance(data, dict) else {}
    identity_slug = _norm_str(identity.get('template_slug') or '')
    filename = _norm_str(
        getattr(document, 'signed_original_filename', None)
        or document.template_name
        or ''
    )
    if classified and document.template_slug:
        class_norm = _norm_str(classified).replace(' ', '-')
        slug_norm = _norm_str(document.template_slug)
        # Soften when identity or filename already agrees with the real form.
        if identity_slug and (
            identity_slug in filename
            or any(part and part in filename for part in identity_slug.split('-')[:3])
            or identity_slug == slug_norm
        ):
            pass
        elif class_norm and slug_norm and class_norm not in slug_norm and slug_norm not in class_norm:
            # Only flag when clearly divergent keywords
            if any(t in class_norm for t in ('lease', 'addendum', 'amendment', 'disclosure')):
                if not any(t in slug_norm for t in class_norm.split('-')[:2]):
                    # Skip financing_addendum vs appraisal filename / TREC 49 noise.
                    if 'appraisal' in filename and 'financ' in class_norm:
                        pass
                    elif inbound_offer and slug_norm in ('completed', 'external', 'custom'):
                        pass
                    else:
                        findings.append(_finding(
                            'wrong_document_type',
                            (
                                f'This file may be misclassified. Extraction suggests '
                                f'“{classified}” but it was uploaded as '
                                f'“{document.template_name or document.template_slug}”.'
                            ),
                            field_key='document_classification',
                            extracted_value=str(classified),
                            crm_value=document.template_slug,
                        ))

    # One useful inbound-offer note on the primary contract only.
    if inbound_offer:
        identity_kind = str(identity.get('kind') or '')
        is_primary = identity_kind == 'purchase_contract' or (
            document.template_slug or ''
        ) in {
            'seller-offer-contract',
            'one-to-four-family-contract',
            'purchase-contract',
        }
        if is_primary and data.get('seller_signature_detected') in (False, 'false', 'no', 0, '0'):
            findings.append(_finding(
                'inbound_offer_not_executed',
                (
                    'Inbound offer — buyer signatures appear present and seller '
                    'execution is not complete yet.'
                ),
                severity=DocumentReviewReport.SEVERITY_ATTENTION,
                field_key='seller_signature_detected',
                page=_page_hint(field_meta, 'seller_signature_detected'),
            ))

    return findings, field_count


def _max_severity(findings: list[dict]) -> str:
    ranks = {
        DocumentReviewReport.SEVERITY_OK: 0,
        DocumentReviewReport.SEVERITY_ATTENTION: 1,
        DocumentReviewReport.SEVERITY_CRITICAL: 2,
    }
    best = DocumentReviewReport.SEVERITY_OK
    for f in findings:
        sev = f.get('severity') or DocumentReviewReport.SEVERITY_ATTENTION
        if ranks.get(sev, 0) > ranks.get(best, 0):
            best = sev
    return best


def _compose_summary(
    *,
    address: str,
    findings: list[dict],
    field_count: int,
    severity: str,
    document_name: str | None = None,
) -> tuple[str, str]:
    """Return (title, summary) with careful operational wording."""
    subject = str(document_name or address)
    if len(subject) > 180:
        subject = f'{subject[:177]}...'
    if severity != DocumentReviewReport.SEVERITY_OK:
        title = f'Review {subject}'
        lines = [finding['message'] for finding in findings[:8]]
        if field_count:
            lines.append(
                f'{field_count} extracted '
                f'{"field is" if field_count == 1 else "fields are"} ready for review. '
                'No transaction changes applied.'
            )
        else:
            lines.append('No transaction changes applied.')
        return title, '\n'.join(lines)

    title = f'Review complete: {subject}'
    summary = (
        f'No obvious CRM conflicts for {address}. '
        f'{field_count} extracted '
        f'{"field still needs" if field_count == 1 else "fields still need"} '
        'approval before terms or deadlines update.'
        if field_count else
        f'No obvious CRM conflicts for {address}. The transaction was not changed.'
    )
    return title, summary


def _notify_targets(transaction: Transaction) -> list[User]:
    users: dict[int, User] = {}
    creator = User.query.get(transaction.created_by_id)
    if creator:
        users[creator.id] = creator
    try:
        assignments = TransactionAssignment.query.filter_by(
            transaction_id=transaction.id,
            organization_id=transaction.organization_id,
        ).all()
    except Exception:
        assignments = []
    for a in assignments:
        if a.role in ('lead_agent', 'transaction_coordinator', 'collaborator'):
            u = User.query.get(a.user_id)
            if u:
                users[u.id] = u
    return list(users.values())


def finalize_document_review(
    *,
    document_id: int,
    org_id: int,
    extraction_run_id: int | None = None,
    extraction_failed: bool = False,
    manual_review_reason: str | None = None,
) -> Optional[DocumentReviewReport]:
    """Create review report + bell/Telegram alerts after extraction settles."""
    doc = TransactionDocument.query.filter_by(
        id=document_id, organization_id=org_id,
    ).first()
    if not doc:
        logger.warning('finalize_document_review: doc %s not found', document_id)
        return None

    transaction = Transaction.query.filter_by(
        id=doc.transaction_id, organization_id=org_id,
    ).first()
    if not transaction:
        return None

    findings, field_count = build_findings(
        transaction=transaction,
        document=doc,
        field_data=doc.field_data if isinstance(doc.field_data, dict) else {},
        extraction_failed=extraction_failed,
        extraction_error=doc.extraction_error,
        manual_review_reason=manual_review_reason,
    )
    severity = _max_severity(findings)
    address = transaction.street_address or f'transaction {transaction.id}'
    document_name = doc.review_filename
    document_type = doc.review_document_type
    document_form = doc.review_form_label
    title, summary = _compose_summary(
        address=address,
        findings=findings,
        field_count=field_count,
        severity=severity,
        document_name=document_name,
    )
    toast_required = severity != DocumentReviewReport.SEVERITY_OK

    report = DocumentReviewReport(
        organization_id=org_id,
        transaction_id=transaction.id,
        document_id=doc.id,
        extraction_run_id=extraction_run_id,
        severity=severity,
        status=DocumentReviewReport.STATUS_OPEN,
        title=title,
        summary=summary,
        findings=findings,
        field_count=field_count,
        toast_required=toast_required,
    )
    db.session.add(report)
    db.session.flush()

    # Offer-linked PDFs deep-link to the package review page (one place).
    action_url = (
        f'/transactions/{transaction.id}/documents/{doc.id}/review'
    )
    try:
        from flask import has_app_context, url_for
        if has_app_context():
            offer_link = SellerOfferDocument.query.filter_by(
                organization_id=org_id,
                transaction_id=transaction.id,
                transaction_document_id=doc.id,
            ).first()
            if offer_link:
                from services.offer_package_review import offer_package_review_url
                action_url = offer_package_review_url(transaction.id, offer_link.offer_id)
            else:
                action_url = url_for(
                    'transactions.document_review_workspace',
                    id=transaction.id,
                    doc_id=doc.id,
                )
    except Exception:
        pass
    icon = 'fa-exclamation-triangle' if toast_required else 'fa-file-alt'
    category = 'document_review'
    Notification.CATEGORIES.setdefault(category, 'Document Review')

    if extraction_failed:
        event_type = 'document_review_failed'
    elif severity != DocumentReviewReport.SEVERITY_OK:
        event_type = 'document_review_needs_attention'
    else:
        event_type = 'document_review_completed'

    dedupe_key = (
        f'extraction_run:{extraction_run_id}'
        if extraction_run_id
        else f'document_review:{report.id}'
    )
    dedupe_bucket = str(extraction_run_id or report.id)

    # Persist report before per-user notifies (create_notification commits).
    db.session.commit()

    from services.document_privacy import may_send_to_telegram
    from services.notification_outbox import NotificationOutboxService

    telegram_allowed = may_send_to_telegram(doc)
    if not telegram_allowed:
        logger.info(
            'Skipping Telegram for document_review doc=%s class=%s',
            doc.id, getattr(doc, 'sensitivity_class', None),
        )

    notification_identity = [document_type, address]
    if document_form:
        notification_identity.insert(1, document_form)
    notification_body = f'{" · ".join(notification_identity)}\n{summary}'

    for user in _notify_targets(transaction):
        try:
            event = NotificationOutboxService.create_event(
                user_id=user.id,
                organization_id=org_id,
                event_type=event_type,
                payload={
                    'title': title,
                    'summary': summary,
                    'body': notification_body,
                    'report_id': report.id,
                    'document_id': doc.id,
                    'document_name': document_name,
                    'document_type': document_type,
                    'document_form': document_form,
                    'transaction_id': transaction.id,
                    'transaction_address': address,
                    'review_url': action_url,
                    'severity': severity,
                    'field_count': field_count,
                    'findings': findings[:20],
                    'telegram_allowed': telegram_allowed,
                },
                priority='high' if toast_required else 'normal',
                dedupe_key=dedupe_key,
                dedupe_bucket=dedupe_bucket,
                related_transaction_id=transaction.id,
                category=category,
            )
            NotificationOutboxService.create_delivery(
                event.id, org_id, 'in_app',
            )
            if telegram_allowed:
                NotificationOutboxService.create_delivery(
                    event.id, org_id, 'telegram',
                )
            db.session.commit()
        except Exception:
            logger.exception(
                'NotificationEvent document_review failed user=%s', user.id,
            )

        try:
            notif = create_notification(
                user_id=user.id,
                organization_id=org_id,
                category=category,
                title=title,
                body=notification_body[:2000],
                icon=icon,
                action_url=action_url,
                respect_preference=True,
            )
            if notif and report.notification_id is None:
                report.notification_id = notif.id
                db.session.commit()
        except Exception:
            logger.exception(
                'In-app document_review notify failed user=%s', user.id,
            )

        # Telegram — optional; banned for confidential / restricted_tenant.
        if not telegram_allowed:
            continue
        try:
            from services.messaging.outbound import notify as telegram_notify
            identity_parts = [f'File: {document_name}', f'Type: {document_type}']
            if document_form:
                identity_parts.append(f'Form: {document_form}')
            identity_parts.append(f'Transaction: {address}')
            identity_text = '\n'.join(identity_parts)
            telegram_body = (
                f'{title}\n\n{identity_text}\n\n{summary}\n\n'
                f'Review: {action_url}\n\n--BOB'
            )
            telegram_notify(
                user,
                category,
                telegram_body,
                respect_quiet_hours=True,
            )
        except Exception:
            logger.exception(
                'Telegram document_review notify failed user=%s', user.id,
            )
    if not extraction_failed and doc.template_slug == 'listing-agreement':
        try:
            _seed_listing_deadline_pack(transaction, doc)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                'Listing deadline pack seed failed doc=%s tx=%s',
                doc.id, transaction.id,
            )

    logger.info(
        'DocumentReviewReport %s created doc=%s event=%s severity=%s findings=%d',
        report.id, document_id, event_type, severity, len(findings),
    )
    return report


def refresh_document_review_findings(
    document_id: int,
    *,
    org_id: int | None = None,
) -> Optional[DocumentReviewReport]:
    """Rebuild findings on the latest report after offer-linking (no new alerts).

    Support PDFs often extract before the primary contract autofiles them onto
    an offer thread; this rewrites stale party-mismatch / seller-sig noise once
    the link exists.
    """
    doc = TransactionDocument.query.get(document_id)
    if not doc:
        return None
    if org_id is not None and doc.organization_id != org_id:
        return None

    transaction = Transaction.query.filter_by(
        id=doc.transaction_id,
        organization_id=doc.organization_id,
    ).first()
    if not transaction:
        return None

    findings, field_count = build_findings(
        transaction=transaction,
        document=doc,
        field_data=doc.field_data if isinstance(doc.field_data, dict) else {},
        extraction_failed=False,
        extraction_error=doc.extraction_error,
    )
    severity = _max_severity(findings)
    address = transaction.street_address or f'transaction {transaction.id}'
    title, summary = _compose_summary(
        address=address,
        findings=findings,
        field_count=field_count,
        severity=severity,
        document_name=doc.review_filename,
    )

    report = (
        DocumentReviewReport.query.filter_by(
            document_id=doc.id,
            organization_id=doc.organization_id,
            transaction_id=transaction.id,
        )
        .order_by(DocumentReviewReport.created_at.desc(), DocumentReviewReport.id.desc())
        .first()
    )
    if report is None:
        report = DocumentReviewReport(
            organization_id=doc.organization_id,
            transaction_id=transaction.id,
            document_id=doc.id,
            status=DocumentReviewReport.STATUS_OPEN,
        )
        db.session.add(report)

    report.severity = severity
    report.title = title
    report.summary = summary
    report.findings = findings
    report.field_count = field_count
    report.toast_required = severity != DocumentReviewReport.SEVERITY_OK
    db.session.flush()
    return report


def dismiss_toast(report_id: int, user_id: int, org_id: int) -> Optional[DocumentReviewReport]:
    report = DocumentReviewReport.query.filter_by(
        id=report_id, organization_id=org_id,
    ).first()
    if not report:
        return None
    report.toast_dismissed_at = datetime.utcnow()
    report.toast_dismissed_by_id = user_id
    if report.status == DocumentReviewReport.STATUS_OPEN:
        report.status = DocumentReviewReport.STATUS_ACKNOWLEDGED
    db.session.commit()
    return report


def resolve_report(report_id: int, user_id: int, org_id: int) -> Optional[DocumentReviewReport]:
    """Mark an operational finding reviewed so it leaves active coordination."""
    report = DocumentReviewReport.query.filter_by(
        id=report_id,
        organization_id=org_id,
    ).first()
    if not report:
        return None
    report.status = DocumentReviewReport.STATUS_RESOLVED
    if report.toast_dismissed_at is None:
        report.toast_dismissed_at = datetime.utcnow()
        report.toast_dismissed_by_id = user_id
    db.session.commit()
    return report


def list_open_reports(transaction_id: int, org_id: int) -> list[DocumentReviewReport]:
    return (
        DocumentReviewReport.query.filter_by(
            transaction_id=transaction_id,
            organization_id=org_id,
        )
        .filter(DocumentReviewReport.status.in_([
            DocumentReviewReport.STATUS_OPEN,
            DocumentReviewReport.STATUS_ACKNOWLEDGED,
        ]))
        .order_by(DocumentReviewReport.created_at.desc())
        .all()
    )


def pending_toasts(transaction_id: int, org_id: int) -> list[DocumentReviewReport]:
    return [
        r for r in list_open_reports(transaction_id, org_id)
        if r.needs_toast
    ]
