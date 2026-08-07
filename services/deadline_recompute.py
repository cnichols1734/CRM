"""
Deadline recompute cascade.

Single place where a change to a transaction's contract anchors (effective
date, option period, closing date) is turned into updated
``TransactionRequirement.due_at`` values.

Both proposal apply and amendment acceptance route through here so a term
change supersedes deadlines the same way regardless of where it came from.
Completed requirements are never touched — history is preserved by
``RequirementsService.update_due_at``, which records ``prior_due_at`` and
``due_at_superseded_at``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from models import SellerAcceptedContract, Transaction, TransactionRequirement

logger = logging.getLogger(__name__)

OPTION_INPUT_KEYS = ('option_period_days', 'option_days', 'effective_date')
CLOSING_INPUT_KEYS = (
    'closing_date', 'close_date', 'proposed_close_date', 'expected_close_date',
)


@dataclass
class RecomputeResult:
    """What the cascade changed, for audit trails and UI confirmation."""

    anchors: Dict[str, date] = field(default_factory=dict)
    updated: List[Dict[str, Any]] = field(default_factory=list)
    created: List[str] = field(default_factory=list)
    skipped_completed: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.updated or self.created)

    def as_dict(self) -> Dict[str, Any]:
        return {
            'anchors': {k: v.isoformat() for k, v in self.anchors.items()},
            'updated': self.updated,
            'created': self.created,
            'skipped_completed': self.skipped_completed,
        }


def parse_date(value: Any) -> Optional[date]:
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


def _primary_contract(transaction: Transaction) -> Optional[SellerAcceptedContract]:
    try:
        from services.controlling_contracts import get_active_primary_contract
        return get_active_primary_contract(
            transaction.id, transaction.organization_id,
        )
    except Exception:
        return None


def recompute_from_changes(
    transaction: Transaction,
    changes: Dict[str, Any],
    *,
    actor_id: Optional[int] = None,
    source: str = 'proposal_apply',
) -> RecomputeResult:
    """Recompute option/closing requirement due dates from a change set.

    ``changes`` is a flat dict of term keys (the same shape a proposal or an
    amendment version carries). Anchors missing from the change set fall back
    to the primary accepted contract's columns.
    """
    result = RecomputeResult()

    option_inputs = any(k in changes for k in OPTION_INPUT_KEYS)
    closing_inputs = any(k in changes for k in CLOSING_INPUT_KEYS)
    if not option_inputs and not closing_inputs:
        return result

    from services.deadline_rules import DeadlineRulesService
    from services.requirements_service import RequirementsService
    from models import db

    effective = parse_date(changes.get('effective_date'))
    closing = parse_date(
        changes.get('closing_date')
        or changes.get('close_date')
        or changes.get('proposed_close_date')
        or changes.get('expected_close_date')
        or transaction.expected_close_date
    )

    contract = _primary_contract(transaction)
    if contract:
        effective = effective or contract.effective_date
        closing = closing or contract.closing_date
        option_days = changes.get('option_period_days')
        if option_days is None:
            option_days = changes.get('option_days')
        if option_days is None:
            option_days = contract.option_period_days
    else:
        option_days = changes.get('option_period_days') or changes.get('option_days')

    try:
        option_days_int = int(option_days) if option_days is not None else None
    except (TypeError, ValueError):
        option_days_int = None

    anchors: Dict[str, date] = {}
    if effective:
        anchors['effective_date'] = effective
    if closing:
        anchors['closing_date'] = closing
    if effective and option_days_int is not None:
        anchors['option_period_end'] = effective + timedelta(days=option_days_int)

    if not anchors:
        return result
    result.anchors = anchors

    try:
        side_hint = (
            transaction.transaction_type.name
            if transaction.transaction_type else None
        )
        pack_key, pack = DeadlineRulesService.resolve_pack_for_transaction(
            transaction,
            side_hint=side_hint,
        )
    except FileNotFoundError:
        logger.info(
            'No deadline pack for transaction %s; skipping requirement deadline updates',
            transaction.id,
        )
        return result

    existing = {
        r.requirement_key: r
        for r in TransactionRequirement.query.filter_by(
            transaction_id=transaction.id,
            organization_id=transaction.organization_id,
        ).all()
    }

    for req_key, req_def in (pack.get('requirements') or {}).items():
        rule = req_def.get('deadline_rule') or {}
        anchor_name = rule.get('anchor')
        phase = req_def.get('phase') or ''

        impacted = False
        if option_inputs and (
            anchor_name in ('effective_date', 'option_period_end')
            or phase == 'option_period'
        ):
            impacted = True
        if closing_inputs and (anchor_name == 'closing_date' or phase == 'closing'):
            impacted = True
        if not impacted:
            continue

        anchor_date = anchors.get(anchor_name)
        if not anchor_date:
            continue
        try:
            due = DeadlineRulesService.calculate_deadline(anchor_date, rule)
        except Exception:
            continue

        due_at = datetime.combine(due, datetime.min.time()) if isinstance(due, date) else due
        req = existing.get(req_key)
        if req:
            if req.work_status == 'completed':
                result.skipped_completed.append(req_key)
                continue
            if getattr(req, 'due_at_manual_override', False):
                # The agent chose this date by hand; recompute must not undo it.
                continue
            prior = req.due_at
            RequirementsService.update_due_at(req.id, due_at, actor_id=actor_id)
            if prior != due_at:
                result.updated.append({
                    'requirement_key': req_key,
                    'title': req.title,
                    'prior_due_at': prior.isoformat() if prior else None,
                    'due_at': due_at.isoformat() if due_at else None,
                })
        else:
            RequirementsService.create_requirement(
                transaction_id=transaction.id,
                organization_id=transaction.organization_id,
                package_key=pack_key,
                phase_key=phase or 'unknown',
                requirement_key=req_key,
                title=req_def.get('title') or req_key,
                due_at=due_at,
                source=source,
                deadline_rule_version=pack.get('version'),
            )
            result.created.append(req_key)

    db.session.flush()
    return result
