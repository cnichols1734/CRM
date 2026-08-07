"""Phase 3 narrow autonomy auto-approve tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.proposal_service import (
    AUTO_APPROVE_CHANGE_TYPES,
    ProposalService,
)


def _proposal(**kwargs):
    base = {
        'id': 1,
        'organization_id': 7,
        'transaction_id': 42,
        'source_document_id': None,
        'change_type': 'missing_doc_checklist',
        'proposed_changes': {
            'checklist': {'inspection_report': 'missing'},
            '_autonomy': {'confidence': 0.5, 'risk': 'low'},
        },
        'status': 'pending',
        'applied_audit_event_id': None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_auto_approve_change_types_are_narrow():
    assert 'missing_doc_checklist' in AUTO_APPROVE_CHANGE_TYPES
    assert 'extracted_contract_fields' not in AUTO_APPROVE_CHANGE_TYPES
    assert 'amendment' not in AUTO_APPROVE_CHANGE_TYPES


def test_auto_approve_eligible_requires_flags_and_thresholds():
    proposal = _proposal()
    org = SimpleNamespace(id=7, feature_flags={})

    with patch('services.proposal_service.Organization') as Org, \
         patch('feature_flags.org_has_feature', return_value=False):
        Org.query.get.return_value = org
        assert ProposalService.auto_approve_eligible(proposal, org=org) is False

    def _flags(name, o=None):
        return name in ('BOB_VTC_PILOT', 'BOB_VTC_NARROW_AUTONOMY')

    with patch('feature_flags.org_has_feature', side_effect=_flags), \
         patch.object(
             ProposalService, '_autonomy_thresholds',
             return_value=(0.85, 'low'),
         ):
        assert ProposalService.auto_approve_eligible(proposal, org=org) is True


def test_auto_approve_rejects_price_and_party_keys():
    org = SimpleNamespace(id=7)

    def _flags(name, o=None):
        return True

    with patch('feature_flags.org_has_feature', side_effect=_flags), \
         patch.object(
             ProposalService, '_autonomy_thresholds',
             return_value=(0.85, 'low'),
         ):
        price = _proposal(proposed_changes={
            'sales_price': 500000,
            '_autonomy': {'confidence': 0.1, 'risk': 'low'},
        })
        assert ProposalService.auto_approve_eligible(price, org=org) is False

        party = _proposal(proposed_changes={
            'buyer_name': 'Jane Doe',
            '_autonomy': {'confidence': 0.1, 'risk': 'low'},
        })
        assert ProposalService.auto_approve_eligible(party, org=org) is False

        commission = _proposal(proposed_changes={
            'buyer_agent_commission_percent': 3,
            '_autonomy': {'confidence': 0.1, 'risk': 'low'},
        })
        assert ProposalService.auto_approve_eligible(commission, org=org) is False


def test_auto_approve_rejects_high_risk_and_high_confidence():
    org = SimpleNamespace(id=7)

    def _flags(name, o=None):
        return True

    with patch('feature_flags.org_has_feature', side_effect=_flags), \
         patch.object(
             ProposalService, '_autonomy_thresholds',
             return_value=(0.85, 'low'),
         ):
        risky = _proposal(proposed_changes={
            'checklist': {'x': 1},
            '_autonomy': {'confidence': 0.2, 'risk': 'high'},
        })
        assert ProposalService.auto_approve_eligible(risky, org=org) is False

        unsure = _proposal(proposed_changes={
            'checklist': {'x': 1},
            '_autonomy': {'confidence': 0.99, 'risk': 'low'},
        })
        assert ProposalService.auto_approve_eligible(unsure, org=org) is False


def test_auto_approve_rejects_wrong_change_type():
    org = SimpleNamespace(id=7)

    def _flags(name, o=None):
        return True

    with patch('feature_flags.org_has_feature', side_effect=_flags):
        prop = _proposal(change_type='extracted_contract_fields')
        assert ProposalService.auto_approve_eligible(prop, org=org) is False


def test_auto_approve_and_apply_writes_audit_and_bob_action():
    proposal = _proposal()
    org = SimpleNamespace(id=7)
    tx = SimpleNamespace(id=42, created_by_id=9, organization_id=7)
    audit = SimpleNamespace(id=100, event_data={})

    bob_actions = []

    class FakeBobAction:
        STATUS_EXECUTED = 'executed'

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.id = 55
            bob_actions.append(self)

    with patch.object(ProposalService, 'auto_approve_eligible', return_value=True), \
         patch('services.proposal_service.TransactionChangeProposal') as TCP, \
         patch('services.proposal_service.Transaction') as TX, \
         patch('services.proposal_service.BobAction', FakeBobAction), \
         patch('services.proposal_service.AuditEvent') as AE, \
         patch('services.proposal_service.db') as db, \
         patch.object(ProposalService, 'approve_proposal') as approve, \
         patch.object(ProposalService, 'apply_proposal', return_value=audit) as apply:
        TCP.query.get.return_value = proposal
        TX.query.get.return_value = tx
        AE.log.return_value = SimpleNamespace(id=101)

        result = ProposalService.auto_approve_and_apply(1)

        assert result is audit
        approve.assert_called_once_with(1, 9)
        apply.assert_called_once()
        assert bob_actions, 'expected BobAction created'
        action = bob_actions[0]
        assert action.tool_name == 'auto_approve_proposal'
        assert action.result['easy_undo'] is True
        assert action.result['undo_marker'] == 'autonomy_auto_approve'
        assert audit.event_data.get('autonomy') is True
        AE.log.assert_called()
        autonomy_call = AE.log.call_args
        assert autonomy_call.kwargs.get('event_type') == 'proposal_auto_approved' \
            or autonomy_call.args[:1] == () and autonomy_call.kwargs['event_type'] == 'proposal_auto_approved'


def test_exception_playbooks_return_proposals_only():
    from services.exception_playbooks import list_playbooks, run_playbook

    keys = list_playbooks()
    assert 'inspection_fail' in keys
    assert 'financing_delay' in keys
    assert 'appraisal_gap' in keys

    payload = run_playbook(
        'appraisal_gap',
        transaction_id=1,
        organization_id=2,
    )
    assert payload['auto_apply'] is False
    assert payload['proposed_changes']['_blocked_auto_keys']
    assert 'sales_price' in payload['proposed_changes']['_blocked_auto_keys']
