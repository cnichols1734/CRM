"""
Requirements Service - Phase 1A/1B/1C Foundation

Manages TransactionRequirement CRUD and bridges SellerContractMilestone → Requirements.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, time, timedelta

from models import (
    db,
    Task,
    TaskSubtype,
    TaskType,
    TransactionRequirement,
    SellerContractMilestone,
    Transaction,
    TransactionRequirementEvent,
)

CLOSED_WORK_STATUSES = frozenset({
    'completed', 'waived', 'cancelled', 'not_applicable', 'superseded',
})


class RequirementsService:
    """
    Service for managing transaction requirements.
    """

    @staticmethod
    def create_requirement(
        transaction_id: int,
        organization_id: int,
        package_key: str,
        phase_key: str,
        requirement_key: str,
        title: str,
        **kwargs
    ) -> TransactionRequirement:
        """
        Create a new transaction requirement.

        Args:
            transaction_id: Transaction ID
            organization_id: Organization ID
            package_key: Package key (e.g., 'seller_ctc')
            phase_key: Phase key (e.g., 'option_period')
            requirement_key: Unique requirement key (e.g., 'earnest_money')
            title: Human-readable title
            **kwargs: Optional fields (due_at, work_status, assignee_user_id, etc.)

        Returns:
            Created TransactionRequirement instance
        """
        req = TransactionRequirement(
            transaction_id=transaction_id,
            organization_id=organization_id,
            package_key=package_key,
            phase_key=phase_key,
            requirement_key=requirement_key,
            title=title,
            **kwargs
        )
        db.session.add(req)
        db.session.flush()

        # Log creation event
        RequirementsService._log_event(
            requirement=req,
            event_type='created',
            actor_id=kwargs.get('assignee_user_id'),
            description=f'Requirement created: {title}'
        )

        return req

    @staticmethod
    def list_requirements(
        transaction_id: int,
        work_status: Optional[str] = None,
        phase_key: Optional[str] = None
    ) -> List[TransactionRequirement]:
        """
        List requirements for a transaction.

        Args:
            transaction_id: Transaction ID
            work_status: Optional status filter
            phase_key: Optional phase filter

        Returns:
            List of TransactionRequirement instances
        """
        query = TransactionRequirement.query.filter_by(transaction_id=transaction_id)

        if work_status:
            query = query.filter_by(work_status=work_status)
        if phase_key:
            query = query.filter_by(phase_key=phase_key)

        return query.order_by(TransactionRequirement.due_at.asc()).all()

    @staticmethod
    def update_work_status(
        requirement_id: int,
        new_status: str,
        actor_id: Optional[int] = None
    ) -> TransactionRequirement:
        """
        Update requirement work status.

        When moving to completed/waived/cancelled, cancels pending
        NotificationEvents for this requirement (history preserved).
        """
        req = TransactionRequirement.query.get(requirement_id)
        if not req:
            raise ValueError(f'Requirement {requirement_id} not found')

        old_status = req.work_status
        req.work_status = new_status
        req.timing_state = RequirementsService.derive_timing_state(
            req.due_at, new_status,
        )
        req.updated_at = datetime.utcnow()

        RequirementsService._log_event(
            requirement=req,
            event_type='status_changed',
            actor_id=actor_id,
            old_value={'work_status': old_status},
            new_value={'work_status': new_status},
            description=f'Status changed: {old_status} → {new_status}'
        )

        if new_status in ('completed', 'waived', 'cancelled'):
            from services.notification_outbox import NotificationOutboxService
            NotificationOutboxService.cancel_events_for_requirement(req.id)

        db.session.flush()
        return req

    @staticmethod
    def create_task_from_requirement(
        requirement_id: int,
        user_id: int,
    ) -> Task:
        """
        Create a human Task from a requirement and set requirement.task_id.

        One-way link only: completing the Task does **not** auto-complete the
        requirement — evidence / waiver policy still owns work_status.
        """
        req = TransactionRequirement.query.get(requirement_id)
        if not req:
            raise ValueError(f'Requirement {requirement_id} not found')

        if req.task_id:
            existing = Task.query.get(req.task_id)
            if existing is not None:
                return existing

        task_type = (
            TaskType.query
            .filter_by(organization_id=req.organization_id)
            .order_by(TaskType.sort_order, TaskType.id)
            .first()
        )
        if not task_type:
            raise ValueError('No task types configured for this organization')

        subtype = (
            TaskSubtype.query
            .filter_by(
                organization_id=req.organization_id,
                task_type_id=task_type.id,
            )
            .order_by(TaskSubtype.sort_order, TaskSubtype.id)
            .first()
        )
        if not subtype:
            raise ValueError(f'No subtypes configured for task type {task_type.name}')

        due = req.due_at
        if due is None:
            due = datetime.utcnow().replace(
                hour=23, minute=59, second=59, microsecond=0,
            )
        elif isinstance(due, datetime):
            if due.hour == 0 and due.minute == 0 and due.second == 0:
                due = datetime.combine(due.date(), time(23, 59, 59))

        tx = Transaction.query.get(req.transaction_id)
        property_address = getattr(tx, 'street_address', None) if tx else None

        task = Task(
            organization_id=req.organization_id,
            contact_id=None,
            transaction_id=req.transaction_id,
            assigned_to_id=user_id,
            created_by_id=user_id,
            type_id=task_type.id,
            subtype_id=subtype.id,
            subject=(req.title or req.requirement_key)[:200],
            description=(
                f'Linked requirement: {req.requirement_key}. '
                'Completing this task does not auto-complete the requirement.'
            ),
            priority='medium',
            due_date=due,
            property_address=property_address,
        )
        db.session.add(task)
        db.session.flush()

        req.task_id = task.id
        req.updated_at = datetime.utcnow()
        RequirementsService._log_event(
            requirement=req,
            event_type='task_linked',
            actor_id=user_id,
            new_value={'task_id': task.id},
            description=f'Task {task.id} created from requirement',
        )
        db.session.flush()
        return task

    @staticmethod
    def update_due_at(
        requirement_id: int,
        due_at: Optional[datetime],
        actor_id: Optional[int] = None,
        manual: bool = False,
    ) -> TransactionRequirement:
        """
        Update requirement due_at, retaining prior value for amendment history.
        Does not delete or recreate the requirement.

        ``manual=True`` marks the date as agent-chosen so automated recompute
        leaves it alone. A manual clear (due_at=None) releases the override.
        """
        req = TransactionRequirement.query.get(requirement_id)
        if not req:
            raise ValueError(f'Requirement {requirement_id} not found')

        old_due = req.due_at
        if old_due == due_at:
            if manual and due_at is not None and not req.due_at_manual_override:
                req.due_at_manual_override = True
                db.session.flush()
            return req

        if old_due is not None:
            req.prior_due_at = old_due
            req.due_at_superseded_at = datetime.utcnow()
        req.due_at = due_at
        req.updated_at = datetime.utcnow()
        req.version = (req.version or 1) + 1
        if manual:
            req.due_at_manual_override = due_at is not None

        RequirementsService._log_event(
            requirement=req,
            event_type='due_changed',
            actor_id=actor_id,
            old_value={'due_at': old_due.isoformat() if old_due else None},
            new_value={
                'due_at': due_at.isoformat() if due_at else None,
                'manual': manual,
            },
            description=(
                'Due date set manually by agent'
                if manual
                else 'Due date superseded from approved proposal'
            ),
        )
        db.session.flush()
        return req

    @staticmethod
    def update_risk_level(
        requirement_id: int,
        risk_level: str,
        actor_id: Optional[int] = None,
        reason: str = '',
    ) -> TransactionRequirement:
        req = TransactionRequirement.query.get(requirement_id)
        if not req:
            raise ValueError(f'Requirement {requirement_id} not found')
        old = req.risk_level
        req.risk_level = risk_level
        req.updated_at = datetime.utcnow()
        RequirementsService._log_event(
            requirement=req,
            event_type='risk_changed',
            actor_id=actor_id,
            old_value={'risk_level': old},
            new_value={'risk_level': risk_level, 'reason': reason or None},
            description=f'Risk changed: {old} → {risk_level}',
        )
        db.session.flush()
        return req

    @staticmethod
    def derive_timing_state(
        due_at: Optional[datetime],
        work_status: Optional[str],
        *,
        now: Optional[datetime] = None,
        upcoming_days: int = 5,
    ) -> str:
        """Derive timing_state from due_at + work_status."""
        status = (work_status or 'pending').lower()
        if status in ('completed',):
            return 'completed'
        if status in ('waived', 'cancelled', 'not_applicable', 'superseded'):
            return status
        if due_at is None:
            return 'no_deadline'
        now = now or datetime.utcnow()
        if due_at < now:
            return 'overdue'
        if due_at <= now + timedelta(days=upcoming_days):
            return 'due_soon'
        return 'on_time'

    @staticmethod
    def _bridge_title_for_milestone(milestone: SellerContractMilestone) -> str:
        """Prefer the milestone's explicit title; fall back to pack, then key."""
        explicit = (milestone.title or '').strip()
        if explicit:
            return explicit
        try:
            from services.deadline_rules import DeadlineRulesService
            req_def = DeadlineRulesService.get_requirement_definition(
                'seller_ctc', milestone.milestone_key, 'v1',
            ) or {}
            pack_title = (req_def.get('title') or '').strip()
            if pack_title:
                return pack_title
        except Exception:
            pass
        return milestone.milestone_key

    @staticmethod
    def _sync_bridged_requirement_title(
        requirement: TransactionRequirement,
        milestone: SellerContractMilestone,
        title: str,
    ) -> bool:
        """Apply milestone title to an existing requirement when explicitly set."""
        if not (milestone.title or '').strip():
            return False
        if requirement.title == title:
            return False
        requirement.title = title
        requirement.updated_at = datetime.utcnow()
        return True

    @staticmethod
    def bridge_milestone_to_requirement(
        milestone: SellerContractMilestone,
        organization_id: int
    ) -> TransactionRequirement:
        """
        Bridge a SellerContractMilestone into a TransactionRequirement.

        Idempotent: returns the existing row when already bridged by
        source_milestone_id or (transaction_id, requirement_key).
        An explicit milestone.title always wins over a prior pack/default title.
        """
        title = RequirementsService._bridge_title_for_milestone(milestone)

        existing = TransactionRequirement.query.filter_by(
            organization_id=organization_id,
            source_milestone_id=milestone.id,
        ).first()
        if existing:
            if RequirementsService._sync_bridged_requirement_title(
                existing, milestone, title,
            ):
                db.session.flush()
            return existing

        existing = TransactionRequirement.query.filter_by(
            transaction_id=milestone.transaction_id,
            requirement_key=milestone.milestone_key,
        ).first()
        if existing:
            changed = False
            if not existing.source_milestone_id:
                existing.source_milestone_id = milestone.id
                existing.source = existing.source or 'milestone_bridge'
                changed = True
            if RequirementsService._sync_bridged_requirement_title(
                existing, milestone, title,
            ):
                changed = True
            if changed:
                db.session.flush()
            return existing

        package_key = 'seller_ctc'
        phase_key = RequirementsService._infer_phase_from_milestone_key(
            milestone.milestone_key,
        )
        work_status = RequirementsService._map_milestone_status(milestone.status)
        timing_state = RequirementsService.derive_timing_state(
            milestone.due_at, work_status,
        )

        return RequirementsService.create_requirement(
            transaction_id=milestone.transaction_id,
            organization_id=organization_id,
            package_key=package_key,
            phase_key=phase_key,
            requirement_key=milestone.milestone_key,
            title=title,
            due_at=milestone.due_at,
            work_status=work_status,
            timing_state=timing_state,
            source='milestone_bridge',
            source_milestone_id=milestone.id,
            responsible_party_label=milestone.responsible_party,
        )

    @staticmethod
    def backfill_transaction_requirements(
        transaction_id: int,
        org_id: int,
    ) -> Dict[str, Any]:
        """
        Idempotently bridge all SellerContractMilestone rows for a transaction.

        Returns counts for created vs already-present requirements.
        """
        milestones = SellerContractMilestone.query.filter_by(
            transaction_id=transaction_id,
            organization_id=org_id,
        ).order_by(SellerContractMilestone.due_at.asc()).all()

        created = 0
        skipped = 0
        bridged: List[TransactionRequirement] = []
        for milestone in milestones:
            already = TransactionRequirement.query.filter(
                TransactionRequirement.organization_id == org_id,
                TransactionRequirement.transaction_id == transaction_id,
                db.or_(
                    TransactionRequirement.source_milestone_id == milestone.id,
                    TransactionRequirement.requirement_key == milestone.milestone_key,
                ),
            ).first()
            req = RequirementsService.bridge_milestone_to_requirement(
                milestone, org_id,
            )
            if already:
                skipped += 1
            else:
                created += 1
            bridged.append(req)

        if created:
            db.session.flush()

        return {
            'transaction_id': transaction_id,
            'milestones': len(milestones),
            'created': created,
            'skipped': skipped,
            'requirement_ids': [r.id for r in bridged],
        }

    @staticmethod
    def _infer_phase_from_milestone_key(milestone_key: str) -> str:
        """
        Infer phase from milestone key.

        Phase 2 will use deadline packs for this.
        """
        if milestone_key in ['earnest_money', 'option_fee']:
            return 'option_period'
        elif milestone_key in ['inspection', 'survey']:
            return 'due_diligence'
        elif milestone_key in ['appraisal', 'financing']:
            return 'financing'
        elif milestone_key in ['final_walkthrough', 'closing']:
            return 'closing'
        else:
            return 'other'

    @staticmethod
    def _map_milestone_status(milestone_status: str) -> str:
        """
        Map SellerContractMilestone status to TransactionRequirement work_status.
        """
        mapping = {
            'not_started': 'pending',
            'waiting': 'pending',
            'due_soon': 'pending',
            'overdue': 'pending',
            'completed': 'completed',
            'not_applicable': 'not_applicable'
        }
        return mapping.get(milestone_status, 'pending')

    @staticmethod
    def _log_event(
        requirement: TransactionRequirement,
        event_type: str,
        actor_id: Optional[int] = None,
        old_value: Optional[Dict[str, Any]] = None,
        new_value: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None
    ):
        """Log a requirement event."""
        event = TransactionRequirementEvent(
            organization_id=requirement.organization_id,
            requirement_id=requirement.id,
            event_type=event_type,
            actor_id=actor_id,
            actor_type='user' if actor_id else 'system',
            old_value=old_value,
            new_value=new_value,
            description=description
        )
        db.session.add(event)
