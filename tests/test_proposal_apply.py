"""Phase 1B proposal apply + supporting-doc change_type wiring."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from services.contract_bootstrap import (
    propose_supporting_document_updates,
    supporting_document_change_type,
)
from services.proposal_service import ProposalService


def test_supporting_document_change_types():
    cases = [
        ('amendment-to-contract', 'amendment'),
        ('earnest-money-receipt', 'earnest_receipt'),
        ('inspection-report', 'inspection'),
        ('appraisal-report', 'appraisal'),
        ('title-commitment', 'title'),
        ('clear-to-close', 'clear_to_close'),
        ('settlement-statement', 'settlement'),
        ('termination-agreement', 'termination'),
        ('cda-track', 'cda_track'),
        ('seller-accepted-contract', 'extracted_contract_fields'),
    ]
    for slug, expected in cases:
        doc = SimpleNamespace(template_slug=slug, template_name=slug)
        assert supporting_document_change_type(document=doc, field_data={}) == expected


def test_propose_supporting_document_updates_creates_proposal():
    doc = SimpleNamespace(
        id=11,
        organization_id=7,
        transaction_id=42,
        template_slug='amendment-to-contract',
        template_name='Amendment',
    )
    field_data = {'closing_date': '2026-10-01', 'option_period_days': 10}

    fake_proposal = SimpleNamespace(id=99, change_type='amendment')
    with patch('services.contract_bootstrap.TransactionChangeProposal') as TCP, \
         patch('services.contract_bootstrap.ProposalService.create_proposal', return_value=fake_proposal) as create:
        TCP.query.filter_by.return_value.all.return_value = []
        result = propose_supporting_document_updates(
            document=doc,
            field_data=field_data,
            extraction_run_id=5,
        )
    assert result is fake_proposal
    kwargs = create.call_args.kwargs
    assert kwargs['change_type'] == 'amendment'
    assert kwargs['proposed_changes'] == field_data
    assert kwargs['source_extraction_run_id'] == 5
    assert kwargs['source_document_id'] == 11


def test_apply_proposal_updates_transaction_and_is_idempotent():
    changes = {
        'street_address': '6004 Lakeside',
        'proposed_close_date': '2026-10-15',
        'option_period_days': 7,
        'effective_date': '2026-09-01',
    }
    proposal = SimpleNamespace(
        id=3,
        organization_id=7,
        transaction_id=42,
        source_document_id=11,
        change_type='extracted_contract_fields',
        proposed_changes=changes,
        status='approved',
        applied_audit_event_id=None,
    )
    transaction = SimpleNamespace(
        id=42,
        organization_id=7,
        street_address='Old',
        expected_close_date=None,
    )
    audit = SimpleNamespace(id=100)

    with patch('services.proposal_service.TransactionChangeProposal') as TCP, \
         patch('services.proposal_service.Transaction') as TX, \
         patch('services.proposal_service.SellerAcceptedContract') as SAC, \
         patch('services.proposal_service.TransactionRequirement') as TR, \
         patch('services.proposal_service.AuditEvent') as AE, \
         patch('services.proposal_service.db') as db, \
         patch.object(ProposalService, '_apply_requirement_deadlines') as deadlines:
        TCP.query.get.return_value = proposal
        TX.query.get.return_value = transaction
        SAC.query.filter_by.return_value.first.return_value = None
        TR.query.filter_by.return_value.all.return_value = []
        AE.log.return_value = audit
        AE.query.get.return_value = audit

        first = ProposalService.apply_proposal(3, actor_id=9, bob_action_id=55)
        assert first is audit
        assert transaction.street_address == '6004 Lakeside'
        assert transaction.expected_close_date == date(2026, 10, 15)
        assert proposal.status == 'applied'
        assert proposal.applied_audit_event_id == 100
        AE.log.assert_called()
        assert AE.log.call_args.kwargs.get('organization_id') == 7
        assert AE.log.call_args.kwargs.get('bob_action_id') == 55
        deadlines.assert_called_once()
        db.session.flush.assert_called()

        # Idempotent second call
        second = ProposalService.apply_proposal(3, actor_id=9)
        assert second is audit


def test_approve_and_apply_selected_filters_fields():
    proposal = SimpleNamespace(
        id=3,
        organization_id=7,
        transaction_id=42,
        source_document_id=None,
        change_type='amendment',
        proposed_changes={
            'closing_date': '2026-10-01',
            'street_address': 'Keep Me',
            'option_period_days': 5,
        },
        status='pending',
        applied_audit_event_id=None,
        reviewed_by_id=None,
        reviewed_at=None,
    )
    audit = SimpleNamespace(id=200)

    with patch('services.proposal_service.TransactionChangeProposal') as TCP, \
         patch.object(ProposalService, 'apply_proposal', return_value=audit) as apply, \
         patch('services.proposal_service.db'), \
         patch('services.proposal_service.flag_modified'):
        TCP.query.get.return_value = proposal
        result = ProposalService.approve_and_apply_selected(
            proposal_id=3,
            reviewed_by_id=9,
            selected_fields={
                'closing_date': True,
                'street_address': False,
                'option_period_days': True,
            },
            corrections={'closing_date': '2026-11-01'},
        )
    assert result is audit
    assert proposal.status == 'approved'
    assert proposal.proposed_changes == {
        'closing_date': '2026-11-01',
        'option_period_days': 5,
    }
    apply.assert_called_once()
