"""
Document privacy controls (BOB VTC Phase 3).

Sensitivity classes, retention helpers, and channel/LLM gates for
lease/tenant and other sensitive transaction documents.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Canonical sensitivity classes (plan E3-1).
PUBLIC_OK = 'public_ok'
INTERNAL = 'internal'
CONFIDENTIAL = 'confidential'
RESTRICTED_TENANT = 'restricted_tenant'

SENSITIVITY_CLASSES = frozenset({
    PUBLIC_OK,
    INTERNAL,
    CONFIDENTIAL,
    RESTRICTED_TENANT,
})

# Telegram is forbidden for confidential + restricted_tenant.
TELEGRAM_BANNED_CLASSES = frozenset({CONFIDENTIAL, RESTRICTED_TENANT})

# LLM requires an explicit allowlist for restricted_tenant.
LLM_BLOCKED_BY_DEFAULT = frozenset({RESTRICTED_TENANT})

# Document-type / slug hints → sensitivity.
_RESTRICTED_TENANT_HINTS = (
    'paystub', 'pay_stub', 'pay-stub', 'w2', 'w-2', 'tax_return',
    'driver_license', 'drivers_license', 'driver-license', 'government_id',
    'passport', 'ssn', 'social_security', 'bank_statement', 'income_verification',
    'tenant_application', 'rental_application', 'lease_application',
    'credit_report', 'background_check', 'photo_id', 'id_card',
)
_CONFIDENTIAL_HINTS = (
    'lease', 'tenant', 'landlord', 'rental_agreement', 'lease_agreement',
    'applicant', 'screening',
)
_INTERNAL_HINTS = (
    'cda', 'commission', 'internal_notes', 'net_sheet',
)

# Default retention windows (days) by sensitivity class.
RETENTION_DAYS = {
    PUBLIC_OK: 365 * 7,
    INTERNAL: 365 * 7,
    CONFIDENTIAL: 365 * 3,
    RESTRICTED_TENANT: 365,  # Prefer shorter retention for IDs/paystubs
}

# Org feature flag that must be on before lease/tenant bootstrap create.
PRIVACY_CONTROLS_FLAG = 'BOB_VTC_PRIVACY_CONTROLS'


def normalize_sensitivity_class(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'public': PUBLIC_OK,
        'publicok': PUBLIC_OK,
        'restricted': RESTRICTED_TENANT,
        'tenant_restricted': RESTRICTED_TENANT,
        'sensitive': RESTRICTED_TENANT,
    }
    text = aliases.get(text, text)
    return text if text in SENSITIVITY_CLASSES else None


def infer_sensitivity_class(
    *,
    document_type: Optional[str] = None,
    template_slug: Optional[str] = None,
    template_name: Optional[str] = None,
    transaction_side: Optional[str] = None,
) -> str:
    """Infer sensitivity from document labels / transaction side."""
    blob = ' '.join(
        str(x or '').lower().replace('-', '_').replace(' ', '_')
        for x in (document_type, template_slug, template_name)
    )
    side = (transaction_side or '').strip().lower()

    if any(h in blob for h in _RESTRICTED_TENANT_HINTS):
        return RESTRICTED_TENANT
    # Token-ish checks for lease/tenant packets (avoid matching "id" inside
    # "residential").
    sensitive_tokens = (
        'application', 'paystub', 'pay_stub', 'income', 'stub',
        'gov_id', 'photo_id', 'driver', 'passport',
    )
    if side in ('tenant', 'landlord') and any(h in blob for h in sensitive_tokens):
        return RESTRICTED_TENANT
    if any(h in blob for h in _CONFIDENTIAL_HINTS) or side in ('tenant', 'landlord'):
        return CONFIDENTIAL
    if any(h in blob for h in _INTERNAL_HINTS):
        return INTERNAL
    return PUBLIC_OK


def retention_until_for(
    sensitivity_class: Optional[str],
    *,
    from_dt: Optional[datetime] = None,
) -> datetime:
    """Return retention cutoff datetime for a sensitivity class."""
    cls = normalize_sensitivity_class(sensitivity_class) or INTERNAL
    days = RETENTION_DAYS.get(cls, RETENTION_DAYS[INTERNAL])
    base = from_dt or datetime.utcnow()
    return base + timedelta(days=days)


def is_past_retention(document) -> bool:
    """True when retention_until is set and has passed."""
    until = getattr(document, 'retention_until', None)
    if until is None:
        return False
    return datetime.utcnow() >= until


def document_sensitivity(document) -> str:
    """Resolve effective sensitivity for a document-like object."""
    existing = normalize_sensitivity_class(
        getattr(document, 'sensitivity_class', None)
    )
    if existing:
        return existing
    return infer_sensitivity_class(
        document_type=getattr(document, 'document_type', None),
        template_slug=getattr(document, 'template_slug', None),
        template_name=getattr(document, 'template_name', None),
    )


def may_send_to_telegram(document) -> bool:
    """
    Ban confidential and restricted_tenant from Telegram.

    Public_ok and internal may be notified (text summaries only — never
    attach raw ID/paystub bytes on Telegram).
    """
    if document is None:
        return False
    cls = document_sensitivity(document)
    if cls in TELEGRAM_BANNED_CLASSES:
        return False
    return True


def may_use_in_llm(
    document,
    *,
    allowlist: Optional[set[str] | frozenset[str]] = None,
) -> bool:
    """
    Gate LLM processing.

    - confidential: blocked unless ai_processing_allowed is explicitly True
      AND not restricted_tenant
    - restricted_tenant: requires template_slug (or type) in allowlist
    - also honors document.ai_processing_allowed == False as hard deny
    """
    if document is None:
        return False

    if getattr(document, 'ai_processing_allowed', True) is False:
        return False

    cls = document_sensitivity(document)
    if cls == RESTRICTED_TENANT:
        allow = allowlist or frozenset()
        slug = (getattr(document, 'template_slug', None) or '').strip().lower()
        dtype = (getattr(document, 'document_type', None) or '').strip().lower()
        return bool(slug and slug in allow) or bool(dtype and dtype in allow)

    if cls == CONFIDENTIAL:
        # Confidential lease paperwork may be classified/extracted only when
        # the document row explicitly allows AI processing.
        return bool(getattr(document, 'ai_processing_allowed', True))

    return True


def apply_sensitivity_to_document(
    document,
    *,
    document_type: Optional[str] = None,
    transaction_side: Optional[str] = None,
    force: bool = False,
) -> str:
    """
    Set sensitivity_class, retention_until, and ai_processing_allowed on a
    TransactionDocument when lease/tenant signals are present.
    """
    inferred = infer_sensitivity_class(
        document_type=document_type,
        template_slug=getattr(document, 'template_slug', None),
        template_name=getattr(document, 'template_name', None),
        transaction_side=transaction_side,
    )

    if force or not normalize_sensitivity_class(
        getattr(document, 'sensitivity_class', None)
    ):
        document.sensitivity_class = inferred

    cls = document_sensitivity(document)
    if force or getattr(document, 'retention_until', None) is None:
        document.retention_until = retention_until_for(cls)

    if cls == RESTRICTED_TENANT:
        document.ai_processing_allowed = False
    elif cls == CONFIDENTIAL and getattr(document, 'ai_processing_allowed', None) is None:
        document.ai_processing_allowed = True

    return cls


def privacy_controls_enabled(org) -> bool:
    """Lease/tenant create path requires privacy controls + VTC pilot."""
    if org is None:
        return False
    from feature_flags import org_has_feature
    return bool(
        org_has_feature('BOB_VTC_PILOT', org)
        and org_has_feature(PRIVACY_CONTROLS_FLAG, org)
    )


def lease_bootstrap_allowed(
    *,
    org,
    side: Optional[str],
    document_type: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Gate bootstrap create_new for landlord/tenant sides.

    Returns (allowed, reason).
    """
    side_norm = (side or '').strip().lower()
    if side_norm not in ('landlord', 'tenant', 'lease'):
        return True, 'not_lease_side'

    if not privacy_controls_enabled(org):
        return False, 'privacy_controls_required'

    # Creating a lease/tenant tx is allowed once org privacy controls are on.
    # Individual restricted docs still cannot go to Telegram / unconstrained LLM.
    dtype = (document_type or '').lower()
    if any(h in dtype for h in _RESTRICTED_TENANT_HINTS):
        # Still allow tx creation — document will be marked restricted and gated.
        return True, 'restricted_doc_ok_with_gates'

    return True, 'ok'


def filter_telegram_safe_attachment_refs(
    attachment_refs: Optional[list],
    *,
    documents_by_id: Optional[dict[int, Any]] = None,
) -> list:
    """Drop attachment refs that point at Telegram-banned documents."""
    if not attachment_refs:
        return []
    docs = documents_by_id or {}
    safe = []
    for ref in attachment_refs:
        if not isinstance(ref, dict):
            safe.append(ref)
            continue
        doc_id = ref.get('document_id') or ref.get('transaction_document_id')
        if doc_id is None:
            safe.append(ref)
            continue
        doc = docs.get(int(doc_id))
        if doc is None:
            # Fail closed when we cannot verify the document.
            logger.info('Dropping attachment_ref %s — document not loaded', doc_id)
            continue
        if may_send_to_telegram(doc):
            safe.append(ref)
        else:
            logger.info(
                'Blocked Telegram attachment for document %s (class=%s)',
                doc_id, document_sensitivity(doc),
            )
    return safe
