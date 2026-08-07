"""Centralized transaction authorization.

Capability checks for transaction routes, BOB tools, and jobs.
Owner/admin org_role retain break-glass access. Assignments
(lead_agent / transaction_coordinator / collaborator) are checked when present.

Do not create a parallel transaction_access module — this is the locked name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flask_login import current_user

from models import Transaction


ROLE_LEAD = 'lead_agent'
ROLE_TC = 'transaction_coordinator'
ROLE_COLLABORATOR = 'collaborator'

CAP_VIEW = 'view'
CAP_EDIT = 'edit'
CAP_COMPLIANCE_COMPLETE = 'compliance_complete'
CAP_ASSIGN = 'assign'
CAP_SEND_COMMS = 'send_comms'


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    reason: str = ''


def _org_role(user=None) -> Optional[str]:
    user = user or current_user
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    return getattr(user, 'org_role', None)


def is_org_break_glass(user=None) -> bool:
    """Owner/admin may access any transaction in their org."""
    return _org_role(user) in ('owner', 'admin')


def get_assignment(transaction: Transaction, user=None):
    """Return TransactionAssignment row if the model/table exists."""
    user = user or current_user
    if not user or not getattr(user, 'id', None):
        return None
    try:
        from models import TransactionAssignment
    except ImportError:
        return None
    return TransactionAssignment.query.filter_by(
        transaction_id=transaction.id,
        user_id=user.id,
        organization_id=transaction.organization_id,
    ).first()


def _assignment_capabilities(assignment) -> set[str]:
    caps = {CAP_VIEW}
    if not assignment:
        return caps
    if assignment.role in (ROLE_LEAD, ROLE_TC):
        caps.update({CAP_EDIT, CAP_SEND_COMMS, CAP_ASSIGN})
    if assignment.role == ROLE_LEAD:
        caps.add(CAP_COMPLIANCE_COMPLETE)
    extra = assignment.capabilities or {}
    if isinstance(extra, dict):
        for key, enabled in extra.items():
            if enabled:
                caps.add(key)
    elif isinstance(extra, list):
        caps.update(extra)
    return caps


def can_view_transaction(transaction: Transaction, user=None) -> AuthDecision:
    user = user or current_user
    if not user or not getattr(user, 'is_authenticated', False):
        return AuthDecision(False, 'unauthenticated')
    if getattr(user, 'organization_id', None) != transaction.organization_id:
        # Indistinguishable from missing — do not disclose cross-org existence.
        return AuthDecision(False, 'not_found')
    if is_org_break_glass(user):
        return AuthDecision(True, 'org_admin')
    if transaction.created_by_id == user.id:
        return AuthDecision(True, 'creator')
    assignment = get_assignment(transaction, user)
    if assignment:
        return AuthDecision(True, f'assignment:{assignment.role}')
    return AuthDecision(False, 'not_assigned')


def can_edit_transaction(transaction: Transaction, user=None) -> AuthDecision:
    view = can_view_transaction(transaction, user)
    if not view.allowed:
        return view
    user = user or current_user
    if is_org_break_glass(user) or transaction.created_by_id == user.id:
        return AuthDecision(True, view.reason)
    assignment = get_assignment(transaction, user)
    caps = _assignment_capabilities(assignment)
    if CAP_EDIT in caps:
        return AuthDecision(True, f'assignment:{assignment.role}')
    return AuthDecision(False, 'insufficient_role')


def has_capability(
    transaction: Transaction,
    capability: str,
    user=None,
) -> AuthDecision:
    view = can_view_transaction(transaction, user)
    if not view.allowed:
        return view
    user = user or current_user
    if is_org_break_glass(user):
        return AuthDecision(True, 'org_admin')
    if transaction.created_by_id == user.id and capability != CAP_COMPLIANCE_COMPLETE:
        # Creator can edit/send; compliance mark-complete still needs lead/TC+cap
        # or explicit capability — allow creator for compliance as lead substitute
        # until assignments are backfilled.
        if capability == CAP_COMPLIANCE_COMPLETE:
            return AuthDecision(True, 'creator_as_lead')
        return AuthDecision(True, 'creator')
    if capability == CAP_COMPLIANCE_COMPLETE and transaction.created_by_id == user.id:
        return AuthDecision(True, 'creator_as_lead')
    assignment = get_assignment(transaction, user)
    caps = _assignment_capabilities(assignment)
    if capability in caps:
        return AuthDecision(True, f'capability:{capability}')
    return AuthDecision(False, f'missing_capability:{capability}')


def require_transaction_access(
    transaction: Transaction,
    capability: str = CAP_VIEW,
    user=None,
) -> AuthDecision:
    """Primary entry point for routes and BOB tools."""
    if capability == CAP_VIEW:
        return can_view_transaction(transaction, user)
    if capability == CAP_EDIT:
        return can_edit_transaction(transaction, user)
    return has_capability(transaction, capability, user)


def get_transaction_for_user(
    transaction_id: int,
    user=None,
    capability: str = CAP_VIEW,
) -> tuple[Optional[Transaction], AuthDecision]:
    """Load a transaction scoped to the user's org and authorize it."""
    user = user or current_user
    if not user or not getattr(user, 'organization_id', None):
        return None, AuthDecision(False, 'unauthenticated')
    transaction = Transaction.query.filter_by(
        id=transaction_id,
        organization_id=user.organization_id,
    ).first()
    if not transaction:
        return None, AuthDecision(False, 'not_found')
    decision = require_transaction_access(transaction, capability, user)
    if not decision.allowed:
        return None, decision
    return transaction, decision


def transactions_visible_query(user=None):
    """Query transactions the user created or is assigned to (same org).

    Org break-glass (owner/admin) still use their own list filter unless the
    route opts into show_all — this helper is for the default agent list.
    """
    from sqlalchemy import or_

    from models import TransactionAssignment, db

    user = user or current_user
    if not user or not getattr(user, 'organization_id', None):
        return Transaction.query.filter_by(id=-1)

    assigned_ids = db.session.query(TransactionAssignment.transaction_id).filter_by(
        organization_id=user.organization_id,
        user_id=user.id,
    )
    return Transaction.query.filter(
        Transaction.organization_id == user.organization_id,
        or_(
            Transaction.created_by_id == user.id,
            Transaction.id.in_(assigned_ids),
        ),
    )
