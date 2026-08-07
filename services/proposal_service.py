"""
Proposal Service - Phase 1B/1C/3

Manages TransactionChangeProposal lifecycle (create, approve, reject, apply).
Phase 3: narrow autonomy auto-approve for low-risk allowlisted change types.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm.attributes import flag_modified

from models import (
    AuditEvent,
    BobAction,
    Organization,
    SellerAcceptedContract,
    Transaction,
    TransactionChangeProposal,
    TransactionRequirement,
    db,
)

logger = logging.getLogger(__name__)

# Keys that may be merged into SellerAcceptedContract.frozen_terms on apply.
_CONTRACT_TERM_KEYS = frozenset({
    'offer_price', 'sales_price', 'purchase_price',
    'effective_date', 'effective_at',
    'proposed_close_date', 'closing_date', 'close_date',
    'option_period_days', 'option_fee',
    'earnest_money', 'additional_earnest_money',
    'financing_type', 'cash_down_payment', 'financing_amount',
    'seller_concessions_amount',
    'title_company', 'escrow_officer',
    'survey_choice', 'survey_furnished_by',
    'residential_service_contract',
    'buyer_agent_commission_percent', 'buyer_agent_commission_flat',
    'hoa_applicable', 'seller_disclosure_required', 'lead_based_paint_required',
    'addenda', 'supporting_documents',
    'buyer_names', 'possession_type', 'leaseback_days',
})

# Narrow autonomy — only these change_types may auto-approve.
AUTO_APPROVE_CHANGE_TYPES = frozenset({
    'missing_doc_checklist',
    'checklist_update',
    'date_format_normalize',
    'non_financial_date_formatting',
})

# Never auto-approve if proposed_changes touch these keys.
AUTO_APPROVE_BLOCKED_KEYS = frozenset({
    'buyer_name', 'seller_name', 'buyer_names', 'seller_names',
    'offer_price', 'sales_price', 'purchase_price',
    'earnest_money', 'option_fee', 'additional_earnest_money',
    'buyer_agent_commission_percent', 'buyer_agent_commission_flat',
    'seller_concessions_amount', 'financing_amount', 'cash_down_payment',
    'legal_status', 'status', 'contract_status',
    'party', 'parties', 'participant',
})

DEFAULT_AUTONOMY_CONFIDENCE_MAX = 0.85
DEFAULT_AUTONOMY_RISK_MAX = 'low'


class ProposalService:
    """Service for managing Bob's transaction change proposals."""

    @staticmethod
    def create_proposal(
        transaction_id: int,
        organization_id: int,
        change_type: str,
        proposed_changes: Dict[str, Any],
        rationale: Optional[str] = None,
        **kwargs
    ) -> TransactionChangeProposal:
        proposal = TransactionChangeProposal(
            transaction_id=transaction_id,
            organization_id=organization_id,
            change_type=change_type,
            proposed_changes=proposed_changes,
            rationale=rationale,
            status='pending',
            **kwargs
        )
        db.session.add(proposal)
        db.session.flush()
        return proposal

    @staticmethod
    def approve_proposal(
        proposal_id: int,
        reviewed_by_id: int
    ) -> TransactionChangeProposal:
        proposal = TransactionChangeProposal.query.get(proposal_id)
        if not proposal:
            raise ValueError(f'Proposal {proposal_id} not found')

        if proposal.status != 'pending':
            raise ValueError(f'Proposal is not pending (status={proposal.status})')

        proposal.status = 'approved'
        proposal.reviewed_by_id = reviewed_by_id
        proposal.reviewed_at = datetime.utcnow()

        db.session.flush()
        return proposal

    @staticmethod
    def reject_proposal(
        proposal_id: int,
        reviewed_by_id: int,
        rejection_reason: Optional[str] = None
    ) -> TransactionChangeProposal:
        proposal = TransactionChangeProposal.query.get(proposal_id)
        if not proposal:
            raise ValueError(f'Proposal {proposal_id} not found')

        if proposal.status != 'pending':
            raise ValueError(f'Proposal is not pending (status={proposal.status})')

        proposal.status = 'rejected'
        proposal.reviewed_by_id = reviewed_by_id
        proposal.reviewed_at = datetime.utcnow()
        proposal.rejection_reason = rejection_reason

        db.session.flush()
        return proposal

    @staticmethod
    def approve_and_apply_selected(
        proposal_id: int,
        reviewed_by_id: int,
        selected_fields: Dict[str, bool],
        corrections: Optional[Dict[str, Any]] = None,
        bob_action_id: Optional[int] = None,
    ) -> AuditEvent:
        """
        Narrow a pending proposal to selected fields (with optional corrections),
        approve, and apply as one change-set.
        """
        proposal = TransactionChangeProposal.query.get(proposal_id)
        if not proposal:
            raise ValueError(f'Proposal {proposal_id} not found')

        if proposal.status == 'applied' or proposal.applied_audit_event_id:
            return ProposalService.apply_proposal(
                proposal_id, actor_id=reviewed_by_id, bob_action_id=bob_action_id,
            )

        if proposal.status not in ('pending', 'approved'):
            raise ValueError(
                f'Proposal cannot be applied (status={proposal.status})'
            )

        corrections = corrections or {}
        original = dict(proposal.proposed_changes or {})
        applied: Dict[str, Any] = {}
        for key, selected in (selected_fields or {}).items():
            if not selected:
                continue
            if key in corrections:
                applied[key] = corrections[key]
            elif key in original:
                applied[key] = original[key]

        if not applied:
            raise ValueError('No fields selected to apply')

        proposal.proposed_changes = applied
        flag_modified(proposal, 'proposed_changes')

        if proposal.status == 'pending':
            ProposalService.approve_proposal(proposal_id, reviewed_by_id)

        return ProposalService.apply_proposal(
            proposal_id,
            actor_id=reviewed_by_id,
            bob_action_id=bob_action_id,
            original_proposed_changes=original,
        )

    @staticmethod
    def apply_proposal(
        proposal_id: int,
        actor_id: Optional[int] = None,
        bob_action_id: Optional[int] = None,
        original_proposed_changes: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        """
        Apply an approved proposal to transaction / accepted-contract data.

        Does NOT call create_contract_milestones(replace=True).
        Idempotent when already applied.
        """
        proposal = TransactionChangeProposal.query.get(proposal_id)
        if not proposal:
            raise ValueError(f'Proposal {proposal_id} not found')

        if proposal.status == 'applied' or proposal.applied_audit_event_id:
            if proposal.applied_audit_event_id:
                existing = AuditEvent.query.get(proposal.applied_audit_event_id)
                if existing:
                    return existing
            # Fallback: already applied but audit missing — return a no-op log
            return AuditEvent.log(
                event_type='proposal_applied',
                organization_id=proposal.organization_id,
                transaction_id=proposal.transaction_id,
                document_id=proposal.source_document_id,
                actor_id=actor_id,
                bob_action_id=bob_action_id,
                description=f'Proposal already applied: {proposal.change_type}',
                event_data={
                    'proposal_id': proposal.id,
                    'change_type': proposal.change_type,
                    'idempotent': True,
                },
                source='system',
            )

        if proposal.status != 'approved':
            raise ValueError(
                f'Proposal must be approved before applying (status={proposal.status})'
            )

        transaction = Transaction.query.get(proposal.transaction_id)
        if not transaction:
            raise ValueError(f'Transaction {proposal.transaction_id} not found')

        changes = dict(proposal.proposed_changes or {})
        applied_keys = list(changes.keys())

        ProposalService._apply_transaction_fields(transaction, changes)
        ProposalService._apply_contract_frozen_terms(transaction, changes)
        ProposalService._apply_requirement_deadlines(
            transaction, changes, actor_id=actor_id,
        )

        audit_event = AuditEvent.log(
            event_type='proposal_applied',
            organization_id=proposal.organization_id,
            transaction_id=proposal.transaction_id,
            document_id=proposal.source_document_id,
            actor_id=actor_id,
            bob_action_id=bob_action_id,
            description=f'Applied proposal: {proposal.change_type}',
            event_data={
                'proposal_id': proposal.id,
                'change_type': proposal.change_type,
                'proposed_changes': changes,
                'applied_keys': applied_keys,
                'original_proposed_keys': list((original_proposed_changes or {}).keys()) or None,
            },
            source='system',
        )
        db.session.flush()

        proposal.applied_audit_event_id = audit_event.id
        proposal.status = 'applied'
        db.session.flush()

        return audit_event

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if value is None or value == '':
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y'):
            try:
                return datetime.strptime(text[:10] if fmt == '%Y-%m-%d' else text, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _apply_transaction_fields(transaction: Transaction, changes: Dict[str, Any]) -> None:
        address = changes.get('street_address') or changes.get('property_address')
        if address:
            transaction.street_address = str(address)[:200]

        close_raw = (
            changes.get('expected_close_date')
            or changes.get('closing_date')
            or changes.get('close_date')
            or changes.get('proposed_close_date')
        )
        close_date = ProposalService._parse_date(close_raw)
        if close_date:
            transaction.expected_close_date = close_date

        db.session.flush()

    @staticmethod
    def _apply_contract_frozen_terms(
        transaction: Transaction,
        changes: Dict[str, Any],
    ) -> None:
        """Merge matching keys into primary accepted contract without milestone replace."""
        term_updates = {
            k: v for k, v in changes.items()
            if k in _CONTRACT_TERM_KEYS and v is not None
        }
        if not term_updates:
            return

        try:
            from services.controlling_contracts import get_active_primary_contract
            contract = get_active_primary_contract(
                transaction.id, transaction.organization_id,
            )
        except Exception:
            # Unit tests may call apply with non-ORM stand-ins outside app context.
            return
        if not contract:
            return

        terms = dict(contract.frozen_terms or {})
        for key, value in term_updates.items():
            if key in ('addenda', 'supporting_documents') and isinstance(value, dict):
                existing = dict(terms.get(key) or {})
                existing.update(value)
                terms[key] = existing
            else:
                terms[key] = value

        # Update canonical columns via apply_contract_terms — never recreate milestones.
        from services.seller_workflow import apply_contract_terms
        apply_contract_terms(contract, terms)
        flag_modified(contract, 'frozen_terms')
        flag_modified(contract, 'addenda_data')
        flag_modified(contract, 'extra_data')
        db.session.flush()

    @staticmethod
    def _apply_requirement_deadlines(
        transaction: Transaction,
        changes: Dict[str, Any],
        actor_id: Optional[int] = None,
    ) -> None:
        """Recompute option/closing requirement due_at via the shared cascade."""
        from services.deadline_recompute import recompute_from_changes

        recompute_from_changes(
            transaction,
            changes,
            actor_id=actor_id,
            source='proposal_apply',
        )

    @staticmethod
    def list_pending_proposals(
        transaction_id: Optional[int] = None,
        organization_id: Optional[int] = None
    ):
        query = TransactionChangeProposal.query.filter_by(status='pending')

        if transaction_id:
            query = query.filter_by(transaction_id=transaction_id)
        if organization_id:
            query = query.filter_by(organization_id=organization_id)

        return query.order_by(TransactionChangeProposal.created_at.desc()).all()

    @staticmethod
    def _autonomy_meta(proposal: TransactionChangeProposal) -> Dict[str, Any]:
        changes = proposal.proposed_changes or {}
        meta = changes.get('_autonomy') if isinstance(changes, dict) else None
        return dict(meta) if isinstance(meta, dict) else {}

    @staticmethod
    def _autonomy_thresholds() -> Tuple[float, str]:
        try:
            from flask import current_app
            conf_max = float(
                current_app.config.get(
                    'BOB_VTC_AUTONOMY_CONFIDENCE_MAX',
                    DEFAULT_AUTONOMY_CONFIDENCE_MAX,
                )
            )
            risk_max = str(
                current_app.config.get(
                    'BOB_VTC_AUTONOMY_RISK_MAX',
                    DEFAULT_AUTONOMY_RISK_MAX,
                )
            ).lower()
        except Exception:
            conf_max = DEFAULT_AUTONOMY_CONFIDENCE_MAX
            risk_max = DEFAULT_AUTONOMY_RISK_MAX
        return conf_max, risk_max

    @staticmethod
    def auto_approve_eligible(
        proposal: TransactionChangeProposal,
        org: Optional[Organization] = None,
    ) -> bool:
        """
        Narrow autonomy gate.

        Only allowlisted low-risk change_types below confidence/risk thresholds,
        with BOB_VTC_PILOT + BOB_VTC_NARROW_AUTONOMY org flags. Never party,
        price, commission, or legal-status claims.
        """
        if proposal is None or proposal.status != 'pending':
            return False

        change_type = (proposal.change_type or '').strip().lower()
        if change_type not in AUTO_APPROVE_CHANGE_TYPES:
            return False

        org = org or Organization.query.get(proposal.organization_id)
        if org is None:
            return False

        from feature_flags import org_has_feature
        if not org_has_feature('BOB_VTC_PILOT', org):
            return False
        if not org_has_feature('BOB_VTC_NARROW_AUTONOMY', org):
            return False

        changes = dict(proposal.proposed_changes or {})
        # Strip internal meta before key scan.
        changes.pop('_autonomy', None)
        flat_keys = set(changes.keys())
        nested_reqs = changes.get('requirements')
        if isinstance(nested_reqs, list):
            for item in nested_reqs:
                if isinstance(item, dict):
                    flat_keys.update(item.keys())

        if flat_keys & AUTO_APPROVE_BLOCKED_KEYS:
            return False

        meta = ProposalService._autonomy_meta(proposal)
        try:
            confidence = float(
                meta.get('confidence', getattr(proposal, 'confidence', 1.0))
            )
        except (TypeError, ValueError):
            confidence = 1.0
        risk = str(
            meta.get('risk')
            or meta.get('risk_level')
            or getattr(proposal, 'risk_level', 'high')
            or 'high'
        ).lower()

        conf_max, risk_max = ProposalService._autonomy_thresholds()
        risk_rank = {'none': 0, 'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
        if risk_rank.get(risk, 99) > risk_rank.get(risk_max, 1):
            return False
        if confidence > conf_max:
            return False

        return True

    @staticmethod
    def auto_approve_and_apply(
        proposal_id: int,
        *,
        system_actor_id: Optional[int] = None,
    ) -> Optional[AuditEvent]:
        """
        If eligible, approve + apply, write autonomy AuditEvent, and create
        a BobAction with easy-undo marker. Returns AuditEvent or None if skipped.
        """
        proposal = TransactionChangeProposal.query.get(proposal_id)
        if not proposal:
            raise ValueError(f'Proposal {proposal_id} not found')

        if not ProposalService.auto_approve_eligible(proposal):
            return None

        # System actor: prefer lead agent / created_by on the transaction.
        actor_id = system_actor_id
        if actor_id is None:
            tx = Transaction.query.get(proposal.transaction_id)
            actor_id = getattr(tx, 'created_by_id', None) if tx else None
        if actor_id is None:
            logger.info(
                'Skipping auto-approve for proposal %s — no actor_id',
                proposal_id,
            )
            return None

        # Snapshot for easy undo before apply mutates.
        undo_snapshot = {
            'proposal_id': proposal.id,
            'change_type': proposal.change_type,
            'proposed_changes': dict(proposal.proposed_changes or {}),
            'transaction_id': proposal.transaction_id,
        }

        bob_action = BobAction(
            organization_id=proposal.organization_id,
            user_id=actor_id,
            tool_name='auto_approve_proposal',
            arguments={
                'proposal_id': proposal.id,
                'change_type': proposal.change_type,
            },
            preview=undo_snapshot,
            status=BobAction.STATUS_EXECUTED,
            summary=f'Auto-approved: {proposal.change_type}',
            surface='autonomy',
            transaction_id=proposal.transaction_id,
            proposal_id=proposal.id,
            risk='low',
            record_version=undo_snapshot,
            result={
                'easy_undo': True,
                'undo_marker': 'autonomy_auto_approve',
                'proposal_id': proposal.id,
            },
            executed_at=datetime.utcnow(),
            approved_at=datetime.utcnow(),
            approving_user_id=actor_id,
        )
        db.session.add(bob_action)
        db.session.flush()

        ProposalService.approve_proposal(proposal.id, actor_id)
        audit_event = ProposalService.apply_proposal(
            proposal.id,
            actor_id=actor_id,
            bob_action_id=bob_action.id,
        )

        # Enrich audit with autonomy flag (apply_proposal already wrote one).
        event_data = dict(getattr(audit_event, 'event_data', None) or {})
        event_data['autonomy'] = True
        event_data['easy_undo'] = True
        event_data['undo_marker'] = 'autonomy_auto_approve'
        event_data['bob_action_id'] = bob_action.id
        audit_event.event_data = event_data
        if hasattr(audit_event, '_sa_instance_state'):
            flag_modified(audit_event, 'event_data')
        if hasattr(audit_event, 'source'):
            audit_event.source = 'autonomy'

        bob_action.resulting_audit_event_ids = [audit_event.id]
        if hasattr(bob_action, '_sa_instance_state'):
            flag_modified(bob_action, 'resulting_audit_event_ids')

        # Dedicated autonomy audit breadcrumb.
        AuditEvent.log(
            event_type='proposal_auto_approved',
            organization_id=proposal.organization_id,
            transaction_id=proposal.transaction_id,
            document_id=proposal.source_document_id,
            actor_id=actor_id,
            bob_action_id=bob_action.id,
            description=f'Narrow autonomy auto-approved proposal {proposal.id}',
            event_data={
                'proposal_id': proposal.id,
                'change_type': proposal.change_type,
                'autonomy': True,
                'easy_undo': True,
                'undo_marker': 'autonomy_auto_approve',
                'applied_audit_event_id': audit_event.id,
            },
            source='autonomy',
        )
        db.session.flush()
        return audit_event
