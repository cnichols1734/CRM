"""Background processing for contract-first transaction intake."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

from jobs.base import set_job_org_context

logger = logging.getLogger(__name__)


def _notify_ready(session, *, failed: bool = False) -> None:
    """Notify the uploader when a contract review is ready or failed."""
    from config import Config
    from feature_flags import org_has_feature
    from models import Notification, Organization, User
    from services.notification_service import create_notification

    user = User.query.filter_by(
        id=session.uploader_user_id,
        organization_id=session.organization_id,
    ).first()
    if not user:
        return

    classification = session.classification or {}
    address = classification.get('property_address') or session.original_filename or 'your contract'
    action_path = f'/transactions/bootstrap/{session.id}/review'
    existing = Notification.query.filter_by(
        user_id=user.id,
        organization_id=session.organization_id,
        category='document_review',
        action_url=action_path,
    ).first()
    if existing:
        return
    if failed:
        title = 'BOB could not finish the contract review'
        body = (
            f'I could not read {address}. Open the upload to try again or choose a clearer PDF.'
        )
        icon = 'fa-exclamation-triangle'
    else:
        title = f'Contract review ready: {address}'
        body = 'BOB finished reading the contract. Review the deal details before the transaction is created or updated.'
        icon = 'fa-file-contract'

    Notification.CATEGORIES.setdefault('document_review', 'Document Review')
    create_notification(
        user_id=user.id,
        organization_id=session.organization_id,
        category='document_review',
        title=title,
        body=body,
        icon=icon,
        action_url=action_path,
    )

    try:
        org = Organization.query.get(session.organization_id)
        if not org or not org_has_feature('BOB_TELEGRAM', org):
            return
        from services.messaging.outbound import notify

        absolute_url = f'{Config.APP_BASE_URL}{action_path}'
        notify(
            user,
            'document_review',
            f'{title}\n\n{body}\n\nOpen review: {absolute_url}\n\n--BOB',
            respect_quiet_hours=True,
        )
    except Exception:
        logger.exception(
            'Bootstrap Telegram notification failed for session %s',
            session.id,
        )


def _resolved_side(classification: dict) -> str | None:
    """Side confirmed by the agent, or confidently inferred from the document."""
    from services import contract_bootstrap

    side = contract_bootstrap._normalize_side(classification.get('side'))
    if side not in ('buyer', 'seller'):
        return None
    if (
        classification.get('side_confirmed_by_user')
        or classification.get('side_inferred_from_document')
    ):
        return side
    return side


def process_contract_bootstrap_job(
    session_id: int,
    org_id: int,
    _inline: bool = False,
) -> None:
    """Extract, classify, and prepare the human review without canonical writes.

    Representation side is optional up front. BOB infers it from the form type
    (listing → seller, buyer-rep → buyer) or the Broker Information block. When
    the PDF is silent, review asks — the job does not fail for that.
    """
    from models import ContractBootstrapSession, User, db
    from services import contract_bootstrap

    try:
        set_job_org_context(org_id)
        session = ContractBootstrapSession.query.filter_by(
            id=session_id,
            organization_id=org_id,
        ).first()
        if not session:
            logger.error('Contract bootstrap session %s was not found', session_id)
            return
        if session.status in (
            ContractBootstrapSession.STATUS_APPLIED,
            ContractBootstrapSession.STATUS_CANCELLED,
            ContractBootstrapSession.STATUS_AWAITING_MATCH,
            ContractBootstrapSession.STATUS_AWAITING_REVIEW,
            ContractBootstrapSession.STATUS_FAILED,
        ):
            return

        classification = dict(session.classification or {})
        file_bytes = None
        for attempt in range(3):
            file_bytes = contract_bootstrap.read_bootstrap_file(session)
            if file_bytes:
                break
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
        if not file_bytes:
            raise ValueError('The uploaded contract could not be read from storage.')

        session.status = ContractBootstrapSession.STATUS_PROCESSING
        classification['processing_started_at'] = (
            classification.get('processing_started_at') or datetime.utcnow().isoformat()
        )
        classification.pop('processing_error', None)
        session.classification = classification
        flag_modified(session, 'classification')
        for attempt in range(3):
            try:
                db.session.commit()
                break
            except Exception as commit_exc:
                db.session.rollback()
                if 'database is locked' not in str(commit_exc).lower() or attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
                set_job_org_context(org_id)
                session = ContractBootstrapSession.query.filter_by(
                    id=session_id,
                    organization_id=org_id,
                ).first()
                if not session:
                    return
                classification = dict(session.classification or {})
                classification['processing_started_at'] = (
                    classification.get('processing_started_at')
                    or datetime.utcnow().isoformat()
                )
                classification.pop('processing_error', None)
                session.status = ContractBootstrapSession.STATUS_PROCESSING
                session.classification = classification
                flag_modified(session, 'classification')
        set_job_org_context(org_id)

        identity = contract_bootstrap.classify_upload_identity(
            file_bytes=file_bytes,
            filename=session.original_filename,
        )
        classification = dict(session.classification or {})
        classification['document_identity'] = identity.to_dict()

        # Early form-type inference (listing / buyer-rep) before AI extraction.
        if not classification.get('side_confirmed_by_user'):
            user = User.query.filter_by(
                id=session.uploader_user_id,
                organization_id=org_id,
            ).first()
            inference = contract_bootstrap.infer_side_for_upload(
                identity=identity,
                file_bytes=file_bytes,
                user=user,
            )
            classification['representation_inference'] = inference.to_dict()
            if inference.is_confident:
                classification['side'] = inference.side
                classification['side_inferred_from_document'] = inference.side

        session.classification = classification
        flag_modified(session, 'classification')
        # Commit identity + "processing" so the batch wait list can update this
        # row in real time while the slower AI extraction runs.
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            set_job_org_context(org_id)
            session = ContractBootstrapSession.query.filter_by(
                id=session_id,
                organization_id=org_id,
            ).first()
            if not session:
                return
            classification = dict(session.classification or {})
            classification['document_identity'] = identity.to_dict()
            session.classification = classification
            session.status = ContractBootstrapSession.STATUS_PROCESSING
            flag_modified(session, 'classification')
            db.session.commit()

        field_data = contract_bootstrap.extract_contract_fields(
            file_bytes=file_bytes,
            identity=identity,
            filename=session.original_filename,
        )
        if not field_data:
            raise ValueError(
                'BOB could not find readable contract details in this PDF.'
            )

        session = ContractBootstrapSession.query.filter_by(
            id=session_id,
            organization_id=org_id,
        ).first()
        contract_bootstrap.classify_and_extract(
            session=session,
            field_data=field_data,
            identity=identity,
        )
        contract_bootstrap.run_match_discovery(session)

        # Propose a new transaction only when side is known. Otherwise leave
        # the session in awaiting_match so review can ask representation first.
        side = _resolved_side(session.classification or {})
        if not (session.match_candidates or []) and side:
            contract_bootstrap.resolve_match(
                session=session,
                decision='create_new',
                side=side,
            )
        elif not (session.match_candidates or []) and not side:
            session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW
            classification = dict(session.classification or {})
            classification['needs_side_confirmation'] = True
            session.classification = classification
            flag_modified(session, 'classification')

        classification = dict(session.classification or {})
        classification['processing_completed_at'] = datetime.utcnow().isoformat()
        session.classification = classification
        flag_modified(session, 'classification')
        db.session.commit()
        _notify_ready(session)
    except Exception as exc:
        logger.exception(
            'Contract bootstrap processing failed for session %s',
            session_id,
        )
        db.session.rollback()
        try:
            set_job_org_context(org_id)
            session = ContractBootstrapSession.query.filter_by(
                id=session_id,
                organization_id=org_id,
            ).first()
            if session:
                classification = dict(session.classification or {})
                classification['processing_error'] = str(exc)[:500]
                classification['processing_failed_at'] = datetime.utcnow().isoformat()
                session.classification = classification
                session.status = ContractBootstrapSession.STATUS_FAILED
                flag_modified(session, 'classification')
                db.session.commit()
                _notify_ready(session, failed=True)
        except Exception:
            logger.exception(
                'Could not mark contract bootstrap session %s failed',
                session_id,
            )
    finally:
        if not _inline:
            db.session.remove()
