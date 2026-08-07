"""One-page offer package review: aggregate terms/docs/findings, confirm once."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from flask import url_for

from models import (
    DocumentReviewReport,
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    Transaction,
    TransactionDocument,
)
from services.document_classification_confirm import mark_auto_filed_offer_confirmation
from services.document_review import refresh_document_review_findings
from services.seller_workflow import apply_offer_terms, create_offer_activity, normalize_offer_terms

logger = logging.getLogger(__name__)

_TERM_FIELDS = (
    'buyer_names',
    'offer_price',
    'earnest_money',
    'option_fee',
    'option_period_days',
    'financing_type',
    'financing_amount',
    'cash_down_payment',
    'proposed_close_date',
    'seller_concessions_amount',
    'title_policy_payer',
    'survey_payer',
    'residential_service_contract',
    'buyer_agent_commission_percent',
    'buyer_agent_commission_flat',
)

_INBOUND_NOISE_CODES = frozenset({
    'party_mismatch',
    'missing_title_email',
    'signature_unconfirmed',
    'seller_signature_missing',
    'missing_seller_signature',
    'missing_seller_signatures',
    'sales_price_format_anomaly',
    'price_format_anomaly',
    'amount_format_check',
})


def offer_package_review_url(transaction_id: int, offer_id: int) -> str:
    try:
        return url_for(
            'transactions.offer_package_review',
            id=transaction_id,
            offer_id=offer_id,
        )
    except Exception:
        return f'/transactions/{transaction_id}/offers/{offer_id}/review'


def _money(value) -> Optional[float]:
    if value is None or value == '':
        return None
    try:
        raw = str(value).replace(',', '').replace('$', '').strip()
        return float(raw)
    except (TypeError, ValueError):
        return None


def _display_money(value) -> str:
    """Format money for review inputs: $440,000."""
    from decimal import Decimal, InvalidOperation
    from services.seller_workflow import _residential_service_amount

    if value in (None, ''):
        return ''
    # RSC / descriptive money-ish fields may still be prose.
    if isinstance(value, str) and any(ch.isalpha() for ch in value):
        extracted = _residential_service_amount(value)
        if extracted in (None, ''):
            return value
        value = extracted
    try:
        amount = Decimal(str(value).replace(',', '').replace('$', '').strip())
    except (InvalidOperation, AttributeError, ValueError):
        return str(value)
    if amount == amount.to_integral_value():
        return f'${amount:,.0f}'
    return f'${amount:,.2f}'


def _display_party(value) -> str:
    from services.seller_workflow import _party_payer_label
    return _party_payer_label(value) or ''


def _display_financing(value) -> str:
    from services.seller_workflow import _financing_type_label
    return _financing_type_label(value) or ''


def _display_percent(value) -> str:
    from decimal import Decimal, InvalidOperation
    if value in (None, ''):
        return ''
    try:
        amount = Decimal(str(value).replace('%', '').strip())
    except (InvalidOperation, AttributeError, ValueError):
        return str(value)
    text = format(amount.normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def _linked_documents(offer: SellerOffer) -> list[tuple[SellerOfferDocument, TransactionDocument]]:
    rows = (
        SellerOfferDocument.query.filter_by(
            organization_id=offer.organization_id,
            offer_id=offer.id,
        )
        .order_by(
            SellerOfferDocument.is_primary_terms_document.desc(),
            SellerOfferDocument.id.asc(),
        )
        .all()
    )
    out = []
    for link in rows:
        doc = TransactionDocument.query.get(link.transaction_document_id)
        if doc:
            out.append((link, doc))
    return out


def _addenda_labels(offer: SellerOffer, links: list[tuple[SellerOfferDocument, TransactionDocument]]) -> list[str]:
    labels = []
    summary = offer.terms_summary if isinstance(offer.terms_summary, dict) else {}
    addenda = summary.get('addenda') if isinstance(summary.get('addenda'), dict) else {}
    for key in addenda:
        labels.append(str(key).replace('_', ' ').title())
    for link, doc in links:
        if link.is_primary_terms_document:
            continue
        name = link.display_name or doc.template_name or doc.signed_original_filename
        if name and name not in labels:
            labels.append(name)
    return labels


def _package_findings(
    *,
    transaction: Transaction,
    links: list[tuple[SellerOfferDocument, TransactionDocument]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen = set()
    for link, doc in links:
        reports = (
            DocumentReviewReport.query.filter_by(
                organization_id=transaction.organization_id,
                transaction_id=transaction.id,
                document_id=doc.id,
            )
            .filter(DocumentReviewReport.status.in_([
                DocumentReviewReport.STATUS_OPEN,
                DocumentReviewReport.STATUS_ACKNOWLEDGED,
            ]))
            .order_by(DocumentReviewReport.created_at.desc())
            .all()
        )
        for report in reports:
            for raw in report.findings or []:
                if not isinstance(raw, dict):
                    continue
                code = str(raw.get('code') or '')
                if code in _INBOUND_NOISE_CODES:
                    continue
                message = str(raw.get('message') or '').strip()
                if not message:
                    continue
                key = (code, message[:120], doc.id)
                if key in seen:
                    continue
                seen.add(key)
                findings.append({
                    'code': code,
                    'message': message,
                    'severity': raw.get('severity') or DocumentReviewReport.SEVERITY_ATTENTION,
                    'page': raw.get('page'),
                    'document_id': doc.id,
                    'document_label': (
                        link.display_name
                        or doc.template_name
                        or doc.signed_original_filename
                        or f'Document {doc.id}'
                    ),
                })
    return findings


def _empty(value) -> bool:
    return value in (None, '', [], {})


def _merge_term(dest: dict[str, Any], key: str, value) -> None:
    if _empty(value):
        return
    if _empty(dest.get(key)):
        dest[key] = value


def _aggregate_terms_from_package(
    offer: SellerOffer,
    links: list[tuple[SellerOfferDocument, TransactionDocument]],
) -> dict[str, Any]:
    """Merge offer columns with extracted fields from every linked package PDF."""
    from services.scoped_document_intake import terms_from_document_field_data
    from services.seller_workflow import (
        _normalized_supporting_payload,
        normalize_offer_terms,
    )

    list_price = None
    try:
        extra = (offer.transaction.extra_data or {}) if offer.transaction else {}
        list_price = extra.get('list_price')
    except Exception:
        list_price = None

    merged: dict[str, Any] = {}
    # Prefer already-confirmed offer columns first for money/deadlines.
    for key, value in (
        ('buyer_names', offer.buyer_names),
        ('offer_price', offer.offer_price),
        ('earnest_money', offer.earnest_money),
        ('option_fee', offer.option_fee),
        ('option_period_days', offer.option_period_days),
        ('financing_type', offer.financing_type),
        ('financing_amount', offer.financing_amount),
        ('cash_down_payment', offer.cash_down_payment),
        ('proposed_close_date', offer.proposed_close_date),
        ('seller_concessions_amount', offer.seller_concessions_amount),
        ('title_policy_payer', offer.title_policy_payer),
        ('survey_payer', offer.survey_payer),
        ('residential_service_contract', offer.residential_service_contract),
        ('buyer_agent_commission_percent', offer.buyer_agent_commission_percent),
        ('buyer_agent_commission_flat', offer.buyer_agent_commission_flat),
        ('buyer_agent_name', offer.buyer_agent_name),
        ('buyer_agent_brokerage', offer.buyer_agent_brokerage),
    ):
        _merge_term(merged, key, value)

    summary = offer.terms_summary if isinstance(offer.terms_summary, dict) else {}
    for key, value in summary.items():
        if key in ('addenda', 'supporting_documents'):
            continue
        _merge_term(merged, key, value)

    # Primary first, then supporting — only fill empties so stronger sources win.
    ordered = sorted(links, key=lambda row: (not row[0].is_primary_terms_document, row[0].id))
    primary_fd: dict[str, Any] = {}
    for link, doc in ordered:
        field_data = doc.field_data if isinstance(doc.field_data, dict) else {}
        if not field_data:
            continue
        if link.is_primary_terms_document:
            primary_fd = field_data
        core = terms_from_document_field_data(field_data, list_price=list_price)
        core.pop('_offer_price_decimal', None)
        for key, value in core.items():
            _merge_term(merged, key, value)
        for key in (
            'title_policy_payer',
            'survey_payer',
            'residential_service_contract',
            'buyer_agent_commission_percent',
            'buyer_agent_commission_flat',
            'buyer_agent_name',
            'buyer_agent_brokerage',
            'seller_concessions_amount',
            'option_period_days',
            'financing_type',
            'financing_amount',
            'total_financing_amount',
            'first_mortgage_amount',
            'cash_down_payment',
        ):
            _merge_term(merged, key, field_data.get(key))

        if not link.is_primary_terms_document:
            normalized = _normalized_supporting_payload(link.document_type, field_data)
            for key, value in (normalized.get('offer_terms') or {}).items():
                _merge_term(merged, key, value)
            if normalized.get('addenda') or normalized.get('supporting_documents'):
                package = {
                    'addenda': {
                        **(merged.get('addenda') or {}),
                        **(normalized.get('addenda') or {}),
                    },
                    'supporting_documents': {
                        **(merged.get('supporting_documents') or {}),
                        **(normalized.get('supporting_documents') or {}),
                    },
                }
                merged.update(package)

    # Contract Other Broker (Associate's Name / firm) wins over compensation-form names.
    for key in (
        'buyer_agent_name',
        'buyer_agent_brokerage',
        'residential_service_contract',
        'title_policy_payer',
        'survey_payer',
    ):
        if not _empty(primary_fd.get(key)):
            merged[key] = primary_fd.get(key)

    merged = normalize_offer_terms(merged)
    if _empty(merged.get('proposed_close_date')) and not _empty(merged.get('closing_date')):
        merged['proposed_close_date'] = merged.get('closing_date')
    if _empty(merged.get('financing_amount')) and not _empty(merged.get('total_financing_amount')):
        merged['financing_amount'] = merged.get('total_financing_amount')
    if _empty(merged.get('offer_price')) and not _empty(merged.get('sales_price')):
        merged['offer_price'] = merged.get('sales_price')
    return merged


def build_offer_package_review(
    *,
    transaction: Transaction,
    offer: SellerOffer,
) -> dict[str, Any]:
    """Payload for the offer package review page / live poll."""
    links = _linked_documents(offer)
    docs_payload = []
    pending = 0
    complete = 0
    for link, doc in links:
        status = (doc.extraction_status or 'pending').lower()
        if status in ('pending', 'processing'):
            pending += 1
        elif status == 'complete':
            complete += 1
        docs_payload.append({
            'link_id': link.id,
            'document_id': doc.id,
            'label': (
                link.display_name
                or doc.template_name
                or doc.signed_original_filename
                or f'Document {doc.id}'
            ),
            'document_type': link.document_type,
            'is_primary': bool(link.is_primary_terms_document),
            'extraction_status': status,
            'template_slug': doc.template_slug,
            'filename': doc.signed_original_filename,
            'pdf_url': url_for(
                'transactions.view_document_pdf',
                id=transaction.id,
                doc_id=doc.id,
            ) if (doc.signed_file_path or doc.source_file_path) else None,
        })

    primary = next((d for d in docs_payload if d['is_primary'] and d.get('pdf_url')), None)
    if primary is None:
        primary = next((d for d in docs_payload if d.get('pdf_url')), None)

    aggregated = _aggregate_terms_from_package(offer, links)
    close_date = aggregated.get('proposed_close_date') or aggregated.get('closing_date')
    if hasattr(close_date, 'isoformat'):
        close_date = close_date.isoformat()

    from services.seller_workflow import _residential_service_amount

    rsc_raw = aggregated.get('residential_service_contract')
    rsc_amount = _residential_service_amount(rsc_raw)

    terms = {
        'buyer_names': aggregated.get('buyer_names') or '',
        'offer_price': _display_money(
            aggregated.get('offer_price') or aggregated.get('sales_price')
        ),
        'earnest_money': _display_money(aggregated.get('earnest_money')),
        'option_fee': _display_money(aggregated.get('option_fee')),
        'option_period_days': aggregated.get('option_period_days'),
        'financing_type': _display_financing(aggregated.get('financing_type')),
        'financing_amount': _display_money(
            aggregated.get('financing_amount') or aggregated.get('total_financing_amount')
        ),
        'cash_down_payment': _display_money(aggregated.get('cash_down_payment')),
        'proposed_close_date': close_date or '',
        'seller_concessions_amount': _display_money(
            aggregated.get('seller_concessions_amount')
        ),
        'title_policy_payer': _display_party(aggregated.get('title_policy_payer')),
        'survey_payer': _display_party(aggregated.get('survey_payer')),
        'residential_service_contract': _display_money(rsc_amount or rsc_raw),
        'buyer_agent_commission_percent': _display_percent(
            aggregated.get('buyer_agent_commission_percent')
        ),
        'buyer_agent_commission_flat': _display_money(
            aggregated.get('buyer_agent_commission_flat')
        ),
        'buyer_agent_name': aggregated.get('buyer_agent_name') or '',
        'buyer_agent_brokerage': aggregated.get('buyer_agent_brokerage') or '',
    }

    return {
        'transaction_id': transaction.id,
        'offer_id': offer.id,
        'status': offer.status,
        'buyer_names': terms.get('buyer_names') or offer.buyer_names,
        'offer_price': _money(aggregated.get('offer_price') or offer.offer_price),
        'terms': terms,
        'addenda': _addenda_labels(offer, links),
        'documents': docs_payload,
        'primary_document_id': primary['document_id'] if primary else None,
        'primary_pdf_url': primary['pdf_url'] if primary else None,
        'findings': _package_findings(transaction=transaction, links=links),
        'extraction': {
            'total': len(docs_payload),
            'complete': complete,
            'pending': pending,
            'done': pending == 0 and len(docs_payload) > 0,
        },
        'confirm_url': url_for(
            'transactions.confirm_offer_package_review',
            id=transaction.id,
            offer_id=offer.id,
        ),
        'live_url': url_for(
            'transactions.offer_package_review_live',
            id=transaction.id,
            offer_id=offer.id,
        ),
        'return_url': url_for('transactions.view_transaction', id=transaction.id) + '#offers',
    }


def _coerce_terms_input(data: dict[str, Any]) -> dict[str, Any]:
    terms = {}
    for key in _TERM_FIELDS:
        if key not in data:
            continue
        value = data.get(key)
        if value in (None, ''):
            continue
        terms[key] = value
    for key in ('buyer_agent_name', 'buyer_agent_brokerage', 'buyer_agent_email', 'buyer_agent_phone'):
        if key in data and data.get(key) not in (None, ''):
            terms[key] = data.get(key)
    # Alias sales_price → offer_price for form convenience.
    if 'offer_price' not in terms and data.get('sales_price') not in (None, ''):
        terms['offer_price'] = data.get('sales_price')
    return normalize_offer_terms(terms)


def confirm_offer_package(
    *,
    offer: SellerOffer,
    actor_id: int,
    terms_dict: dict[str, Any] | None = None,
    draft: bool = False,
) -> SellerOffer:
    """Save package terms and mark all linked docs filed to this offer."""
    terms = _coerce_terms_input(terms_dict or {})

    if 'buyer_names' in (terms_dict or {}):
        offer.buyer_names = (terms_dict or {}).get('buyer_names') or offer.buyer_names
    for field in ('buyer_agent_name', 'buyer_agent_brokerage', 'buyer_agent_email', 'buyer_agent_phone'):
        if field in (terms_dict or {}):
            setattr(offer, field, (terms_dict or {}).get(field) or None)

    version = None
    if offer.current_version_id:
        version = SellerOfferVersion.query.filter_by(
            id=offer.current_version_id,
            offer_id=offer.id,
            organization_id=offer.organization_id,
        ).first()
    if version:
        merged = dict(version.terms_data or {})
        merged.update(terms)
        version.terms_data = merged
        version.status = 'reviewed'
        version.extraction_reviewed_at = datetime.utcnow()
        version.extraction_reviewed_by_id = actor_id
        apply_offer_terms(offer, merged)
    else:
        apply_offer_terms(offer, terms)

    if not draft and offer.status in ('new', 'needs_review', 'draft'):
        offer.status = 'reviewing'

    links = _linked_documents(offer)
    for link, doc in links:
        mark_auto_filed_offer_confirmation(
            doc,
            actor_id=actor_id,
            offer_id=offer.id,
            template_slug=doc.template_slug,
        )
        refresh_document_review_findings(doc.id, org_id=offer.organization_id)
        _quiet_inbound_reports(doc)

    create_offer_activity(
        offer,
        'offer_package_confirmed' if not draft else 'offer_package_draft_saved',
        'Offer package confirmed' if not draft else 'Offer package draft saved',
        actor_id=actor_id,
        version_id=version.id if version else None,
        event_data={
            'document_ids': [doc.id for _, doc in links],
            'draft': bool(draft),
        },
    )
    return offer


def _quiet_inbound_reports(doc: TransactionDocument) -> None:
    """Acknowledge open reports that only contain inbound-offer noise."""
    reports = (
        DocumentReviewReport.query.filter_by(
            organization_id=doc.organization_id,
            document_id=doc.id,
        )
        .filter(DocumentReviewReport.status.in_([
            DocumentReviewReport.STATUS_OPEN,
            DocumentReviewReport.STATUS_ACKNOWLEDGED,
        ]))
        .all()
    )
    for report in reports:
        findings = [f for f in (report.findings or []) if isinstance(f, dict)]
        remaining = [
            f for f in findings
            if str(f.get('code') or '') not in _INBOUND_NOISE_CODES
        ]
        report.findings = remaining
        if not remaining:
            report.status = DocumentReviewReport.STATUS_RESOLVED
            report.severity = DocumentReviewReport.SEVERITY_OK
            report.toast_required = False
            if report.toast_dismissed_at is None:
                report.toast_dismissed_at = datetime.utcnow()
        else:
            # Keep useful findings, but don't keep nagging via toast after package confirm.
            report.status = DocumentReviewReport.STATUS_ACKNOWLEDGED
            report.toast_required = False
