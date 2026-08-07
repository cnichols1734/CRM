"""Telegram PDF intake after transaction selection (Phase 2 E2-5).

Routes attachment PDFs into ContractBootstrapSession / document-review propose
path. Never auto-applies extracted fields to CRM.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from models import (
    ContractBootstrapSession,
    Transaction,
    User,
    db,
)
from services.contract_bootstrap import (
    classify_and_extract,
    extract_contract_fields,
    record_upload_metadata,
    store_bootstrap_file,
)
from services.transaction_auth import CAP_EDIT, require_transaction_access

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 20 * 1024 * 1024
ALLOWED_MIME = frozenset({
    'application/pdf',
    'application/x-pdf',
})


class TelegramDocumentError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def download_telegram_document(transport: Any, file_id: str) -> bytes:
    try:
        meta = transport.get_file(file_id)
    except Exception as exc:
        logger.warning('Telegram getFile for document failed: %s', exc)
        raise TelegramDocumentError(
            "Couldn't download that file. Try again."
        ) from exc

    file_path = (meta or {}).get('file_path') or ''
    file_size = int((meta or {}).get('file_size') or 0)
    if file_size and file_size > MAX_PDF_BYTES:
        raise TelegramDocumentError(
            'That PDF is too large (20 MB max). Upload it in the CRM instead.'
        )
    if not file_path:
        raise TelegramDocumentError("Couldn't find that file. Try again.")

    try:
        data = transport.download_file(file_path)
    except Exception as exc:
        logger.warning('Telegram document download failed: %s', exc)
        raise TelegramDocumentError(
            "Couldn't download that file. Try again."
        ) from exc

    if not data:
        raise TelegramDocumentError('That file came through empty.')
    if len(data) > MAX_PDF_BYTES:
        raise TelegramDocumentError(
            'That PDF is too large (20 MB max). Upload it in the CRM instead.'
        )
    return data


def process_telegram_pdf_for_transaction(
    *,
    user: User,
    transaction: Transaction,
    file_bytes: bytes,
    filename: str,
    mime_type: str = 'application/pdf',
    run_extraction: bool = True,
) -> ContractBootstrapSession:
    """
    Store PDF against the selected transaction as a bootstrap/review session.

    Observation-only: creates ContractBootstrapSession in awaiting_review with
    matched_transaction_id set. Does NOT apply fields or mutate deal terms.
    """
    decision = require_transaction_access(transaction, CAP_EDIT, user)
    if not decision.allowed:
        raise TelegramDocumentError(
            "You're not authorized to upload documents on that transaction."
        )

    mime = (mime_type or 'application/pdf').lower()
    if mime not in ALLOWED_MIME and not (filename or '').lower().endswith('.pdf'):
        raise TelegramDocumentError('Only PDF files are accepted here.')

    # Phase 3 privacy: ban sensitive tenant docs on the Telegram intake path.
    from services.document_privacy import (
        CONFIDENTIAL,
        RESTRICTED_TENANT,
        infer_sensitivity_class,
        may_use_in_llm,
    )
    side = None
    if getattr(transaction, 'transaction_type', None):
        side = (transaction.transaction_type.name or '').lower()
    sensitivity = infer_sensitivity_class(
        document_type=filename,
        template_slug=filename,
        template_name=filename,
        transaction_side=side,
    )
    if sensitivity == RESTRICTED_TENANT:
        raise TelegramDocumentError(
            'That looks like a sensitive tenant document (ID, pay stub, or '
            'application). Upload it in the CRM secure portal — Telegram is '
            'not allowed for those files.'
        )

    sha = hashlib.sha256(file_bytes).hexdigest()
    existing = (
        ContractBootstrapSession.query
        .filter_by(
            organization_id=transaction.organization_id,
            file_sha256=sha,
            matched_transaction_id=transaction.id,
        )
        .order_by(ContractBootstrapSession.id.desc())
        .first()
    )
    if existing and existing.status not in (
        ContractBootstrapSession.STATUS_CANCELLED,
    ):
        logger.info(
            'Telegram PDF duplicate sha=%s session=%s tx=%s',
            sha[:8], existing.id, transaction.id,
        )
        return existing

    session = record_upload_metadata(
        file_bytes=file_bytes,
        filename=filename or 'telegram.pdf',
        mime_type=mime if mime in ALLOWED_MIME else 'application/pdf',
        source='telegram',
        user=user,
        org_id=transaction.organization_id,
    )
    store_bootstrap_file(session=session, file_bytes=file_bytes)

    field_data: dict = {}
    # Skip unconstrained LLM for confidential lease/tenant docs unless allowed.
    stub_doc = type('Doc', (), {
        'sensitivity_class': sensitivity,
        'template_slug': (filename or '').lower(),
        'template_name': filename,
        'document_type': filename,
        'ai_processing_allowed': sensitivity != CONFIDENTIAL,
    })()
    if run_extraction and may_use_in_llm(stub_doc):
        field_data = extract_contract_fields(file_bytes=file_bytes)
    elif run_extraction:
        logger.info(
            'Skipping Telegram PDF LLM extraction (privacy class=%s) tx=%s',
            sensitivity, transaction.id,
        )
        field_data = {
            'document_type': 'lease_or_tenant_document',
            '_privacy_blocked_llm': True,
            '_sensitivity_class': sensitivity,
        }
    classify_and_extract(session=session, field_data=field_data)
    classification = dict(session.classification or {})
    classification['sensitivity_class'] = sensitivity
    session.classification = classification
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(session, 'classification')

    # Transaction already selected — skip match discovery; await human review.
    session.matched_transaction_id = transaction.id
    session.match_status = ContractBootstrapSession.MATCH_MATCHED
    session.match_candidates = [{
        'transaction_id': transaction.id,
        'address': transaction.street_address,
        'score': 1.0,
        'reason': 'telegram_selected_transaction',
    }]
    session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW
    db.session.flush()
    # Document row + TransactionChangeProposal are created on human apply /
    # bootstrap attach — never auto-write CRM from this path.
    db.session.commit()
    logger.info(
        'Telegram PDF intake session=%s tx=%s sha=%s fields=%s (no auto-apply)',
        session.id,
        transaction.id,
        sha[:8],
        len(session.extracted_candidates or {}),
    )
    return session


def format_intake_reply(session: ContractBootstrapSession, transaction: Transaction) -> str:
    """Short Telegram reply after PDF intake."""
    from services.document_privacy import CONFIDENTIAL, RESTRICTED_TENANT

    classification = session.classification or {}
    sensitivity = classification.get('sensitivity_class')
    field_count = len(session.extracted_candidates or {})
    addr = transaction.street_address or f'transaction {transaction.id}'
    lines = [
        f'Received PDF for {addr}.',
        f'Review session #{session.id} is waiting in the CRM bootstrap inbox.',
        'Nothing was applied automatically — open the review screen to accept or reject fields.',
    ]
    if sensitivity in (CONFIDENTIAL, RESTRICTED_TENANT):
        lines.append(
            'Privacy controls applied: sensitive content stays in CRM '
            '(not summarized here).'
        )
    elif field_count:
        lines.append(f'Extracted {field_count} candidate field(s) for review.')
    else:
        lines.append('No fields extracted yet; you can still attach/review the file in CRM.')
    return '\n'.join(lines) + '\n\n--BOB'
