"""
Transaction Communications Service - Phase 1C

Ledger + approved outbox for outbound transaction messages.
Gmail path is draft-only (never auto-sends). Portal path creates PortalMessage.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from models import (
    ClientPortalAccess,
    CommunicationDeliveryAttempt,
    PortalMessage,
    TransactionCommunication,
    db,
)

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({'sent', 'cancelled'})
RETRYABLE_STATUSES = frozenset({'queued', 'failed', 'ambiguous'})


class TransactionCommsService:
    """Outbound transaction communications ledger and delivery."""

    @staticmethod
    def create_communication(
        transaction_id: int,
        organization_id: int,
        communication_type: str,
        channel: str,
        body: str,
        subject: Optional[str] = None,
        participant_id: Optional[int] = None,
        communication_metadata: Optional[dict] = None,
        created_by_bob: bool = False,
        *,
        created_by_user_id: Optional[int] = None,
        purpose: Optional[str] = None,
        recipients: Optional[list] = None,
        cc: Optional[list] = None,
    ) -> TransactionCommunication:
        """Create a draft communication record."""
        comm = TransactionCommunication(
            transaction_id=transaction_id,
            organization_id=organization_id,
            communication_type=communication_type,
            channel=channel,
            body=body,
            subject=subject,
            participant_id=participant_id,
            communication_metadata=communication_metadata or {},
            created_by_bob=created_by_bob,
            created_by_user_id=created_by_user_id,
            purpose=purpose,
            recipients=recipients or [],
            cc=cc or [],
            direction='outbound',
            status='draft',
        )
        db.session.add(comm)
        db.session.flush()
        return comm

    @staticmethod
    def compute_payload_hash(
        *,
        channel: str,
        subject: Optional[str],
        body: str,
        recipients: Optional[list] = None,
        cc: Optional[list] = None,
        attachment_refs: Optional[list] = None,
    ) -> str:
        payload = {
            'channel': channel,
            'subject': subject or '',
            'body': body or '',
            'recipients': recipients or [],
            'cc': cc or [],
            'attachment_refs': attachment_refs or [],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    @staticmethod
    def queue_approved_send(
        *,
        transaction_id: int,
        organization_id: int,
        channel: str,
        body: str,
        client_idempotency_key: str,
        approved_payload_hash: str,
        approved_by_user_id: int,
        subject: Optional[str] = None,
        recipients: Optional[list] = None,
        cc: Optional[list] = None,
        attachment_refs: Optional[list] = None,
        participant_id: Optional[int] = None,
        requirement_id: Optional[int] = None,
        communication_type: str = 'client_update',
        purpose: Optional[str] = None,
        communication_metadata: Optional[dict] = None,
        created_by_bob: bool = False,
        process_now: bool = True,
    ) -> TransactionCommunication:
        """
        Queue an approved outbound send (idempotent by client_idempotency_key).

        Retries with the same key return the existing row without re-sending.
        Gmail is draft-only and requires the approval flag on the row.
        """
        key = (client_idempotency_key or '').strip()
        if not key:
            raise ValueError('client_idempotency_key is required')

        channel = (channel or '').strip().lower()
        if channel not in ('email', 'portal'):
            raise ValueError('channel must be email or portal')

        existing = TransactionCommunication.query.filter_by(
            organization_id=organization_id,
            client_idempotency_key=key,
        ).first()
        if existing:
            if existing.status in TERMINAL_STATUSES:
                return existing
            if existing.status in RETRYABLE_STATUSES and process_now:
                return TransactionCommsService.process_queued_communication(existing.id)
            return existing

        expected = TransactionCommsService.compute_payload_hash(
            channel=channel,
            subject=subject,
            body=body,
            recipients=recipients,
            cc=cc,
            attachment_refs=attachment_refs,
        )
        if approved_payload_hash and approved_payload_hash != expected:
            raise ValueError('approved_payload_hash does not match payload')

        meta = dict(communication_metadata or {})
        meta['approved'] = True

        now = datetime.utcnow()
        comm = TransactionCommunication(
            transaction_id=transaction_id,
            organization_id=organization_id,
            participant_id=participant_id,
            requirement_id=requirement_id,
            communication_type=communication_type,
            channel=channel,
            direction='outbound',
            purpose=purpose,
            subject=subject,
            body=body,
            recipients=recipients or [],
            cc=cc or [],
            attachment_refs=attachment_refs or [],
            approved_payload_hash=approved_payload_hash or expected,
            client_idempotency_key=key,
            communication_metadata=meta,
            status='queued',
            created_by_user_id=approved_by_user_id,
            approved_by_user_id=approved_by_user_id,
            approved_at=now,
            created_by_bob=created_by_bob,
        )
        try:
            with db.session.begin_nested():
                db.session.add(comm)
                db.session.flush()
        except IntegrityError:
            existing = TransactionCommunication.query.filter_by(
                organization_id=organization_id,
                client_idempotency_key=key,
            ).first()
            if existing:
                if existing.status in RETRYABLE_STATUSES and process_now:
                    return TransactionCommsService.process_queued_communication(
                        existing.id
                    )
                return existing
            raise

        if process_now:
            return TransactionCommsService.process_queued_communication(comm.id)
        return comm

    @staticmethod
    def process_queued_communication(communication_id: int) -> TransactionCommunication:
        """
        Attempt delivery for a queued/retryable communication.

        Records a CommunicationDeliveryAttempt. Unknown provider outcomes
        mark status=ambiguous (manual review / reconcile required).
        """
        comm = TransactionCommunication.query.get(communication_id)
        if not comm:
            raise ValueError(f'Communication {communication_id} not found')

        if comm.status == 'sent':
            return comm
        if comm.status == 'cancelled':
            return comm

        if not TransactionCommsService._is_approved(comm):
            raise ValueError(
                'Communication must be approved before delivery '
                '(approved_at / approved flag required)'
            )

        last = (
            CommunicationDeliveryAttempt.query
            .filter_by(communication_id=comm.id)
            .order_by(CommunicationDeliveryAttempt.attempt_number.desc())
            .first()
        )
        attempt_number = (last.attempt_number + 1) if last else 1
        started = datetime.utcnow()

        attempt = CommunicationDeliveryAttempt(
            communication_id=comm.id,
            organization_id=comm.organization_id,
            attempt_number=attempt_number,
            status='sending',
            started_at=started,
            attempted_at=started,
        )
        db.session.add(attempt)
        comm.status = 'sending'
        comm.locked_at = started
        comm.locked_by = f'comms:{attempt_number}'
        db.session.flush()

        try:
            if comm.channel == 'portal':
                result = TransactionCommsService._deliver_portal(comm)
            elif comm.channel == 'email':
                result = TransactionCommsService._deliver_gmail_draft(comm)
            else:
                result = {
                    'outcome': 'failed',
                    'error': f'Unsupported channel: {comm.channel}',
                    'provider': None,
                }
        except TimeoutError as exc:
            result = {
                'outcome': 'ambiguous',
                'error': f'Timeout with unknown provider outcome: {exc}',
                'provider': 'gmail_draft' if comm.channel == 'email' else comm.channel,
                'provider_response': {'exception': 'TimeoutError'},
            }
        except Exception as exc:
            logger.exception('Communication delivery failed id=%s', comm.id)
            # Unknown whether provider accepted — do not claim sent or failed.
            result = {
                'outcome': 'ambiguous',
                'error': f'Unknown delivery outcome: {exc}',
                'provider': 'gmail_draft' if comm.channel == 'email' else comm.channel,
                'provider_response': {'exception': type(exc).__name__},
            }

        finished = datetime.utcnow()
        outcome = result.get('outcome') or 'failed'
        attempt.status = outcome
        attempt.finished_at = finished
        attempt.provider = result.get('provider')
        attempt.provider_message_id = result.get('provider_message_id')
        attempt.provider_response = result.get('provider_response')
        attempt.error = result.get('error')

        comm.status = outcome if outcome in (
            'sent', 'failed', 'ambiguous', 'cancelled',
        ) else 'failed'
        comm.last_error = result.get('error')
        comm.locked_at = None
        comm.locked_by = None
        if result.get('provider_message_id'):
            comm.provider_message_id = result['provider_message_id']
        if result.get('provider_thread_id'):
            comm.provider_thread_id = result['provider_thread_id']
        if outcome == 'failed':
            comm.next_attempt_at = None

        db.session.flush()
        return comm

    @staticmethod
    def record_delivery_attempt(
        communication_id: int,
        organization_id: int,
        status: str,
        provider: Optional[str] = None,
        provider_message_id: Optional[str] = None,
        provider_response: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> CommunicationDeliveryAttempt:
        """Record a delivery attempt and sync parent communication status."""
        last_attempt = (
            CommunicationDeliveryAttempt.query
            .filter_by(communication_id=communication_id)
            .order_by(CommunicationDeliveryAttempt.attempt_number.desc())
            .first()
        )
        attempt_number = (last_attempt.attempt_number + 1) if last_attempt else 1
        now = datetime.utcnow()

        attempt = CommunicationDeliveryAttempt(
            communication_id=communication_id,
            organization_id=organization_id,
            attempt_number=attempt_number,
            status=status,
            provider=provider,
            provider_message_id=provider_message_id,
            provider_response=provider_response,
            error=error,
            started_at=now,
            finished_at=now if status != 'sending' else None,
            attempted_at=now,
        )
        db.session.add(attempt)
        db.session.flush()

        comm = TransactionCommunication.query.get(communication_id)
        if comm:
            if status == 'sent':
                comm.status = 'sent'
            elif status == 'ambiguous':
                comm.status = 'ambiguous'
            elif status in ('failed', 'bounced'):
                comm.status = status
                comm.last_error = error
            db.session.flush()
        return attempt

    @staticmethod
    def queue_for_delivery(communication_id: int) -> TransactionCommunication:
        """Mark a communication as queued for delivery (must already be approved)."""
        comm = TransactionCommunication.query.get(communication_id)
        if not comm:
            raise ValueError(f'Communication {communication_id} not found')
        if not TransactionCommsService._is_approved(comm):
            raise ValueError('Approve the communication before queueing delivery')
        if comm.status == 'sent':
            return comm
        comm.status = 'queued'
        db.session.flush()
        return comm

    @staticmethod
    def list_communications(
        transaction_id: Optional[int] = None,
        status: Optional[str] = None,
        created_by_bob: Optional[bool] = None,
    ) -> List[TransactionCommunication]:
        query = TransactionCommunication.query
        if transaction_id:
            query = query.filter_by(transaction_id=transaction_id)
        if status:
            query = query.filter_by(status=status)
        if created_by_bob is not None:
            query = query.filter_by(created_by_bob=created_by_bob)
        return query.order_by(TransactionCommunication.created_at.desc()).all()

    # ------------------------------------------------------------------
    # Channel deliveries
    # ------------------------------------------------------------------

    @staticmethod
    def _is_approved(comm: TransactionCommunication) -> bool:
        if comm.approved_at is not None and comm.approved_by_user_id is not None:
            return True
        meta = comm.communication_metadata or {}
        return bool(meta.get('approved'))

    @staticmethod
    def _client_safe_body(comm: TransactionCommunication) -> str:
        """
        Body shown to clients — never include internal notes / other-party PII
        stashed under metadata.internal_notes.
        """
        meta = comm.communication_metadata or {}
        # Prefer an explicit client-facing body if provided.
        client_body = meta.get('client_body')
        if isinstance(client_body, str) and client_body.strip():
            text = client_body.strip()
        else:
            text = (comm.body or '').strip()

        internal = meta.get('internal_notes')
        if isinstance(internal, str) and internal.strip() and internal in text:
            text = text.replace(internal, '').strip()

        # Strip common internal markers if agents pasted them into body.
        for marker in ('INTERNAL NOTE:', 'INTERNAL:', '[INTERNAL]'):
            if marker in text:
                text = text.split(marker, 1)[0].strip()

        return text

    @staticmethod
    def _deliver_portal(comm: TransactionCommunication) -> Dict[str, Any]:
        """Create a PortalMessage for the participant without leaking internals."""
        if not comm.participant_id:
            return {
                'outcome': 'failed',
                'error': 'participant_id required for portal channel',
                'provider': 'portal',
            }

        access = ClientPortalAccess.query.filter_by(
            transaction_id=comm.transaction_id,
            participant_id=comm.participant_id,
            organization_id=comm.organization_id,
            is_active=True,
        ).first()
        if not access:
            return {
                'outcome': 'failed',
                'error': 'No active portal access for participant',
                'provider': 'portal',
            }

        safe_body = TransactionCommsService._client_safe_body(comm)
        if not safe_body:
            return {
                'outcome': 'failed',
                'error': 'Empty client-safe body after filtering internal notes',
                'provider': 'portal',
            }

        # Avoid duplicate portal posts on idempotent retry.
        meta = dict(comm.communication_metadata or {})
        existing_msg_id = meta.get('portal_message_id')
        if existing_msg_id:
            existing = PortalMessage.query.get(existing_msg_id)
            if existing is not None:
                return {
                    'outcome': 'sent',
                    'provider': 'portal',
                    'provider_message_id': str(existing.id),
                    'provider_response': {'portal_message_id': existing.id, 'idempotent': True},
                }

        msg = PortalMessage(
            organization_id=comm.organization_id,
            transaction_id=comm.transaction_id,
            participant_id=comm.participant_id,
            sender='agent',
            kind='update',
            body=safe_body,
            author_user_id=comm.approved_by_user_id or comm.created_by_user_id,
        )
        db.session.add(msg)
        db.session.flush()

        meta['portal_message_id'] = msg.id
        # Do not persist internal_notes into the portal-visible path.
        meta.pop('internal_notes', None)
        comm.communication_metadata = meta
        db.session.flush()

        return {
            'outcome': 'sent',
            'provider': 'portal',
            'provider_message_id': str(msg.id),
            'provider_response': {'portal_message_id': msg.id, 'kind': 'update'},
        }

    @staticmethod
    def _deliver_gmail_draft(comm: TransactionCommunication) -> Dict[str, Any]:
        """
        Draft-only Gmail path. Requires prior approval on the communication row.
        Never calls messages.send — only drafts.create.
        """
        if not TransactionCommsService._is_approved(comm):
            return {
                'outcome': 'failed',
                'error': 'Gmail draft requires prior approval on the communication row',
                'provider': 'gmail_draft',
            }

        # Idempotent: already have a provider draft id.
        if comm.provider_message_id:
            return {
                'outcome': 'sent',
                'provider': 'gmail_draft',
                'provider_message_id': comm.provider_message_id,
                'provider_thread_id': comm.provider_thread_id,
                'provider_response': {'idempotent': True},
            }

        user_id = comm.approved_by_user_id or comm.created_by_user_id
        if not user_id:
            return {
                'outcome': 'failed',
                'error': 'No approving user for Gmail draft',
                'provider': 'gmail_draft',
            }

        from models import UserEmailIntegration
        integration = UserEmailIntegration.query.filter_by(user_id=user_id).first()
        if not integration or not integration.access_token_encrypted:
            return {
                'outcome': 'failed',
                'error': 'Gmail not connected for approving user',
                'provider': 'gmail_draft',
            }

        to_emails = TransactionCommsService._recipient_emails(comm.recipients)
        cc_emails = TransactionCommsService._recipient_emails(comm.cc)
        if not to_emails:
            return {
                'outcome': 'failed',
                'error': 'No recipient emails for Gmail draft',
                'provider': 'gmail_draft',
            }

        safe_body = TransactionCommsService._client_safe_body(comm)
        from services.gmail_service import create_draft

        try:
            result = create_draft(
                integration,
                to_emails=to_emails,
                subject=comm.subject or '',
                body_html=safe_body,
                cc_emails=cc_emails or None,
            )
        except TimeoutError:
            raise
        except Exception as exc:
            # Bubble as ambiguous only when we cannot know if draft landed.
            raise RuntimeError(str(exc)) from exc

        if not result.get('success'):
            return {
                'outcome': 'failed',
                'error': result.get('error') or 'Gmail draft create failed',
                'provider': 'gmail_draft',
                'provider_response': result,
            }

        return {
            'outcome': 'sent',
            'provider': 'gmail_draft',
            'provider_message_id': result.get('draft_id') or result.get('message_id'),
            'provider_thread_id': result.get('thread_id'),
            'provider_response': result,
        }

    @staticmethod
    def _recipient_emails(recipients: Optional[list]) -> List[str]:
        emails: List[str] = []
        for item in recipients or []:
            if isinstance(item, str) and '@' in item:
                emails.append(item.strip())
            elif isinstance(item, dict):
                email = (item.get('email') or '').strip()
                if email and '@' in email:
                    emails.append(email)
        return emails
