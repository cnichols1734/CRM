"""
Exception playbooks (BOB VTC Phase 3).

Practical stubs that return suggested requirement updates as
TransactionChangeProposal payloads — never auto-applied.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional


PlaybookFn = Callable[..., Dict[str, Any]]


def _iso(d: date | datetime | None) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()


def playbook_inspection_fail(
    *,
    transaction_id: int,
    organization_id: int,
    option_period_end: Optional[date] = None,
    notes: str = '',
) -> Dict[str, Any]:
    """Suggest repairs / option extension tracking after a failed inspection."""
    due = option_period_end or (date.today() + timedelta(days=3))
    return {
        'playbook_key': 'inspection_fail',
        'change_type': 'exception_playbook_inspection_fail',
        'transaction_id': transaction_id,
        'organization_id': organization_id,
        'rationale': (
            'Inspection failed or major items found. Track repair negotiations '
            'and option-period decision; human approval required.'
            + (f' Notes: {notes}' if notes else '')
        ),
        'proposed_changes': {
            'requirements': [
                {
                    'requirement_key': 'inspection_repairs',
                    'title': 'Inspection repair negotiations',
                    'phase_key': 'due_diligence',
                    'package_key': 'seller_ctc',
                    'work_status': 'pending',
                    'due_at': _iso(due),
                    'risk_level': 'high',
                },
                {
                    'requirement_key': 'option_period_decision',
                    'title': 'Buyer option-period decision',
                    'phase_key': 'option_period',
                    'package_key': 'seller_ctc',
                    'work_status': 'pending',
                    'due_at': _iso(due),
                    'risk_level': 'high',
                },
            ],
        },
        'auto_apply': False,
    }


def playbook_financing_delay(
    *,
    transaction_id: int,
    organization_id: int,
    closing_date: Optional[date] = None,
    notes: str = '',
) -> Dict[str, Any]:
    """Suggest financing follow-ups when loan approval slips."""
    close = closing_date or (date.today() + timedelta(days=21))
    follow_up = close - timedelta(days=7)
    return {
        'playbook_key': 'financing_delay',
        'change_type': 'exception_playbook_financing_delay',
        'transaction_id': transaction_id,
        'organization_id': organization_id,
        'rationale': (
            'Financing delayed. Track lender status and closing readiness; '
            'human approval required.'
            + (f' Notes: {notes}' if notes else '')
        ),
        'proposed_changes': {
            'requirements': [
                {
                    'requirement_key': 'financing_status_update',
                    'title': 'Obtain financing status update from lender',
                    'phase_key': 'financing',
                    'package_key': 'seller_ctc',
                    'work_status': 'pending',
                    'due_at': _iso(follow_up),
                    'risk_level': 'medium',
                },
                {
                    'requirement_key': 'closing_date_extension_check',
                    'title': 'Confirm whether closing date extension is needed',
                    'phase_key': 'closing',
                    'package_key': 'seller_ctc',
                    'work_status': 'pending',
                    'due_at': _iso(follow_up),
                    'risk_level': 'medium',
                },
            ],
        },
        'auto_apply': False,
    }


def playbook_appraisal_gap(
    *,
    transaction_id: int,
    organization_id: int,
    closing_date: Optional[date] = None,
    notes: str = '',
) -> Dict[str, Any]:
    """Suggest appraisal gap / renegotiation tracking."""
    close = closing_date or (date.today() + timedelta(days=21))
    due = min(date.today() + timedelta(days=5), close)
    return {
        'playbook_key': 'appraisal_gap',
        'change_type': 'exception_playbook_appraisal_gap',
        'transaction_id': transaction_id,
        'organization_id': organization_id,
        'rationale': (
            'Appraisal came in below contract price. Track gap options '
            '(renegotiate, buyer covers, terminate); human approval required.'
            + (f' Notes: {notes}' if notes else '')
        ),
        'proposed_changes': {
            'requirements': [
                {
                    'requirement_key': 'appraisal_gap_resolution',
                    'title': 'Resolve appraisal gap with parties',
                    'phase_key': 'financing',
                    'package_key': 'seller_ctc',
                    'work_status': 'pending',
                    'due_at': _iso(due),
                    'risk_level': 'high',
                },
                {
                    'requirement_key': 'price_amendment_if_needed',
                    'title': 'Prepare price amendment if parties agree',
                    'phase_key': 'financing',
                    'package_key': 'seller_ctc',
                    'work_status': 'pending',
                    'due_at': _iso(due + timedelta(days=2)),
                    'risk_level': 'high',
                },
            ],
            # Never auto-touch price — proposal only flags the need.
            '_blocked_auto_keys': ['offer_price', 'sales_price', 'purchase_price'],
        },
        'auto_apply': False,
    }


PLAYBOOKS: Dict[str, PlaybookFn] = {
    'inspection_fail': playbook_inspection_fail,
    'financing_delay': playbook_financing_delay,
    'appraisal_gap': playbook_appraisal_gap,
}


def list_playbooks() -> List[str]:
    return sorted(PLAYBOOKS.keys())


def run_playbook(playbook_key: str, **kwargs) -> Dict[str, Any]:
    """
    Run a playbook and return a proposal-shaped payload.

    Does not persist — caller creates a TransactionChangeProposal if desired.
    """
    fn = PLAYBOOKS.get(playbook_key)
    if not fn:
        raise ValueError(f'Unknown playbook: {playbook_key}')
    result = fn(**kwargs)
    result['auto_apply'] = False
    return result


def create_playbook_proposal(playbook_key: str, **kwargs):
    """Create a pending TransactionChangeProposal from a playbook (no apply)."""
    from services.proposal_service import ProposalService

    payload = run_playbook(playbook_key, **kwargs)
    return ProposalService.create_proposal(
        transaction_id=payload['transaction_id'],
        organization_id=payload['organization_id'],
        change_type=payload['change_type'],
        proposed_changes=payload['proposed_changes'],
        rationale=payload.get('rationale'),
    )
