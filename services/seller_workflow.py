"""Seller transaction workflow helpers.

These functions keep deadline math and lifecycle transitions out of route
handlers. They intentionally do not commit; callers should commit after the
surrounding action and audit records are complete.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import re

from models import (
    SellerAcceptedContract,
    SellerClosingSummary,
    SellerContractMilestone,
    SellerContractTermination,
    SellerOffer,
    SellerOfferActivity,
    SellerOfferDocument,
    SellerOfferVersion,
    TransactionDocument,
    db,
)


ACTIVE_OFFER_STATUSES = {
    'new',
    'reviewing',
    'needs_review',
    'countered',
}


OFFER_DOCUMENT_TYPES = {
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


def _parse_date(value):
    if not value:
        return None
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


def apply_offer_terms(offer, terms):
    """Copy reviewed/extracted terms into canonical offer comparison columns."""
    terms = terms or {}
    offer.offer_price = _coerce_decimal(terms.get('offer_price') or terms.get('sales_price'))
    offer.financing_type = terms.get('financing_type')
    offer.cash_down_payment = _coerce_decimal(terms.get('cash_down_payment'))
    offer.earnest_money = _coerce_decimal(terms.get('earnest_money'))
    offer.additional_earnest_money = _coerce_decimal(terms.get('additional_earnest_money'))
    offer.option_fee = _coerce_decimal(terms.get('option_fee'))
    offer.option_period_days = _coerce_int(terms.get('option_period_days'))
    offer.seller_concessions_amount = _coerce_decimal(terms.get('seller_concessions_amount'))
    offer.proposed_close_date = _parse_date(terms.get('proposed_close_date') or terms.get('closing_date'))
    offer.possession_type = terms.get('possession_type')
    offer.leaseback_days = _coerce_int(terms.get('leaseback_days'))
    offer.appraisal_contingency = _coerce_bool(terms.get('appraisal_contingency'))
    offer.financing_contingency = _coerce_bool(terms.get('financing_contingency'))
    offer.sale_of_other_property_contingency = _coerce_bool(terms.get('sale_of_other_property_contingency'))
    offer.inspection_or_repair_terms_summary = terms.get('inspection_or_repair_terms_summary')
    offer.title_policy_payer = terms.get('title_policy_payer')
    offer.survey_payer = terms.get('survey_payer')
    offer.hoa_resale_certificate_payer = terms.get('hoa_resale_certificate_payer')
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
    return merged


def _normalized_supporting_payload(document_type, extracted):
    """Map supporting document extraction into offer terms namespaces."""
    extracted = dict(extracted or {})
    if document_type == 'third_party_financing':
        return {
            'offer_terms': {
                'financing_type': extracted.get('financing_type'),
                'financing_contingency': extracted.get('buyer_approval_required'),
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
    offer = version.offer
    terms = dict(version.terms_data or {})
    terms.update(extracted)
    terms = _merge_existing_offer_context(offer, terms)
    version.terms_data = terms
    version.status = 'reviewed'
    version.extraction_reviewed_at = datetime.utcnow()

    if offer:
        apply_offer_terms(offer, terms)
        offer.current_version_id = version.id
        if extracted.get('buyer_names') and not offer.buyer_names:
            offer.buyer_names = extracted.get('buyer_names')
        if extracted.get('buyer_agent_name') and not offer.buyer_agent_name:
            offer.buyer_agent_name = extracted.get('buyer_agent_name')
        if extracted.get('buyer_agent_brokerage') and not offer.buyer_agent_brokerage:
            offer.buyer_agent_brokerage = extracted.get('buyer_agent_brokerage')
        offer.status = 'reviewing' if offer.status in ('draft', 'new') else offer.status
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
            db.session.delete(existing)

    milestones = build_contract_milestones(contract)
    for item in milestones:
        db.session.add(item)
    return milestones


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
