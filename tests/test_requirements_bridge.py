"""Tests for milestone → requirement bridge (Phase 1A)."""
from datetime import date, datetime

from models import (
    SellerAcceptedContract,
    SellerContractMilestone,
    TransactionRequirement,
    TransactionRequirementEvent,
    db,
)
from services.requirements_service import RequirementsService


def _accepted_contract(seed):
    contract = SellerAcceptedContract(
        organization_id=seed['org_a'],
        transaction_id=seed['tx_a'],
        created_by_id=seed['owner_a'],
        offer_id=None,
        status='active',
        effective_date=date(2026, 1, 1),
        closing_date=date(2026, 2, 15),
    )
    db.session.add(contract)
    db.session.commit()
    return contract


def _cleanup_bridge(req_id=None, milestone_id=None, contract_id=None):
    if req_id:
        TransactionRequirementEvent.query.filter_by(
            requirement_id=req_id,
        ).delete(synchronize_session=False)
        TransactionRequirement.query.filter_by(id=req_id).delete(
            synchronize_session=False,
        )
    if milestone_id:
        SellerContractMilestone.query.filter_by(id=milestone_id).delete(
            synchronize_session=False,
        )
    if contract_id:
        SellerAcceptedContract.query.filter_by(id=contract_id).delete(
            synchronize_session=False,
        )
    db.session.commit()


class TestRequirementsBridge:
    def test_bridge_earnest_money_milestone(self, app, seed):
        req_id = milestone_id = contract_id = None
        with app.app_context():
            try:
                contract = _accepted_contract(seed)
                contract_id = contract.id
                milestone = SellerContractMilestone(
                    organization_id=seed['org_a'],
                    transaction_id=seed['tx_a'],
                    accepted_contract_id=contract.id,
                    created_by_id=seed['owner_a'],
                    milestone_key='earnest_money',
                    title='Earnest Money Deposited',
                    due_at=datetime(2026, 1, 6, 17, 0),
                    status='not_started',
                    responsible_party='Buyer',
                )
                db.session.add(milestone)
                db.session.commit()
                milestone_id = milestone.id

                req = RequirementsService.bridge_milestone_to_requirement(
                    milestone, seed['org_a'],
                )
                db.session.commit()
                req_id = req.id

                assert req.transaction_id == seed['tx_a']
                assert req.organization_id == seed['org_a']
                assert req.requirement_key == 'earnest_money'
                assert req.title == 'Earnest Money Deposited'
                assert req.package_key == 'seller_ctc'
                assert req.phase_key == 'option_period'
                assert req.work_status == 'pending'
                assert req.source == 'milestone_bridge'
                assert req.source_milestone_id == milestone.id
                assert req.responsible_party_label == 'Buyer'
            finally:
                _cleanup_bridge(req_id, milestone_id, contract_id)

    def test_bridge_inspection_milestone(self, app, seed):
        req_id = milestone_id = contract_id = None
        with app.app_context():
            try:
                contract = _accepted_contract(seed)
                contract_id = contract.id
                milestone = SellerContractMilestone(
                    organization_id=seed['org_a'],
                    transaction_id=seed['tx_a'],
                    accepted_contract_id=contract.id,
                    created_by_id=seed['owner_a'],
                    milestone_key='inspection',
                    title='Home Inspection',
                    due_at=datetime(2026, 1, 15, 17, 0),
                    status='waiting',
                    responsible_party='Buyer',
                )
                db.session.add(milestone)
                db.session.commit()
                milestone_id = milestone.id

                req = RequirementsService.bridge_milestone_to_requirement(
                    milestone, seed['org_a'],
                )
                db.session.commit()
                req_id = req.id

                assert req.requirement_key == 'inspection'
                assert req.phase_key == 'due_diligence'
                assert req.work_status == 'pending'
            finally:
                _cleanup_bridge(req_id, milestone_id, contract_id)

    def test_bridge_completed_milestone(self, app, seed):
        req_id = milestone_id = contract_id = None
        with app.app_context():
            try:
                contract = _accepted_contract(seed)
                contract_id = contract.id
                milestone = SellerContractMilestone(
                    organization_id=seed['org_a'],
                    transaction_id=seed['tx_a'],
                    accepted_contract_id=contract.id,
                    created_by_id=seed['owner_a'],
                    milestone_key='final_walkthrough',
                    title='Final Walkthrough',
                    due_at=datetime(2026, 1, 6, 17, 0),
                    status='completed',
                    completed_at=datetime(2026, 1, 5, 14, 30),
                    responsible_party='Buyer',
                )
                db.session.add(milestone)
                db.session.commit()
                milestone_id = milestone.id

                req = RequirementsService.bridge_milestone_to_requirement(
                    milestone, seed['org_a'],
                )
                db.session.commit()
                req_id = req.id

                assert req.work_status == 'completed'
                assert req.requirement_key == 'final_walkthrough'
            finally:
                _cleanup_bridge(req_id, milestone_id, contract_id)

    def test_infer_phase_from_milestone_key(self):
        assert RequirementsService._infer_phase_from_milestone_key('earnest_money') == 'option_period'
        assert RequirementsService._infer_phase_from_milestone_key('option_fee') == 'option_period'
        assert RequirementsService._infer_phase_from_milestone_key('inspection') == 'due_diligence'
        assert RequirementsService._infer_phase_from_milestone_key('survey') == 'due_diligence'
        assert RequirementsService._infer_phase_from_milestone_key('appraisal') == 'financing'
        assert RequirementsService._infer_phase_from_milestone_key('financing') == 'financing'
        assert RequirementsService._infer_phase_from_milestone_key('final_walkthrough') == 'closing'
        assert RequirementsService._infer_phase_from_milestone_key('closing') == 'closing'
        assert RequirementsService._infer_phase_from_milestone_key('unknown_key') == 'other'

    def test_map_milestone_status(self):
        assert RequirementsService._map_milestone_status('not_started') == 'pending'
        assert RequirementsService._map_milestone_status('waiting') == 'pending'
        assert RequirementsService._map_milestone_status('due_soon') == 'pending'
        assert RequirementsService._map_milestone_status('overdue') == 'pending'
        assert RequirementsService._map_milestone_status('completed') == 'completed'
        assert RequirementsService._map_milestone_status('not_applicable') == 'not_applicable'
        assert RequirementsService._map_milestone_status('unknown') == 'pending'
