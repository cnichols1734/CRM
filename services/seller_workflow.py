"""Seller transaction workflow helpers.

These functions keep deadline math and lifecycle transitions out of route
handlers. They intentionally do not commit; callers should commit after the
surrounding action and audit records are complete.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import logging
import re

from models import (
    SellerAcceptedContract,
    SellerClosingSummary,
    SellerContractMilestone,
    SellerContractTermination,
    SellerContractDocument,
    SellerOffer,
    SellerOfferActivity,
    SellerOfferDocument,
    SellerOfferVersion,
    TransactionDocument,
    db,
)

logger = logging.getLogger(__name__)


ACTIVE_OFFER_STATUSES = {
    'new',
    'reviewing',
    'needs_review',
    'countered',
}


OFFER_DOCUMENT_TYPES = {
    'offer_package': {
        'label': 'Offer Package',
        'template_slug': 'seller-offer-contract',
        'direction': 'buyer_offer',
        'primary_terms': True,
    },
    'buyer_offer': {
        'label': 'Offer Contract',
        'template_slug': 'seller-offer-contract',
        'direction': 'buyer_offer',
        'primary_terms': True,
    },
    'seller_counter': {
        'label': 'Seller Counter Offer',
        'template_slug': 'seller-counter-offer',
        'direction': 'seller_counter',
        'primary_terms': True,
    },
    'buyer_counter': {
        'label': 'Buyer Counter Offer',
        'template_slug': 'seller-counter-offer',
        'direction': 'buyer_counter',
        'primary_terms': True,
    },
    'final_acceptance': {
        'label': 'Executed Contract',
        'template_slug': 'seller-accepted-contract',
        'direction': 'final_acceptance',
        'primary_terms': True,
    },
    'backup_acceptance': {
        'label': 'Backup Addendum',
        'template_slug': 'seller-backup-addendum',
        'direction': 'backup_acceptance',
        'primary_terms': True,
    },
    'sellers_disclosure': {
        'label': "Seller's Disclosure Notice",
        'template_slug': 'sellers-disclosure',
        'direction': None,
        'primary_terms': False,
    },
    'hoa_addendum': {
        'label': 'HOA Addendum',
        'template_slug': 'hoa-addendum',
        'direction': None,
        'primary_terms': False,
    },
    'pre_approval': {
        'label': 'Mortgage Pre-Approval',
        'template_slug': 'pre-approval-or-proof-of-funds',
        'direction': None,
        'primary_terms': False,
    },
    'third_party_financing': {
        'label': 'Third Party Financing Addendum',
        'template_slug': 'third-party-financing-addendum',
        'direction': None,
        'primary_terms': False,
    },
    'appraisal_termination': {
        'label': "Appraisal Termination Addendum",
        'template_slug': 'appraisal-termination-addendum',
        'direction': None,
        'primary_terms': False,
    },
    'broker_compensation': {
        'label': 'Broker Compensation Agreement',
        'template_slug': 'broker-compensation-agreement',
        'direction': None,
        'primary_terms': False,
    },
}

_SLUG_TO_OFFER_DOCUMENT_TYPE = {
    'seller-offer-contract': 'buyer_offer',
    'one-to-four-family-contract': 'buyer_offer',
    'condominium-contract': 'buyer_offer',
    'new-home-completed-construction-contract': 'buyer_offer',
    'new-home-incomplete-construction-contract': 'buyer_offer',
    'farm-and-ranch-contract': 'buyer_offer',
    'unimproved-property-contract': 'buyer_offer',
    'purchase-contract': 'buyer_offer',
    'seller-accepted-contract': 'final_acceptance',
    'third-party-financing-addendum': 'third_party_financing',
    'hoa-addendum': 'hoa_addendum',
    'seller-backup-addendum': 'backup_acceptance',
    'sellers-disclosure': 'sellers_disclosure',
    'pre-approval-or-proof-of-funds': 'pre_approval',
    'appraisal-termination-addendum': 'appraisal_termination',
    'broker-compensation-agreement': 'broker_compensation',
}


def get_offer_document_type(document_type):
    """Return normalized offer document type metadata."""
    return OFFER_DOCUMENT_TYPES.get(document_type) or OFFER_DOCUMENT_TYPES['buyer_offer']


def infer_offer_document_type(filename='', explicit_type=None):
    """Infer the offer package document type from an explicit choice or filename."""
    if explicit_type in OFFER_DOCUMENT_TYPES:
        return explicit_type

    normalized = re.sub(r'[^a-z0-9]+', ' ', (filename or '').lower()).strip()
    tokens = set(normalized.split())

    if 'third' in tokens and 'financing' in tokens:
        return 'third_party_financing'
    if (
        'preapproval' in tokens
        or {'pre', 'approval'} <= tokens
        or 'prequal' in tokens
        or {'pre', 'qual'} <= tokens
        or 'prequalification' in tokens
    ):
        return 'pre_approval'
    if (
        'hoa' in tokens
        or {'owners', 'association'} <= tokens
        or {'property', 'subject', 'mandatory'} <= tokens
        or {'mandatory', 'membership'} <= tokens
    ):
        return 'hoa_addendum'
    if (
        {'seller', 'disclosure'} <= tokens
        or {'sellers', 'disclosure'} <= tokens
        or 'sd' in tokens
    ):
        return 'sellers_disclosure'
    if 'backup' in tokens:
        return 'backup_acceptance'
    if 'executed' in tokens or 'signed' in tokens or 'acceptance' in tokens:
        return 'final_acceptance'
    if 'counter' in tokens:
        return 'seller_counter' if 'seller' in tokens else 'buyer_counter'
    if (
        'contract' in tokens
        or 'resale' in tokens
        or {'one', 'four', 'family'} <= tokens
        or {'residential', 'contract'} <= tokens
    ):
        return 'buyer_offer'

    return 'buyer_offer'


def infer_offer_document_type_from_text(text='', filename='', explicit_type=None):
    """Prefer document text over filenames/dropdown guesses when classifying uploads."""
    normalized = re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()
    has_contract = (
        'one to four family residential contract' in normalized
        or ('sales price' in normalized and 'trec no 20' in normalized)
    )
    has_tpf = 'third party financing addendum' in normalized
    has_hoa = (
        'addendum for property subject to mandatory membership' in normalized
        or 'property owners association' in normalized
        or 'home owners association' in normalized
    )
    has_disclosure = (
        'seller disclosure notice' in normalized
        or 'seller s disclosure notice' in normalized
    )
    has_preapproval = (
        'pre approval' in normalized
        or 'preapproval' in normalized
        or 'pre qualification' in normalized
        or 'prequalification' in normalized
    )
    has_backup = 'addendum for back up contract' in normalized or 'backup contract' in normalized

    if has_contract:
        if has_tpf or has_hoa or has_disclosure:
            return 'offer_package'
        if explicit_type in ('buyer_offer', 'seller_counter', 'buyer_counter', 'final_acceptance'):
            return explicit_type
        return 'buyer_offer'
    if has_tpf:
        return 'third_party_financing'
    if has_hoa:
        return 'hoa_addendum'
    if has_disclosure:
        return 'sellers_disclosure'
    if has_preapproval:
        return 'pre_approval'
    if has_backup:
        return 'backup_acceptance'
    return infer_offer_document_type(filename, explicit_type)


def infer_offer_document_type_from_pdf(file_data, filename='', explicit_type=None):
    """Infer upload type from PDF text, falling back to filename/dropdown metadata."""
    try:
        import fitz

        chunks = []
        doc = fitz.open(stream=file_data, filetype='pdf')
        try:
            for page in doc:
                chunks.append(page.get_text('text') or '')
        finally:
            doc.close()
        return infer_offer_document_type_from_text(' '.join(chunks), filename, explicit_type)
    except Exception:
        return infer_offer_document_type(filename, explicit_type)


def _coerce_decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).replace(',', '').replace('$', '').strip())
    except (InvalidOperation, AttributeError):
        return None


def _coerce_int(value):
    if value in (None, ''):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_bool(value):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ('true', 'yes', '1'):
            return True
        if lowered in ('false', 'no', '0'):
            return False
    return None


def _coerce_text(value):
    """Return a DB-safe display string for scalar text columns."""
    if value in (None, ''):
        return None
    if isinstance(value, (list, tuple)):
        parts = [_coerce_text(item) for item in value]
        return ', '.join(part for part in parts if part)
    if isinstance(value, dict):
        parts = [_coerce_text(item) for item in value.values()]
        return ', '.join(part for part in parts if part) or None
    return str(value).strip() or None


_PARTY_LABELS = {
    'buyer': 'Buyer',
    'seller': 'Seller',
    'split': 'Split',
    'both': 'Split',
    'shared': 'Split',
}


def _party_payer_label(value):
    text = _coerce_text(value)
    if not text:
        return None
    key = re.sub(r'[^a-z]+', '', text.lower())
    if key in _PARTY_LABELS:
        return _PARTY_LABELS[key]
    return text[:1].upper() + text[1:] if text else None


def _financing_type_label(value):
    text = _coerce_text(value)
    if not text:
        return None
    known = {
        'cash': 'Cash',
        'conventional': 'Conventional',
        'fha': 'FHA',
        'va': 'VA',
        'usda': 'USDA',
        'texasveterans': 'Texas Veterans',
        'reversemortgage': 'Reverse mortgage',
        'sellerfinancing': 'Seller financing',
        'other': 'Other',
    }
    key = re.sub(r'[^a-z]+', '', text.lower())
    if key in known:
        return known[key]
    return text[:1].upper() + text[1:]


def _residential_service_amount(value):
    """Keep Paragraph 7.H as a dollar amount when possible."""
    if value in (None, ''):
        return None
    amount = _coerce_decimal(value)
    if amount is not None:
        if amount == amount.to_integral_value():
            return str(int(amount))
        return format(amount.quantize(Decimal('0.01')), 'f')
    text = _coerce_text(value) or ''
    match = re.search(
        r'(?:not\s+exceeding|up\s+to|amount\s+of|reimburse(?:\s+\w+){0,6}\s+)?\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)',
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return text or None
    amount = _coerce_decimal(match.group(1))
    if amount is None:
        return text
    if amount == amount.to_integral_value():
        return str(int(amount))
    return format(amount.quantize(Decimal('0.01')), 'f')


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        return value
    if isinstance(value, str):
        for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y'):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, 'year') and hasattr(value, 'month') and hasattr(value, 'day'):
        return datetime.combine(value, time(17, 0))
    if isinstance(value, str):
        for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
    return None


def _as_datetime(date_value, default_time=time(17, 0)):
    if not date_value:
        return None
    if isinstance(date_value, datetime):
        return date_value
    return datetime.combine(date_value, default_time)


def _sum_financing_amount(data):
    first = _coerce_decimal((data or {}).get('first_mortgage_amount'))
    second = _coerce_decimal((data or {}).get('second_mortgage_amount'))
    if first is None and second is None:
        return None
    return str((first or Decimal('0')) + (second or Decimal('0')))


def derive_financing_approval_deadline(terms, effective_date=None):
    """Calculate buyer approval deadline from explicit dates or TPF paragraph 2A days."""
    terms = normalize_offer_terms(terms)
    addenda = _json_object(terms.get('addenda'))
    supporting = _json_object(terms.get('supporting_documents'))
    financing_addendum = (
        _json_object(addenda.get('third_party_financing_addendum'))
        or _json_object(supporting.get('third_party_financing'))
    )

    explicit_deadline = (
        terms.get('financing_approval_deadline')
        or financing_addendum.get('financing_approval_deadline')
        or financing_addendum.get('buyer_approval_deadline')
    )
    parsed_deadline = _parse_date(explicit_deadline)
    if parsed_deadline:
        return parsed_deadline

    parsed_effective_date = _parse_date(effective_date)
    approval_days = _coerce_int(financing_addendum.get('buyer_approval_days'))
    if parsed_effective_date and approval_days is not None:
        return parsed_effective_date + timedelta(days=approval_days)
    return None


def create_offer_activity(offer, event_type, label, actor_id=None, version_id=None, document_id=None, event_data=None):
    """Create an offer activity row and update offer summary fields."""
    created_at = datetime.utcnow()
    activity = SellerOfferActivity(
        organization_id=offer.organization_id,
        transaction_id=offer.transaction_id,
        offer_id=offer.id,
        version_id=version_id,
        document_id=document_id,
        actor_id=actor_id,
        event_type=event_type,
        label=label,
        event_data=event_data or {},
        created_at=created_at,
    )
    offer.last_activity_at = created_at
    offer.last_activity_label = label
    db.session.add(activity)
    return activity


def offer_urgency(offer, now=None):
    """Return a small urgency descriptor for offer deadline sorting and badges."""
    now = now or datetime.utcnow()
    deadline = offer.response_deadline_at
    if not deadline:
        return {'rank': 50, 'label': 'No deadline', 'state': 'none', 'hours_remaining': None}

    seconds = (deadline - now).total_seconds()
    hours = seconds / 3600

    if seconds <= 0:
        return {'rank': 0, 'label': 'Expired', 'state': 'expired', 'hours_remaining': hours}
    if hours <= 1:
        return {'rank': 1, 'label': 'Due within 1 hour', 'state': 'critical', 'hours_remaining': hours}
    if hours <= 4:
        return {'rank': 2, 'label': 'Due within 4 hours', 'state': 'strong_warning', 'hours_remaining': hours}
    if hours <= 24:
        return {'rank': 3, 'label': 'Due within 24 hours', 'state': 'warning', 'hours_remaining': hours}
    return {'rank': 10, 'label': 'Deadline set', 'state': 'normal', 'hours_remaining': hours}


def expire_offer_if_needed(offer, now=None, actor_id=None):
    """Mark an untouched active offer expired once its response deadline passes."""
    now = now or datetime.utcnow()
    if (
        offer.status in ACTIVE_OFFER_STATUSES
        and offer.response_deadline_at
        and offer.response_deadline_at <= now
    ):
        offer.status = 'expired'
        offer.expired_at = now
        offer.next_action = None
        offer.next_deadline_at = None
        create_offer_activity(
            offer,
            'expired',
            'Offer expired without seller response',
            actor_id=actor_id,
            event_data={'response_deadline_at': offer.response_deadline_at.isoformat()},
        )
        return True
    return False


def _money_was_written_with_separators(value) -> bool:
    """True when the extracted value still carries human money formatting.

    ``$10,000.00`` and ``10000.00`` are amounts someone read off a page and
    copied verbatim. A bare integer such as ``1000000`` is the shape a stripped
    decimal leaves behind. Only the bare form is a cents-blowup candidate, so
    formatted values are never rescaled.
    """
    if isinstance(value, float):
        return True
    text = str(value or '').strip()
    return any(marker in text for marker in ('.', ',', '$'))


def _normalize_extracted_money(
    value,
    *,
    list_price=None,
    offer_price=None,
    field_role='price',
):
    """Fix digits-only OCR blowups ($440,000.00 → 44000000) without inventing them.

    ``field_role``:
      - ``price``: sales/offer price only (large-magnitude / list-price heuristics)
      - ``ancillary``: earnest money and concessions, which scale with the price
        and are routinely five figures
      - ``fee``: option fee and flat commissions, which stay small no matter
        what the house costs
      - ``financing``: loan / cash-down amounts (often near offer price; only fix
        clear cents blowups, never shrink a plausible mortgage amount)

    A correction is only applied when the amount is implausible for its field
    AND arrived without money formatting. Rescaling a plausible amount is far
    more damaging than leaving a rare blowup alone: it silently turns a $30,000
    earnest deposit into $300 while looking like a successful extraction.
    """
    amount = _coerce_decimal(value)
    if amount is None or amount <= 0:
        return None
    exact = amount.quantize(Decimal('0.01')) if amount == amount.to_integral_value() else amount
    formatted = _money_was_written_with_separators(value)
    reference = _coerce_decimal(list_price) or _coerce_decimal(offer_price)

    if not formatted and (
        amount >= Decimal('10000000')
        or (reference and reference > 0 and amount > reference * Decimal('5'))
    ):
        candidate = (amount / Decimal('100')).quantize(Decimal('0.01'))
        if candidate >= Decimal('1000') and (
            not reference or candidate <= reference * Decimal('3')
        ):
            return candidate

    if field_role == 'financing':
        if (
            not formatted
            and reference
            and reference > 0
            and amount > reference
        ):
            # $352,000.00 → 35200000 against a $440,000 offer.
            candidate = (amount / Decimal('100')).quantize(Decimal('0.01'))
            if Decimal('1000') <= candidate <= reference:
                return candidate
        return exact

    if field_role in ('ancillary', 'fee') and reference and reference > 0:
        # Earnest money and concessions are a share of the price, so five
        # figures is normal on a mid-priced home. An option fee stays small
        # regardless. Only an amount past what the field could plausibly hold
        # is treated as a blowup.
        ceiling = (
            reference * Decimal('0.25')
            if field_role == 'ancillary'
            else max(Decimal('5000'), reference * Decimal('0.02'))
        )
        if not formatted and amount > ceiling:
            candidate = (amount / Decimal('100')).quantize(Decimal('0.01'))
            if Decimal('1') <= candidate <= ceiling:
                return candidate

    return exact


def apply_offer_terms(offer, terms):
    """Copy reviewed/extracted terms into canonical offer comparison columns."""
    terms = normalize_offer_terms(terms)
    list_price = terms.get('list_price')
    offer_price = _normalize_extracted_money(
        terms.get('offer_price') or terms.get('sales_price'),
        list_price=list_price,
        field_role='price',
    )
    offer.offer_price = offer_price
    offer.financing_type = _financing_type_label(terms.get('financing_type'))
    offer.cash_down_payment = _normalize_extracted_money(
        terms.get('cash_down_payment'),
        list_price=list_price,
        offer_price=offer_price,
        field_role='financing',
    ) or _coerce_decimal(terms.get('cash_down_payment'))
    offer.financing_amount = _normalize_extracted_money(
        terms.get('financing_amount') or terms.get('total_financing_amount'),
        list_price=list_price,
        offer_price=offer_price,
        field_role='financing',
    ) or _coerce_decimal(
        terms.get('financing_amount') or terms.get('total_financing_amount')
    )
    offer.earnest_money = _normalize_extracted_money(
        terms.get('earnest_money'),
        list_price=list_price,
        offer_price=offer_price,
        field_role='ancillary',
    ) or _coerce_decimal(terms.get('earnest_money'))
    offer.additional_earnest_money = _normalize_extracted_money(
        terms.get('additional_earnest_money'),
        list_price=list_price,
        offer_price=offer_price,
        field_role='ancillary',
    ) or _coerce_decimal(terms.get('additional_earnest_money'))
    offer.option_fee = _normalize_extracted_money(
        terms.get('option_fee'),
        list_price=list_price,
        offer_price=offer_price,
        field_role='fee',
    ) or _coerce_decimal(terms.get('option_fee'))
    offer.option_period_days = _coerce_int(terms.get('option_period_days'))
    offer.seller_concessions_amount = _normalize_extracted_money(
        terms.get('seller_concessions_amount'),
        list_price=list_price,
        offer_price=offer_price,
        field_role='ancillary',
    ) or _coerce_decimal(terms.get('seller_concessions_amount'))
    offer.proposed_close_date = _parse_date(terms.get('proposed_close_date') or terms.get('closing_date'))
    offer.possession_type = _coerce_text(terms.get('possession_type'))
    offer.leaseback_days = _coerce_int(terms.get('leaseback_days'))
    offer.appraisal_contingency = _coerce_bool(terms.get('appraisal_contingency'))
    offer.financing_contingency = _coerce_bool(terms.get('financing_contingency'))
    offer.sale_of_other_property_contingency = _coerce_bool(terms.get('sale_of_other_property_contingency'))
    offer.inspection_or_repair_terms_summary = _coerce_text(terms.get('inspection_or_repair_terms_summary'))
    offer.title_policy_payer = _party_payer_label(terms.get('title_policy_payer'))
    offer.survey_payer = _party_payer_label(terms.get('survey_payer'))
    offer.survey_furnished_by = _coerce_text(terms.get('survey_furnished_by'))
    offer.hoa_resale_certificate_payer = _party_payer_label(
        terms.get('hoa_resale_certificate_payer')
    )
    offer.residential_service_contract = _residential_service_amount(
        terms.get('residential_service_contract')
    )
    offer.buyer_agent_commission_percent = _coerce_decimal(terms.get('buyer_agent_commission_percent'))
    offer.buyer_agent_commission_flat = _normalize_extracted_money(
        terms.get('buyer_agent_commission_flat'),
        list_price=list_price,
        offer_price=offer_price,
        field_role='fee',
    ) or _coerce_decimal(terms.get('buyer_agent_commission_flat'))
    offer.response_deadline_at = _parse_datetime(terms.get('response_deadline_at')) or offer.response_deadline_at
    existing_terms = dict(offer.terms_summary or {})
    summary_terms = dict(terms)
    if existing_terms.get('supporting_documents') and 'supporting_documents' not in summary_terms:
        summary_terms['supporting_documents'] = existing_terms['supporting_documents']
    if existing_terms.get('addenda') or summary_terms.get('addenda'):
        merged_addenda = dict(existing_terms.get('addenda') or {})
        merged_addenda.update(summary_terms.get('addenda') or {})
        summary_terms['addenda'] = merged_addenda
    offer.terms_summary = summary_terms
    offer.next_deadline_at = offer.response_deadline_at
    return offer


def _merge_existing_offer_context(offer, terms):
    """Preserve supporting document context when a primary contract re-syncs."""
    merged = dict(terms or {})
    existing = dict(offer.terms_summary or {}) if offer else {}
    if existing.get('supporting_documents') and 'supporting_documents' not in merged:
        merged['supporting_documents'] = existing['supporting_documents']
    if existing.get('addenda') or merged.get('addenda'):
        addenda = dict(existing.get('addenda') or {})
        addenda.update(merged.get('addenda') or {})
        merged['addenda'] = addenda
    return normalize_offer_terms(merged)


def _normalized_supporting_payload(document_type, extracted):
    """Map supporting document extraction into offer terms namespaces."""
    extracted = dict(extracted or {})
    if document_type == 'third_party_financing':
        financing_amount = extracted.get('total_financing_amount') or _sum_financing_amount(extracted)
        return {
            'offer_terms': {
                'financing_type': extracted.get('financing_type'),
                'financing_contingency': extracted.get('buyer_approval_required'),
                'financing_amount': financing_amount,
            },
            'addenda': {
                'third_party_financing_addendum': extracted,
            },
            'supporting_documents': {
                document_type: extracted,
            },
        }
    if document_type == 'hoa_addendum':
        return {
            'offer_terms': {
                'hoa_applicable': True,
                'hoa_resale_certificate_payer': extracted.get('title_company_info_payer'),
            },
            'addenda': {
                'hoa_addendum': extracted,
            },
            'supporting_documents': {
                document_type: extracted,
            },
        }
    if document_type == 'sellers_disclosure':
        return {
            'offer_terms': {
                'seller_disclosure_required': True,
                'lead_based_paint_required': extracted.get('built_before_1978'),
            },
            'supporting_documents': {
                document_type: extracted,
            },
        }
    if document_type == 'pre_approval':
        return {
            'supporting_documents': {
                document_type: extracted,
            },
        }
    if document_type == 'broker_compensation':
        # Commission only — associate/firm names come from the purchase contract
        # Other Broker section (Associate's Name), not this form's cooperating-broker line.
        return {
            'offer_terms': {
                'buyer_agent_commission_percent': extracted.get(
                    'buyer_agent_commission_percent'
                ),
                'buyer_agent_commission_flat': extracted.get(
                    'buyer_agent_commission_flat'
                ),
            },
            'supporting_documents': {
                document_type: extracted,
            },
        }
    return {
        'supporting_documents': {
            document_type: extracted,
        },
    }


def merge_offer_supporting_document(offer_document):
    """Merge a supporting offer document extraction into its offer package."""
    if not offer_document or not offer_document.document or not offer_document.document.field_data:
        return None
    if offer_document.is_primary_terms_document:
        offer_document.extraction_summary = dict(offer_document.document.field_data or {})
        return offer_document

    from sqlalchemy.orm.attributes import flag_modified

    offer = offer_document.offer
    if not offer:
        return None

    extracted = dict(offer_document.document.field_data or {})
    normalized = _normalized_supporting_payload(offer_document.document_type, extracted)
    terms = dict(offer.terms_summary or {})

    supporting = dict(terms.get('supporting_documents') or {})
    supporting.update(normalized.get('supporting_documents') or {})
    terms['supporting_documents'] = supporting

    if normalized.get('addenda'):
        addenda = dict(terms.get('addenda') or {})
        addenda.update(normalized['addenda'])
        terms['addenda'] = addenda

    for key, value in (normalized.get('offer_terms') or {}).items():
        if value is not None:
            terms[key] = value

    offer_document.extraction_summary = extracted
    offer.terms_summary = terms
    apply_offer_terms(offer, terms)

    if offer.current_version_id:
        version = SellerOfferVersion.query.filter_by(
            id=offer.current_version_id,
            offer_id=offer.id,
            organization_id=offer.organization_id,
        ).first()
        if version:
            version_terms = dict(version.terms_data or {})
            version_terms.update(terms)
            version.terms_data = version_terms
            flag_modified(version, 'terms_data')

    flag_modified(offer, 'terms_summary')
    flag_modified(offer_document, 'extraction_summary')
    create_offer_activity(
        offer,
        'extraction_completed',
        f'{offer_document.display_name} details extracted',
        version_id=offer_document.offer_version_id,
        document_id=offer_document.transaction_document_id,
        event_data={'field_count': len(extracted), 'document_type': offer_document.document_type},
    )
    return offer_document


SPLIT_DOCUMENT_TYPE_TO_OFFER_TYPE = {
    'buyer_offer': 'buyer_offer',
    'residential_contract': 'buyer_offer',
    'one_to_four_family': 'buyer_offer',
    'farm_and_ranch': 'buyer_offer',
    'new_home': 'buyer_offer',
    'unimproved_property': 'buyer_offer',
    'third_party_financing': 'third_party_financing',
    'third_party_financing_addendum': 'third_party_financing',
    'hoa_addendum': 'hoa_addendum',
    'sellers_disclosure': 'sellers_disclosure',
    'seller_disclosure': 'sellers_disclosure',
    'pre_approval': 'pre_approval',
    'preapproval': 'pre_approval',
    'backup_addendum': 'backup_acceptance',
    'backup_acceptance': 'backup_acceptance',
    'lead_based_paint': 'sellers_disclosure',
    'compensation_agreement': 'broker_compensation',
    'broker_compensation': 'broker_compensation',
    'appraisal_termination': 'appraisal_termination',
    'appraisal_addendum': 'appraisal_termination',
}
# Addenda with no canonical slot of their own. Mapping them onto the contract
# made the splitter drop them as duplicate primaries, so they file as
# unidentified children and the package UI asks for a human classification.
# sale_of_other_property, temporary_lease, and anything else land here.


def _split_segment_to_offer_type(segment_type):
    if not segment_type:
        return None
    return SPLIT_DOCUMENT_TYPE_TO_OFFER_TYPE.get(segment_type)


def _inherited_field_data_for_segment(segment_type, parent_field_data):
    """Pick the parent extraction subset that should appear on a split child."""
    if not isinstance(parent_field_data, dict):
        return {}
    addenda = _json_object(parent_field_data.get('addenda'))
    supporting = _json_object(parent_field_data.get('supporting_documents'))
    offer_offer_type = _split_segment_to_offer_type(segment_type)

    if offer_offer_type == 'third_party_financing':
        payload = (
            _json_object(addenda.get('third_party_financing_addendum'))
            or _json_object(supporting.get('third_party_financing'))
        )
        if payload:
            return dict(payload)
    if offer_offer_type == 'hoa_addendum':
        payload = _json_object(addenda.get('hoa_addendum')) or _json_object(supporting.get('hoa_addendum'))
        if payload:
            return dict(payload)
    if offer_offer_type == 'sellers_disclosure':
        payload = _json_object(supporting.get('sellers_disclosure'))
        if payload:
            return dict(payload)
    if offer_offer_type == 'pre_approval':
        payload = _json_object(supporting.get('pre_approval'))
        if payload:
            return dict(payload)
    if offer_offer_type == 'backup_acceptance':
        payload = (
            _json_object(addenda.get('backup_addendum'))
            or _json_object(supporting.get('backup_addendum'))
        )
        if payload:
            return dict(payload)
    return {}


def split_offer_package_into_children(doc_id, file_data, *, split_source='ai_packet_split'):
    """Create split child documents under an offer packet parent.

    Reads ``detected_documents`` from the parent's ``field_data``, slices the
    PDF using PyMuPDF, uploads each child PDF to Supabase, and links a
    matching ``TransactionDocument`` + ``SellerOfferDocument`` to the same
    offer. Skips when only one segment is detected (no real packet) or when
    children already exist for this parent.
    """
    if not file_data:
        return []

    doc = TransactionDocument.query.get(doc_id)
    if not doc or not doc.field_data:
        return []

    offer_document = SellerOfferDocument.query.filter_by(transaction_document_id=doc.id).first()
    if not offer_document or not offer_document.is_primary_terms_document:
        return []

    parent_offer_type = offer_document.document_type
    if parent_offer_type not in ('offer_package', 'buyer_offer'):
        return []

    detected = doc.field_data.get('detected_documents') if isinstance(doc.field_data, dict) else None
    if not isinstance(detected, list) or not detected:
        return []

    from services.pdf_splitter import (
        get_pdf_page_count,
        normalize_segments,
        split_pdf_by_segments,
    )

    total_pages = get_pdf_page_count(file_data)
    if total_pages <= 0:
        return []

    segments = normalize_segments(detected, total_pages=total_pages)
    if len(segments) < 2:
        return []

    existing_children = TransactionDocument.query.filter_by(parent_document_id=doc.id).count()
    if existing_children:
        return []

    from services.supabase_storage import upload_external_document

    offer = offer_document.offer
    organization_id = doc.organization_id
    transaction_id = doc.transaction_id
    primary_assigned = False
    created_children = []

    base_filename = doc.signed_original_filename or 'offer_packet.pdf'
    name_root, _, ext = base_filename.rpartition('.')
    name_root = name_root or 'offer_packet'
    ext = (ext or 'pdf').lower()

    contract_segment = None

    for split_result in split_pdf_by_segments(file_data, segments):
        seg = split_result.segment
        offer_type = _split_segment_to_offer_type(seg.document_type)
        # Avoid creating a redundant primary contract child when the parent itself is the
        # buyer offer (which would re-trigger primary terms handling).
        if offer_type == 'buyer_offer':
            if parent_offer_type == 'buyer_offer' or primary_assigned:
                if contract_segment is None:
                    contract_segment = seg
                continue
            primary_assigned = True

        # A form we cannot name is still a form in the packet. Dropping it
        # loses those pages entirely and looks like a clean split, so file it
        # on its own and let the package UI ask for a classification.
        is_unidentified = not offer_type
        if is_unidentified:
            offer_type = 'supporting'
            template_slug = UNIDENTIFIED_CHILD_SLUG
            display_name = seg.title or (
                f'Unidentified document (pages {seg.start_page}–{seg.end_page})'
            )
            name_hint = 'unidentified'
        else:
            doc_config = get_offer_document_type(offer_type)
            template_slug = doc_config['template_slug']
            display_name = doc_config['label']
            name_hint = offer_type

        child_filename = f"{name_root}_p{seg.start_page}-{seg.end_page}_{name_hint}.{ext}"

        child_path = None
        try:
            child_path = (
                upload_external_document(
                    transaction_id=transaction_id,
                    file_data=split_result.pdf_bytes,
                    original_filename=child_filename,
                    content_type='application/pdf',
                ) or {}
            ).get('path')
        except Exception:
            logger.exception(
                'Offer split child upload failed for doc %s pages %s-%s',
                doc.id, seg.start_page, seg.end_page,
            )
        if not child_path:
            # A row with no file reads as "uploaded" with nothing to open.
            if offer_type == 'buyer_offer':
                primary_assigned = False
            continue

        inherited_field_data = _inherited_field_data_for_segment(seg.document_type, doc.field_data)

        child_doc = TransactionDocument(
            organization_id=organization_id,
            transaction_id=transaction_id,
            template_slug=template_slug,
            template_name=display_name,
            status='signed',
            document_source='completed',
            signed_file_path=child_path,
            signed_file_size=len(split_result.pdf_bytes),
            signed_original_filename=child_filename,
            signed_at=datetime.utcnow(),
            extraction_status='complete' if inherited_field_data else None,
            field_data=inherited_field_data,
            parent_document_id=doc.id,
            page_start=seg.start_page,
            page_end=seg.end_page,
            split_source=split_source,
        )
        db.session.add(child_doc)
        db.session.flush()

        child_offer_document = SellerOfferDocument(
            organization_id=organization_id,
            transaction_id=transaction_id,
            offer_id=offer_document.offer_id,
            transaction_document_id=child_doc.id,
            offer_version_id=None,
            created_by_id=offer_document.created_by_id,
            document_type=offer_type,
            display_name=seg.title or display_name,
            is_primary_terms_document=False,
            extraction_summary=inherited_field_data,
        )
        db.session.add(child_offer_document)
        db.session.flush()

        if offer is not None:
            create_offer_activity(
                offer,
                'document_split',
                f'AI split {seg.title or display_name} from {offer_document.display_name}',
                version_id=offer_document.offer_version_id,
                document_id=child_doc.id,
                event_data={
                    'parent_document_id': doc.id,
                    'page_start': seg.start_page,
                    'page_end': seg.end_page,
                    'document_type': offer_type,
                    'segment_type': seg.document_type,
                },
            )
        created_children.append(child_offer_document)

    if created_children:
        _trim_offer_parent_to_contract(
            doc,
            file_data,
            contract_segment=contract_segment,
            total_pages=total_pages,
            name_root=name_root,
            ext=ext,
            split_source=split_source,
        )

    return created_children


def _trim_offer_parent_to_contract(
    doc,
    file_data,
    *,
    contract_segment,
    total_pages,
    name_root,
    ext,
    split_source,
):
    """Trim a contract-parent packet down to its own pages.

    When the parent row *is* the purchase contract, the contract segment is
    skipped rather than filed as a child. Leaving the full packet behind means
    the agent opens the whole stack under a contract label — the same trap the
    listing packet had.
    """
    if contract_segment is None:
        return
    pages = contract_segment.end_page - contract_segment.start_page + 1
    if pages >= total_pages:
        return

    from services.pdf_splitter import slice_pdf_pages
    from services.supabase_storage import upload_external_document

    trimmed = slice_pdf_pages(
        file_data, contract_segment.start_page, contract_segment.end_page,
    )
    if not trimmed:
        return

    filename = f'{name_root}_contract.{ext}'
    original_path = doc.signed_file_path
    original_source_path = doc.source_file_path
    trimmed_path = None
    try:
        trimmed_path = (
            upload_external_document(
                transaction_id=doc.transaction_id,
                file_data=trimmed,
                original_filename=filename,
                content_type='application/pdf',
            ) or {}
        ).get('path')
    except Exception:
        logger.exception(
            'Trimmed offer contract upload failed for doc %s; keeping full packet',
            doc.id,
        )
    if not trimmed_path:
        return

    doc.signed_file_path = trimmed_path
    # Viewers and the field editor read source_file_path first, so leaving it
    # on the packet would keep serving every page.
    if original_source_path in (None, original_path):
        doc.source_file_path = trimmed_path
    doc.signed_file_size = len(trimmed)
    doc.signed_original_filename = filename
    doc.page_start = contract_segment.start_page
    doc.page_end = contract_segment.end_page
    doc.split_source = split_source

    merged_field_data = dict(doc.field_data or {})
    merged_field_data['_offer_packet_split'] = {
        'original_page_count': total_pages,
        'original_signed_file_path': original_path,
        'original_source_file_path': original_source_path,
        'contract_pages': [
            contract_segment.start_page,
            contract_segment.end_page,
        ],
    }
    doc.field_data = merged_field_data
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(doc, 'field_data')


def split_contract_package_into_children(doc_id, file_data, *, split_source='ai_packet_split'):
    """Create split child documents under a contract packet parent."""
    if not file_data:
        return []

    doc = TransactionDocument.query.get(doc_id)
    if not doc or not doc.field_data:
        return []

    contract_document = SellerContractDocument.query.filter_by(transaction_document_id=doc.id).first()
    if not contract_document or not contract_document.is_primary_contract_document:
        return []

    parent_contract_type = contract_document.document_type
    if parent_contract_type not in ('offer_package', 'buyer_offer', 'final_acceptance'):
        return []

    detected = doc.field_data.get('detected_documents') if isinstance(doc.field_data, dict) else None
    if not isinstance(detected, list) or not detected:
        return []

    from services.pdf_splitter import (
        get_pdf_page_count,
        normalize_segments,
        split_pdf_by_segments,
    )

    total_pages = get_pdf_page_count(file_data)
    if total_pages <= 0:
        return []

    segments = normalize_segments(detected, total_pages=total_pages)
    if len(segments) < 2:
        return []

    existing_children = TransactionDocument.query.filter_by(parent_document_id=doc.id).count()
    if existing_children:
        return []

    from services.supabase_storage import upload_external_document

    organization_id = doc.organization_id
    transaction_id = doc.transaction_id
    primary_assigned = False
    created_children = []

    base_filename = doc.signed_original_filename or 'contract_packet.pdf'
    name_root, _, ext = base_filename.rpartition('.')
    name_root = name_root or 'contract_packet'
    ext = (ext or 'pdf').lower()

    for split_result in split_pdf_by_segments(file_data, segments):
        seg = split_result.segment
        document_type = _split_segment_to_offer_type(seg.document_type)
        if document_type == 'buyer_offer':
            document_type = 'final_acceptance'
        if not document_type:
            continue
        if document_type == 'final_acceptance':
            if primary_assigned:
                continue
            primary_assigned = True

        doc_config = get_offer_document_type(document_type)
        template_slug = doc_config['template_slug']
        display_name = doc_config['label']
        child_filename = f"{name_root}_p{seg.start_page}-{seg.end_page}_{document_type}.{ext}"

        try:
            upload_result = upload_external_document(
                transaction_id=transaction_id,
                file_data=split_result.pdf_bytes,
                original_filename=child_filename,
                content_type='application/pdf',
            )
        except Exception:
            upload_result = {'path': None}

        inherited_field_data = _inherited_field_data_for_segment(seg.document_type, doc.field_data)
        if document_type == 'final_acceptance':
            inherited_field_data = dict(doc.field_data or {})

        child_doc = TransactionDocument(
            organization_id=organization_id,
            transaction_id=transaction_id,
            template_slug=template_slug,
            template_name=display_name,
            status='signed',
            document_source='completed',
            signed_file_path=upload_result.get('path'),
            signed_file_size=len(split_result.pdf_bytes),
            signed_original_filename=child_filename,
            signed_at=datetime.utcnow(),
            extraction_status='complete' if inherited_field_data else None,
            field_data=inherited_field_data,
            parent_document_id=doc.id,
            page_start=seg.start_page,
            page_end=seg.end_page,
            split_source=split_source,
        )
        db.session.add(child_doc)
        db.session.flush()

        child_contract_document = SellerContractDocument(
            organization_id=organization_id,
            transaction_id=transaction_id,
            accepted_contract_id=contract_document.accepted_contract_id,
            transaction_document_id=child_doc.id,
            created_by_id=contract_document.created_by_id,
            document_type=document_type,
            display_name=seg.title or display_name,
            is_primary_contract_document=doc_config['primary_terms'],
            extraction_summary=inherited_field_data,
        )
        db.session.add(child_contract_document)
        db.session.flush()
        created_children.append(child_contract_document)

    return created_children


# Listing-package segment labels → (template_slug, display name). Slugs match
# the seller intake schema document_rules so split children satisfy the same
# required-document slots the questionnaire would otherwise create.
from services.listing_packet import LISTING_PACKET_FILING as LISTING_SPLIT_SEGMENT_MAP


# Unidentified split children are filed under a generic slug so the package UI
# renders them as "Needs classification" with a File document action.
UNIDENTIFIED_CHILD_SLUG = 'external'


def split_listing_package_into_children(
    doc_id,
    file_data,
    *,
    split_source='ai_packet_split',
    require_confident=True,
):
    """Create split child documents under a listing package parent.

    When a signed listing package PDF contains the listing agreement plus
    supporting paperwork (IABS, Seller's Disclosure, notices...), slice each
    supporting document into its own PDF and file it under its canonical
    template slug. If a placeholder already exists for that slug it is
    fulfilled in place instead of creating a duplicate row. The parent
    document remains the listing agreement of record, and its stored PDF is
    trimmed to listing-agreement pages only.
    """
    if not file_data:
        return []

    doc = TransactionDocument.query.get(doc_id)
    if not doc or not doc.transaction_id:
        return []

    field_data = doc.field_data if isinstance(doc.field_data, dict) else {}
    slug = (doc.template_slug or '').strip().lower().replace('_', '-')
    identity = field_data.get('_document_identity') or {}
    if slug != 'listing-agreement' and (identity.get('kind') or '') != 'listing_agreement':
        return []

    from services.listing_packet import (
        LISTING_AGREEMENT_TYPE,
        UNKNOWN_TYPE,
        build_listing_packet_plan,
        detected_documents_from_field_data,
        filing_for_type,
        packet_segments_as_detected_documents,
        packet_segments_as_split_segments,
    )
    from services.pdf_splitter import (
        get_pdf_page_count,
        slice_pdf_pages,
        split_pdf_by_segments,
    )

    total_pages = get_pdf_page_count(file_data)
    if total_pages <= 0:
        return []

    plan = build_listing_packet_plan(
        file_data,
        ai_segments=detected_documents_from_field_data(field_data),
    )
    packet_segments = plan.segments
    listing_segment = next(
        (seg for seg in packet_segments if seg.document_type == LISTING_AGREEMENT_TYPE),
        None,
    )
    child_segments = [
        seg for seg in packet_segments
        if seg.document_type != LISTING_AGREEMENT_TYPE
    ]
    listing_is_subset = bool(
        listing_segment
        and (listing_segment.end_page - listing_segment.start_page + 1) < total_pages
    )
    if not child_segments and not listing_is_subset:
        return []

    # Splitting on a guess is worse than splitting late: it files the wrong
    # pages under the listing of record and looks like success. When any page
    # is unaccounted for, hold off so the AI extraction pass can cover the
    # forms our fingerprint table does not know about.
    if require_confident and not plan.is_confident:
        logger.info(
            'Deferring listing package split for doc %s until AI classification: %s',
            doc.id, plan.coverage_summary(),
        )
        return []

    existing_children = TransactionDocument.query.filter_by(parent_document_id=doc.id).count()
    if existing_children:
        return []

    from services.supabase_storage import upload_external_document

    organization_id = doc.organization_id
    transaction_id = doc.transaction_id
    created_children = []
    used_slugs = set()

    base_filename = doc.signed_original_filename or 'listing_package.pdf'
    name_root, _, ext = base_filename.rpartition('.')
    name_root = name_root or 'listing_package'
    ext = (ext or 'pdf').lower()

    child_split_segments = packet_segments_as_split_segments(child_segments)
    for split_result in split_pdf_by_segments(file_data, child_split_segments):
        seg = split_result.segment
        seg_type = (seg.document_type or '').strip().lower()
        if seg_type in ('listing_agreement', 'buyer_offer', ''):
            continue
        child_slug, display_name = filing_for_type(seg_type)
        # No canonical slot for this form. File it on its own and let the
        # package UI ask for a human classification rather than guessing.
        is_unidentified = seg_type == UNKNOWN_TYPE or child_slug == 'supporting-document'
        if is_unidentified:
            child_slug = UNIDENTIFIED_CHILD_SLUG
            display_name = seg.title or (
                f'Unidentified document (pages {seg.start_page}–{seg.end_page})'
            )
        slug_key = (
            f'{child_slug}:{seg.start_page}-{seg.end_page}'
            if is_unidentified
            else child_slug
        )
        if slug_key in used_slugs:
            continue
        used_slugs.add(slug_key)

        name_hint = 'unidentified' if is_unidentified else child_slug
        child_filename = f"{name_root}_p{seg.start_page}-{seg.end_page}_{name_hint}.{ext}"

        child_path = None
        try:
            child_path = (
                upload_external_document(
                    transaction_id=transaction_id,
                    file_data=split_result.pdf_bytes,
                    original_filename=child_filename,
                    content_type='application/pdf',
                ) or {}
            ).get('path')
        except Exception:
            logger.exception(
                'Split child upload failed for doc %s pages %s-%s',
                doc.id, seg.start_page, seg.end_page,
            )
        if not child_path:
            # A row with no file is worse than no row: it would read as
            # "uploaded" with nothing to open. Leave the slot alone.
            used_slugs.discard(slug_key)
            continue

        placeholder = None
        if not is_unidentified:
            placeholder = (
                TransactionDocument.query.filter_by(
                    transaction_id=transaction_id,
                    organization_id=organization_id,
                    template_slug=child_slug,
                    is_placeholder=True,
                )
                .filter(TransactionDocument.signed_file_path.is_(None))
                .first()
            )

        if placeholder is not None:
            child_doc = placeholder
            child_doc.status = 'signed'
            child_doc.document_source = 'completed'
            child_doc.is_placeholder = False
        else:
            if not is_unidentified:
                existing_same_slug = (
                    TransactionDocument.query.filter_by(
                        transaction_id=transaction_id,
                        organization_id=organization_id,
                        template_slug=child_slug,
                    )
                    .filter(TransactionDocument.signed_file_path.isnot(None))
                    .count()
                )
                if existing_same_slug:
                    continue
            child_doc = TransactionDocument(
                organization_id=organization_id,
                transaction_id=transaction_id,
                template_slug=child_slug,
                template_name=display_name,
                status='signed',
                document_source='completed',
            )
            db.session.add(child_doc)

        child_doc.signed_file_path = child_path
        child_doc.signed_file_size = len(split_result.pdf_bytes)
        child_doc.signed_original_filename = child_filename
        child_doc.signed_at = datetime.utcnow()
        child_doc.parent_document_id = doc.id
        child_doc.page_start = seg.start_page
        child_doc.page_end = seg.end_page
        child_doc.split_source = split_source
        db.session.flush()
        created_children.append(child_doc)

    if listing_is_subset and listing_segment is not None:
        trimmed = slice_pdf_pages(
            file_data,
            listing_segment.start_page,
            listing_segment.end_page,
        )
        if trimmed:
            listing_filename = f"{name_root}_listing_agreement.{ext}"
            original_path = doc.signed_file_path
            original_source_path = doc.source_file_path
            trimmed_path = None
            try:
                trimmed_path = (
                    upload_external_document(
                        transaction_id=transaction_id,
                        file_data=trimmed,
                        original_filename=listing_filename,
                        content_type='application/pdf',
                    ) or {}
                ).get('path')
            except Exception:
                logger.exception(
                    'Trimmed listing upload failed for doc %s; keeping full packet',
                    doc.id,
                )
            # Never claim a trimmed listing while the stored PDF is still the
            # full packet — the agent would open 15 pages under an 11-page label.
            if trimmed_path:
                doc.signed_file_path = trimmed_path
                # Viewers, the field editor, and extraction all read
                # source_file_path first. Leaving it on the packet would keep
                # serving 15 pages under an 11-page listing.
                if original_source_path in (None, original_path):
                    doc.source_file_path = trimmed_path
                doc.signed_file_size = len(trimmed)
                doc.signed_original_filename = listing_filename
                doc.page_start = listing_segment.start_page
                doc.page_end = listing_segment.end_page
                doc.split_source = split_source
                merged_field_data = dict(field_data)
                merged_field_data['detected_documents'] = (
                    packet_segments_as_detected_documents(packet_segments)
                )
                merged_field_data['_listing_packet_split'] = {
                    'original_page_count': total_pages,
                    'original_signed_file_path': original_path,
                    'original_source_file_path': original_source_path,
                    'listing_pages': [
                        listing_segment.start_page,
                        listing_segment.end_page,
                    ],
                    'coverage': plan.coverage_summary(),
                }
                doc.field_data = merged_field_data
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(doc, 'field_data')

    if created_children:
        try:
            from services import audit_service
            audit_service.log_event(
                event_type='document_package_split',
                transaction_id=transaction_id,
                document_id=doc.id,
                description=(
                    f'Listing package split into {len(created_children)} '
                    f'supporting document(s)'
                ),
                event_data={
                    'parent_document_id': doc.id,
                    'children': [
                        {
                            'document_id': child.id,
                            'template_slug': child.template_slug,
                            'page_start': child.page_start,
                            'page_end': child.page_end,
                        }
                        for child in created_children
                    ],
                },
                source='system',
            )
        except Exception:
            logger.exception('Failed to audit listing package split for doc %s', doc.id)

    return created_children


def is_listing_package_candidate(doc) -> bool:
    """True when this document could be a mixed listing packet."""
    if doc is None:
        return False
    slug = (doc.template_slug or '').strip().lower().replace('_', '-')
    if slug == 'listing-agreement':
        return True
    field_data = doc.field_data if isinstance(doc.field_data, dict) else {}
    identity = field_data.get('_document_identity') or {}
    return str(identity.get('kind') or '').strip().lower() == 'listing_agreement'


def ensure_listing_package_split(
    doc,
    file_bytes=None,
    *,
    split_source='ai_packet_split',
    require_confident=True,
):
    """Split a listing packet into child PDFs using the file itself.

    Page fingerprints read the PDF, so this does not need AI extraction to have
    run. Callers should invoke it as soon as the PDF is on the document —
    waiting for extraction means packets that skip extraction (bootstrap apply
    writes field_data and marks the document complete) never get split.

    ``require_confident`` holds the split back until every page is positively
    identified. Pass it as True before AI has seen the file and False once AI
    has had its say, so the last pass always files something.

    Returns the created child documents. Safe to call more than once: the
    splitter no-ops once children exist.
    """
    if not is_listing_package_candidate(doc):
        return []

    if not file_bytes:
        path = getattr(doc, 'signed_file_path', None) or getattr(doc, 'source_file_path', None)
        if not path:
            return []
        try:
            from services.supabase_storage import download_document
            file_bytes = download_document(path)
        except Exception:
            logger.exception(
                'Could not download listing PDF for split doc=%s path=%s', doc.id, path,
            )
            return []
    if not file_bytes:
        return []

    return split_listing_package_into_children(
        doc.id,
        file_bytes,
        split_source=split_source,
        require_confident=require_confident,
    )


def sync_offer_version_from_document(doc_id):
    """Sync AI-extracted TransactionDocument.field_data into linked offer records."""
    doc = TransactionDocument.query.get(doc_id)
    if not doc or not doc.field_data:
        return None

    version = SellerOfferVersion.query.filter_by(transaction_document_id=doc.id).first()
    offer_document = SellerOfferDocument.query.filter_by(transaction_document_id=doc.id).first()
    if not version:
        return merge_offer_supporting_document(offer_document)

    extracted = dict(doc.field_data or {})
    offer = SellerOffer.query.filter_by(
        id=version.offer_id,
        organization_id=doc.organization_id,
    ).first()
    list_price = None
    if offer and offer.transaction_id:
        from models import Transaction
        tx = Transaction.query.get(offer.transaction_id)
        extra = (tx.extra_data or {}) if tx else {}
        list_price = extra.get('list_price')

    from services.scoped_document_intake import terms_from_document_field_data
    normalized_core = terms_from_document_field_data(extracted, list_price=list_price)
    price_decimal = normalized_core.pop('_offer_price_decimal', None)

    terms = dict(version.terms_data or {})
    terms.update(extracted)
    terms.update(normalized_core)
    if list_price not in (None, ''):
        terms['list_price'] = list_price
    terms = _merge_existing_offer_context(offer, terms)
    version.terms_data = terms
    version.status = 'reviewed'
    version.extraction_reviewed_at = datetime.utcnow()

    if offer:
        apply_offer_terms(offer, terms)
        if price_decimal is not None and (
            offer.offer_price is None
            or offer.offer_price <= 0
            or (
                price_decimal > 0
                and offer.offer_price
                and offer.offer_price < price_decimal / Decimal('10')
            )
        ):
            offer.offer_price = price_decimal
        offer.current_version_id = version.id
        buyers = _coerce_text(
            normalized_core.get('buyer_names')
            or extracted.get('buyer_names')
            or extracted.get('buyer_name')
        )
        if buyers:
            offer.buyer_names = buyers
        if extracted.get('buyer_agent_name') and not offer.buyer_agent_name:
            offer.buyer_agent_name = _coerce_text(extracted.get('buyer_agent_name'))
        if extracted.get('buyer_agent_brokerage') and not offer.buyer_agent_brokerage:
            offer.buyer_agent_brokerage = _coerce_text(extracted.get('buyer_agent_brokerage'))
        if offer.status in ('draft', 'new', 'needs_review'):
            offer.status = 'needs_review'
        # Ensure money/buyer columns survive autoflush of related rows.
        db.session.add(offer)
        db.session.flush()
        create_offer_activity(
            offer,
            'extraction_completed',
            'AI extracted offer terms',
            version_id=version.id,
            document_id=doc.id,
            event_data={'field_count': len(extracted)},
        )

    if offer_document:
        offer_document.extraction_summary = extracted

    return version


def sync_offer_thread_from_extraction(doc_id):
    """Retag + sync a linked offer document after extraction.

    Safe without global EXTRACTION_AUTO_APPLY — only mutates offer-thread
    columns / versions, never controlling-contract baselines.
    """
    from services.document_identity import DocumentIdentity, KIND_PURCHASE_CONTRACT
    from sqlalchemy.orm.attributes import flag_modified

    doc = TransactionDocument.query.get(doc_id)
    if not doc:
        return None

    offer_document = SellerOfferDocument.query.filter_by(
        transaction_document_id=doc.id,
        organization_id=doc.organization_id,
    ).first()
    if not offer_document:
        return None

    offer = SellerOffer.query.filter_by(
        id=offer_document.offer_id,
        organization_id=doc.organization_id,
    ).first()
    if not offer:
        return None

    identity = DocumentIdentity.from_dict(
        (doc.field_data or {}).get('_document_identity')
        if isinstance(doc.field_data, dict)
        else None
    )
    classification = ''
    if isinstance(doc.field_data, dict):
        classification = str(
            doc.field_data.get('document_classification')
            or doc.field_data.get('document_type')
            or ''
        ).strip().lower()

    slug = (identity.template_slug or doc.template_slug or '').strip().lower()
    if identity.is_high_confidence and identity.template_slug:
        slug = identity.template_slug.strip().lower()
        doc.template_slug = slug
        if identity.label:
            doc.template_name = str(identity.label)[:200]
        flag_modified(doc, 'field_data')
    elif classification == 'commission_document' and slug in ('completed', 'external', 'custom', ''):
        slug = 'broker-compensation-agreement'
        doc.template_slug = slug
        doc.template_name = doc.template_name or 'Broker Compensation Agreement'

    is_primary = (
        identity.kind == KIND_PURCHASE_CONTRACT
        or bool(OFFER_DOCUMENT_TYPES.get(
            _SLUG_TO_OFFER_DOCUMENT_TYPE.get(slug, ''), {}
        ).get('primary_terms'))
        or slug in {
            'seller-offer-contract',
            'one-to-four-family-contract',
            'purchase-contract',
            'condominium-contract',
            'new-home-completed-construction-contract',
            'new-home-incomplete-construction-contract',
            'farm-and-ranch-contract',
            'unimproved-property-contract',
        }
    )
    doc_type = _SLUG_TO_OFFER_DOCUMENT_TYPE.get(slug)
    if is_primary:
        doc_type = 'buyer_offer'
    elif not doc_type:
        doc_type = slug or 'supporting'

    offer_document.document_type = doc_type
    offer_document.display_name = (
        (identity.label if identity and identity.label else None)
        or doc.template_name
        or get_offer_document_type(doc_type).get('label')
        or 'Offer Document'
    )
    offer_document.is_primary_terms_document = bool(is_primary)

    if is_primary:
        version = SellerOfferVersion.query.filter_by(
            organization_id=offer.organization_id,
            transaction_id=offer.transaction_id,
            transaction_document_id=doc.id,
        ).first()
        if not version:
            doc_config = get_offer_document_type('buyer_offer')
            version = SellerOfferVersion(
                organization_id=offer.organization_id,
                transaction_id=offer.transaction_id,
                offer_id=offer.id,
                created_by_id=offer.created_by_id,
                transaction_document_id=doc.id,
                version_number=1,
                direction=doc_config.get('direction') or 'buyer_offer',
                status='submitted',
                submitted_at=datetime.utcnow(),
                terms_data={},
            )
            db.session.add(version)
            db.session.flush()
            offer.current_version_id = version.id
            offer_document.offer_version_id = version.id
        return sync_offer_version_from_document(doc.id)

    return merge_offer_supporting_document(offer_document)


def apply_contract_terms(contract, terms):
    """Copy reviewed/extracted terms into canonical accepted contract columns."""
    terms = normalize_offer_terms(terms)
    addenda = _json_object(terms.get('addenda'))
    supporting = _json_object(terms.get('supporting_documents'))
    financing_addendum = (
        _json_object(addenda.get('third_party_financing_addendum'))
        or _json_object(supporting.get('third_party_financing'))
    )
    seller_disclosure = _json_object(supporting.get('sellers_disclosure'))
    hoa_addendum = _json_object(addenda.get('hoa_addendum')) or _json_object(supporting.get('hoa_addendum'))

    contract.accepted_price = _coerce_decimal(terms.get('offer_price') or terms.get('sales_price')) or contract.accepted_price
    contract.effective_date = _parse_date(terms.get('effective_date')) or contract.effective_date
    contract.effective_at = _parse_datetime(terms.get('effective_at')) or contract.effective_at
    contract.closing_date = _parse_date(terms.get('proposed_close_date') or terms.get('closing_date')) or contract.closing_date
    contract.option_period_days = _coerce_int(terms.get('option_period_days')) if terms.get('option_period_days') is not None else contract.option_period_days
    contract.financing_type = _coerce_text(terms.get('financing_type')) or contract.financing_type
    contract.cash_down_payment = _coerce_decimal(terms.get('cash_down_payment')) or contract.cash_down_payment
    contract.financing_amount = _coerce_decimal(
        terms.get('financing_amount')
        or terms.get('total_financing_amount')
        or financing_addendum.get('total_financing_amount')
    ) or contract.financing_amount
    contract.seller_concessions_amount = _coerce_decimal(terms.get('seller_concessions_amount')) or contract.seller_concessions_amount
    contract.title_company = _coerce_text(terms.get('title_company')) or contract.title_company
    contract.escrow_officer = _coerce_text(terms.get('escrow_officer')) or contract.escrow_officer
    contract.survey_choice = _coerce_text(terms.get('survey_choice')) or contract.survey_choice
    contract.survey_furnished_by = _coerce_text(terms.get('survey_furnished_by')) or contract.survey_furnished_by
    contract.residential_service_contract = _coerce_text(terms.get('residential_service_contract')) or contract.residential_service_contract
    contract.buyer_agent_commission_percent = _coerce_decimal(terms.get('buyer_agent_commission_percent')) or contract.buyer_agent_commission_percent
    contract.buyer_agent_commission_flat = _coerce_decimal(terms.get('buyer_agent_commission_flat')) or contract.buyer_agent_commission_flat

    if terms.get('hoa_applicable') is not None:
        contract.hoa_applicable = _coerce_bool(terms.get('hoa_applicable'))
    elif hoa_addendum:
        contract.hoa_applicable = True

    if terms.get('seller_disclosure_required') is not None:
        contract.seller_disclosure_required = _coerce_bool(terms.get('seller_disclosure_required'))
    elif seller_disclosure:
        contract.seller_disclosure_required = True

    seller_disclosure_delivered = (
        seller_disclosure.get('buyer_received_date')
        or seller_disclosure.get('seller_signed_date')
    )
    contract.seller_disclosure_delivered_at = (
        _parse_datetime(seller_disclosure_delivered)
        or contract.seller_disclosure_delivered_at
    )

    if terms.get('lead_based_paint_required') is not None:
        contract.lead_based_paint_required = _coerce_bool(terms.get('lead_based_paint_required'))
    elif seller_disclosure.get('built_before_1978') is not None:
        contract.lead_based_paint_required = _coerce_bool(seller_disclosure.get('built_before_1978'))

    financing_deadline = derive_financing_approval_deadline(terms, contract.effective_date)
    contract.financing_approval_deadline = financing_deadline or contract.financing_approval_deadline
    contract.frozen_terms = terms
    contract.addenda_data = terms.get('addenda') or {}

    extra_data = dict(contract.extra_data or {})
    if terms.get('supporting_documents'):
        extra_data['supporting_documents'] = terms.get('supporting_documents')
    contract.extra_data = extra_data
    return contract


def _merge_existing_contract_context(contract, terms):
    merged = dict(terms or {})
    existing = dict(contract.frozen_terms or {}) if contract else {}
    if existing.get('supporting_documents') and 'supporting_documents' not in merged:
        merged['supporting_documents'] = existing['supporting_documents']
    if existing.get('addenda') or merged.get('addenda'):
        addenda = dict(existing.get('addenda') or {})
        addenda.update(merged.get('addenda') or {})
        merged['addenda'] = addenda
    return normalize_offer_terms(merged)


def sync_contract_from_document(doc_id):
    """Sync AI-extracted TransactionDocument.field_data into a linked accepted contract."""
    from sqlalchemy.orm.attributes import flag_modified

    doc = TransactionDocument.query.get(doc_id)
    if not doc or not doc.field_data:
        return None

    contract_document = SellerContractDocument.query.filter_by(transaction_document_id=doc.id).first()
    if not contract_document:
        return None

    contract = contract_document.accepted_contract
    if not contract:
        return None

    extracted = dict(doc.field_data or {})
    terms = dict(contract.frozen_terms or {})
    if contract_document.is_primary_contract_document:
        terms.update(extracted)
    else:
        normalized = _normalized_supporting_payload(contract_document.document_type, extracted)
        supporting = dict(terms.get('supporting_documents') or {})
        supporting.update(normalized.get('supporting_documents') or {})
        if supporting:
            terms['supporting_documents'] = supporting

        if normalized.get('addenda'):
            addenda = dict(terms.get('addenda') or {})
            addenda.update(normalized['addenda'])
            terms['addenda'] = addenda

        for key, value in (normalized.get('offer_terms') or {}).items():
            if value is not None:
                terms[key] = value

    terms = _merge_existing_contract_context(contract, terms)
    apply_contract_terms(contract, terms)
    create_contract_milestones(contract, replace=True)
    contract_document.extraction_summary = extracted

    flag_modified(contract, 'frozen_terms')
    flag_modified(contract, 'addenda_data')
    flag_modified(contract, 'extra_data')
    flag_modified(contract_document, 'extraction_summary')
    return contract_document


def _milestone(contract, key, title, due_at=None, source='calculated', responsible_party=None, source_data=None):
    return SellerContractMilestone(
        organization_id=contract.organization_id,
        transaction_id=contract.transaction_id,
        accepted_contract_id=contract.id,
        milestone_key=key,
        title=title,
        due_at=due_at,
        status='not_started' if due_at else 'waiting',
        responsible_party=responsible_party,
        source=source,
        source_data=source_data or {},
    )


def _json_object(value):
    return value if isinstance(value, dict) else {}


def normalize_offer_terms(terms):
    """Promote combined package/addendum extraction into canonical offer fields."""
    normalized = dict(terms or {})
    addenda = _json_object(normalized.get('addenda'))
    supporting = _json_object(normalized.get('supporting_documents'))

    financing_addendum = (
        _json_object(addenda.get('third_party_financing_addendum'))
        or _json_object(supporting.get('third_party_financing'))
    )
    if financing_addendum:
        normalized.setdefault('financing_type', financing_addendum.get('financing_type'))
        normalized.setdefault('financing_contingency', financing_addendum.get('buyer_approval_required'))
        financing_amount = (
            financing_addendum.get('total_financing_amount')
            or _sum_financing_amount(financing_addendum)
        )
        normalized.setdefault('financing_amount', financing_amount)
        addenda['third_party_financing_addendum'] = financing_addendum
        supporting.setdefault('third_party_financing', financing_addendum)

    hoa_addendum = _json_object(addenda.get('hoa_addendum')) or _json_object(supporting.get('hoa_addendum'))
    if hoa_addendum:
        normalized.setdefault('hoa_applicable', True)
        normalized.setdefault('hoa_resale_certificate_payer', hoa_addendum.get('title_company_info_payer'))
        addenda['hoa_addendum'] = hoa_addendum
        supporting.setdefault('hoa_addendum', hoa_addendum)

    compensation = _json_object(supporting.get('broker_compensation'))
    if compensation:
        for key in (
            'buyer_agent_commission_percent',
            'buyer_agent_commission_flat',
        ):
            if normalized.get(key) in (None, '') and compensation.get(key) not in (None, ''):
                normalized[key] = compensation.get(key)
        supporting['broker_compensation'] = compensation

    if addenda:
        normalized['addenda'] = addenda
    if supporting:
        normalized['supporting_documents'] = supporting
    return normalized


def build_contract_milestones(contract):
    """Build Texas seller contract milestones from accepted terms and addenda data."""
    addenda = _json_object(contract.addenda_data)
    effective_dt = contract.effective_at or _as_datetime(contract.effective_date)
    closing_dt = _as_datetime(contract.closing_date)
    milestones = []

    if effective_dt and contract.option_period_days:
        milestones.append(_milestone(
            contract,
            'option_period_expires',
            'Option period expires',
            effective_dt + timedelta(days=contract.option_period_days),
        ))

    if effective_dt:
        milestones.append(_milestone(
            contract,
            'earnest_money_due',
            'Earnest money due to title company',
            effective_dt + timedelta(days=3),
            source_data={'basis': 'effective_date_plus_3_days'},
        ))

    financing_addendum = _json_object(addenda.get('third_party_financing_addendum'))
    financing_due = _as_datetime(
        _parse_date(financing_addendum.get('buyer_approval_deadline'))
        or contract.financing_approval_deadline
    )
    if not financing_due:
        approval_days = _coerce_int(financing_addendum.get('buyer_approval_days'))
        if effective_dt and approval_days is not None:
            financing_due = effective_dt + timedelta(days=approval_days)
    milestones.append(_milestone(
        contract,
        'financing_approval_due',
        'Financing approval deadline',
        financing_due,
        source='ai_extracted' if financing_due else 'calculated',
        source_data=financing_addendum,
    ))

    sale_contingency = _json_object(addenda.get('sale_of_other_property_addendum'))
    sale_deadline = _as_datetime(_parse_date(sale_contingency.get('waiver_deadline')))
    if sale_deadline:
        milestones.append(_milestone(
            contract,
            'sale_of_other_property_deadline',
            'Sale of other property contingency deadline',
            sale_deadline,
            source='ai_extracted',
            source_data=sale_contingency,
        ))

    title_data = _json_object(addenda.get('title'))
    title_commitment_due = _as_datetime(_parse_date(title_data.get('title_commitment_due')))
    milestones.append(_milestone(
        contract,
        'title_commitment_due',
        'Title commitment delivery',
        title_commitment_due,
        source='ai_extracted' if title_commitment_due else 'calculated',
        source_data=title_data,
    ))

    objection_due = _as_datetime(_parse_date(title_data.get('title_objection_deadline')))
    if not objection_due and title_commitment_due and title_data.get('title_objection_days'):
        objection_due = title_commitment_due + timedelta(days=int(title_data['title_objection_days']))
    milestones.append(_milestone(
        contract,
        'title_objection_deadline',
        'Buyer title objection deadline',
        objection_due,
        source='ai_extracted' if objection_due else 'calculated',
        source_data=title_data,
    ))

    milestones.extend([
        _milestone(contract, 'survey_due', 'Survey or existing survey/T-47 due'),
        _milestone(contract, 'hoa_resale_certificate_due', 'HOA resale certificate due' if contract.hoa_applicable else 'HOA resale certificate not applicable'),
        _milestone(contract, 'seller_disclosure_due', "Seller's Disclosure Notice delivery"),
    ])

    if contract.lead_based_paint_required:
        milestones.append(_milestone(contract, 'lead_paint_due', 'Lead-based paint disclosure delivery'))

    if closing_dt:
        milestones.extend([
            _milestone(contract, 'closing_date', 'Contract closing date', closing_dt),
            _milestone(contract, 'funding_recording', 'Funding and recording confirmation', closing_dt),
            _milestone(contract, 'final_walkthrough', 'Final walkthrough', closing_dt - timedelta(days=2)),
            _milestone(contract, 'key_access_handoff', 'Key and access handoff', closing_dt),
        ])

    return milestones


def create_contract_milestones(contract, replace=False):
    """Persist calculated milestones for an accepted contract."""
    if replace:
        for existing in contract.milestones.all():
            if existing.milestone_key != 'manual' and existing.source != 'manual':
                db.session.delete(existing)

    milestones = build_contract_milestones(contract)
    for item in milestones:
        db.session.add(item)
    return milestones


def seed_ctc_requirements_from_accepted_contract(
    *,
    transaction,
    accepted_contract,
    actor_id=None,
    pack_key='seller_ctc',
):
    """Seed CTC deadline-pack requirements from accepted/effective/closing anchors.

    Idempotent: DeadlineRulesService skips existing requirement keys.
    Legacy SellerContractMilestone rows remain for compatibility.
    """
    from services.deadline_rules import DeadlineRulesService

    anchors = {}
    if getattr(accepted_contract, 'effective_date', None):
        anchors['effective_date'] = accepted_contract.effective_date
    if getattr(accepted_contract, 'closing_date', None):
        anchors['closing_date'] = accepted_contract.closing_date
        anchors['expected_close_date'] = accepted_contract.closing_date

    option_days = getattr(accepted_contract, 'option_period_days', None)
    if option_days is not None and anchors.get('effective_date'):
        try:
            anchors['option_period_end'] = (
                anchors['effective_date'] + timedelta(days=int(option_days))
            )
        except (TypeError, ValueError):
            pass

    if not anchors:
        return {'created': 0, 'skipped': 0, 'reason': 'no_anchors'}

    side = 'seller'
    if pack_key.startswith('buyer'):
        side = 'buyer'

    return DeadlineRulesService.apply_pack_to_transaction(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
        pack_key=pack_key,
        anchors=anchors,
        side=side,
        source='offer_acceptance',
        actor_id=actor_id,
    )


def seed_buyer_ctc_from_terms(
    *,
    transaction,
    terms: dict,
    actor_id=None,
):
    """Seed buyer_ctc requirements from approved/controlling contract terms."""
    from datetime import date as date_cls

    from services.deadline_rules import DeadlineRulesService

    def _as_date(value):
        if value is None or value == '':
            return None
        if isinstance(value, date_cls) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        text = str(value).strip()[:10]
        try:
            return datetime.strptime(text, '%Y-%m-%d').date()
        except ValueError:
            return None

    anchors = {}
    effective = _as_date(terms.get('effective_date'))
    closing = _as_date(
        terms.get('closing_date')
        or terms.get('proposed_close_date')
        or terms.get('close_date')
    )
    if effective:
        anchors['effective_date'] = effective
    if closing:
        anchors['closing_date'] = closing
    option_days = terms.get('option_period_days')
    if option_days is not None and effective:
        try:
            anchors['option_period_end'] = effective + timedelta(days=int(option_days))
        except (TypeError, ValueError):
            pass
    if not anchors:
        return {'created': 0, 'skipped': 0, 'reason': 'no_anchors'}
    return DeadlineRulesService.apply_pack_to_transaction(
        transaction_id=transaction.id,
        organization_id=transaction.organization_id,
        pack_key='buyer_ctc',
        anchors=anchors,
        side='buyer',
        source='buyer_controlling_contract',
        actor_id=actor_id,
    )


def promote_backup_contract(primary_contract, backup_contract, notice_received_at, actor_id=None):
    """Terminate primary workflow position and promote an accepted backup contract."""
    primary_contract.status = 'terminated'
    backup_contract.position = 'primary'
    backup_contract.backup_notice_received_at = notice_received_at
    backup_contract.backup_promoted_at = datetime.utcnow()
    backup_contract.status = 'active'

    if backup_contract.offer:
        backup_contract.offer.status = 'accepted_primary'
        backup_contract.offer.backup_promoted_at = backup_contract.backup_promoted_at
        create_offer_activity(
            backup_contract.offer,
            'backup_promoted',
            'Backup contract promoted to primary',
            actor_id=actor_id,
            event_data={'notice_received_at': notice_received_at.isoformat() if notice_received_at else None},
        )

    create_contract_milestones(backup_contract, replace=True)
    return backup_contract


def terminate_contract(contract, reason, actor_id, terminated_at=None, document_id=None, notes=None):
    """Mark an accepted contract terminated and create its termination record."""
    terminated_at = terminated_at or datetime.utcnow()
    termination = SellerContractTermination(
        organization_id=contract.organization_id,
        transaction_id=contract.transaction_id,
        accepted_contract_id=contract.id,
        created_by_id=actor_id,
        termination_document_id=document_id,
        termination_reason=reason,
        terminated_at=terminated_at,
        notes=notes,
    )
    contract.status = 'terminated'
    db.session.add(termination)
    return termination


def close_contract(contract, actor_id, **closeout):
    """Create/update closeout data and mark the accepted contract closed."""
    closing = contract.closing_summary or SellerClosingSummary(
        organization_id=contract.organization_id,
        transaction_id=contract.transaction_id,
        accepted_contract_id=contract.id,
        created_by_id=actor_id,
    )

    for key, value in closeout.items():
        if hasattr(closing, key):
            setattr(closing, key, value)

    contract.status = 'closed'
    contract.transaction.status = 'closed'
    if closing.actual_closing_date:
        contract.transaction.actual_close_date = closing.actual_closing_date

    for milestone in contract.milestones.all():
        if milestone.status not in ('completed', 'not_applicable'):
            milestone.status = 'completed'
            milestone.completed_at = datetime.utcnow()

    db.session.add(closing)
    return closing
