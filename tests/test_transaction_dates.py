from datetime import date, datetime

from models import db, SellerAcceptedContract, SellerContractMilestone, TransactionDocument, TransactionRequirement
from services.seller_workflow import create_contract_milestones
from services.transaction_dates import transaction_date_rows
from services.portal_service import milestones_for_client_api
from services.client_calendar import render_calendar
from tests.test_client_portal_api import _seller_tx, _participant, _grant


def _file(seed):
    tx = _seller_tx(seed)
    participant = _participant(seed, tx, contact_id=seed['contact_a'])
    access = _grant(seed, tx, participant)
    doc = TransactionDocument(organization_id=tx.organization_id, transaction_id=tx.id,
        template_slug='listing-agreement', template_name='Listing agreement',
        field_data={'listing_start_date': '2026-10-01', 'listing_end_date': '2027-01-01'})
    db.session.add(doc)
    db.session.commit()
    return tx, access, doc


def _contract(tx):
    contract = SellerAcceptedContract(organization_id=tx.organization_id, transaction_id=tx.id,
        created_by_id=tx.created_by_id, position='primary', status='active',
        effective_date=date(2026, 10, 1), closing_date=date(2026, 11, 1), option_period_days=7,
        frozen_terms={'closing_date': '2026-11-01'})
    db.session.add(contract)
    db.session.flush()
    create_contract_milestones(contract)
    db.session.commit()
    return contract


def test_listing_edits_reset_clear_and_client_calendar_agree(app, seed, owner_a_client, monkeypatch):
    calls = []
    monkeypatch.setattr('services.device_push.enqueue_date_push', lambda **kw: calls.append(kw))
    with app.app_context():
        tx, access, doc = _file(seed)
        tid, aid, did = tx.id, access.id, doc.id
        tx.extra_data = {'listing_info_overrides': {'list_price': '450000'}}
        db.session.commit()
        assert {r['due_at'].date() for r in transaction_date_rows(db.session, seed['org_a'], tid)} == {date(2026, 10, 1), date(2027, 1, 1)}
    url = f'/transactions/{tid}/listing-dates'
    assert owner_a_client.post(url, json={'field': 'listing_end_date', 'value': 'bad'}).status_code == 400
    calls.clear()
    for _ in range(2):
        assert owner_a_client.post(url, json={'field': 'listing_end_date', 'value': '2027-02-01'}).status_code == 200
    assert len(calls) == 1
    with app.app_context():
        from models import ClientPortalAccess, Transaction
        access = db.session.get(ClientPortalAccess, aid)
        rows = milestones_for_client_api(access)['items']
        assert next(r for r in rows if r['id'] == 'listing:listing_end_date')['due_at'] == '2027-02-01'
        assert all(r['scope'] == 'listing' for r in rows)
        assert 'DTSTART;VALUE=DATE:20270201' in render_calendar(access)
        assert db.session.get(TransactionDocument, did).field_data['listing_end_date'] == '2027-01-01'
        assert db.session.get(Transaction, tid).extra_data['listing_info_overrides']['list_price'] == '450000'
    assert owner_a_client.post(url, json={'field': 'listing_end_date', 'value': ''}).status_code == 200
    with app.app_context():
        access = db.session.get(ClientPortalAccess, aid)
        assert all(r['id'] != 'listing:listing_end_date' for r in milestones_for_client_api(access)['items'])
    assert owner_a_client.post(url, json={'field': 'listing_end_date', 'action': 'reset'}).status_code == 200
    with app.app_context():
        assert 'DTSTART;VALUE=DATE:20270101' in render_calendar(db.session.get(ClientPortalAccess, aid))


def test_milestone_removal_restore_recalculation_preserve_facts_and_requirements(app, seed, owner_a_client):
    with app.app_context():
        tx, access, _ = _file(seed)
        contract = _contract(tx)
        milestone = contract.milestones.filter_by(milestone_key='closing_date').one()
        tid, cid, mid = tx.id, contract.id, milestone.id
        req = TransactionRequirement(organization_id=tx.organization_id, transaction_id=tid,
            requirement_key='closing_test', title='Closing requirement', package_key='seller_ctc', phase_key='closing',
            source_milestone_id=mid, due_at=milestone.due_at)
        db.session.add(req)
        db.session.commit()
        rid = req.id
    url = f'/transactions/{tid}/seller/contracts/{cid}/milestones/{mid}'
    for _ in range(2):
        assert owner_a_client.post(url, json={'action': 'remove'}).status_code == 200
    with app.app_context():
        contract = db.session.get(SellerAcceptedContract, cid)
        for _ in range(2):
            create_contract_milestones(contract, replace=True)
        db.session.commit()
        assert contract.closing_date == date(2026, 11, 1)
        assert contract.frozen_terms['closing_date'] == '2026-11-01'
        assert db.session.get(TransactionRequirement, rid).source_milestone_id == mid
        assert contract.milestones.filter_by(milestone_key='closing_date').count() == 1
        assert all(r['id'] != mid for r in transaction_date_rows(db.session, seed['org_a'], tid))
    assert owner_a_client.post(url, json={'action': 'restore'}).status_code == 200
    assert owner_a_client.post(url, json={'title': 'Closing', 'due_at': '2026-11-05', 'status': 'waiting'}).status_code == 200
    with app.app_context():
        contract = db.session.get(SellerAcceptedContract, cid)
        contract.closing_date = date(2026, 11, 3)
        create_contract_milestones(contract, replace=True)
        db.session.commit()
        assert db.session.get(SellerContractMilestone, mid).due_at.date() == date(2026, 11, 5)
        assert contract.milestones.filter_by(milestone_key='closing_date').count() == 1
    assert owner_a_client.post(url, json={'action': 'automatic'}).status_code == 200
    with app.app_context():
        assert db.session.get(SellerContractMilestone, mid).due_at.date() == date(2026, 11, 3)


def test_status_only_edit_keeps_automatic_date_and_invalid_edit_is_atomic(app, seed, owner_a_client):
    with app.app_context():
        tx, _, _ = _file(seed)
        contract = _contract(tx)
        milestone = contract.milestones.filter_by(milestone_key='closing_date').one()
        tid, cid, mid = tx.id, contract.id, milestone.id
    url = f'/transactions/{tid}/seller/contracts/{cid}/milestones/{mid}'
    assert owner_a_client.post(url, json={'title': 'Closing', 'due_at': '2026-11-01T17:00', 'status': 'waiting'}).status_code == 200
    assert owner_a_client.post(url, json={'title': 'Bad', 'due_at': 'invalid'}).status_code == 400
    with app.app_context():
        m = db.session.get(SellerContractMilestone, mid)
        assert m.title == 'Closing' and m.source == 'calculated'
        contract = db.session.get(SellerAcceptedContract, cid)
        contract.closing_date = date(2026, 11, 9)
        create_contract_milestones(contract, replace=True)
        db.session.commit()
        assert m.due_at.date() == date(2026, 11, 9) and m.status == 'waiting'


def test_stage_transition_never_uses_backup_or_terminated_contract(app, seed):
    with app.app_context():
        tx, access, _ = _file(seed)
        contract = _contract(tx)
        assert {r['scope'] for r in transaction_date_rows(db.session, tx.organization_id, tx.id)} == {'contract'}
        contract.status = 'terminated'
        backup = _contract(tx)
        backup.position = 'backup'
        db.session.commit()
        assert {r['scope'] for r in transaction_date_rows(db.session, tx.organization_id, tx.id)} == {'listing'}
        tx.status = 'under_contract'
        db.session.commit()
        assert transaction_date_rows(db.session, tx.organization_id, tx.id) == []
        assert transaction_date_rows(db.session, seed['org_b'], tx.id) == []


def test_date_mutations_reject_other_org_and_unassigned_agent(app, seed, owner_b_client, agent_a_client):
    with app.app_context():
        from models import Organization
        db.session.get(Organization, seed['org_b']).subscription_tier = 'pro'
        db.session.commit()
        tx, _, _ = _file(seed)
        contract = _contract(tx)
        milestone = contract.milestones.first()
        tid, cid, mid = tx.id, contract.id, milestone.id
    for client in (owner_b_client, agent_a_client):
        assert client.post(f'/transactions/{tid}/listing-dates', json={'field': 'go_live_date', 'value': '2026-10-01'}).status_code in (403, 404)
        assert client.post(f'/transactions/{tid}/seller/contracts/{cid}/milestones/{mid}', json={'action': 'remove'}).status_code in (403, 404)

    with app.app_context():
        db.session.get(Organization, seed['org_b']).subscription_tier = 'free'
        db.session.commit()


def test_approved_proposal_updates_calendar_in_place_and_rollback_is_silent(app, seed, monkeypatch):
    from models import TransactionChangeProposal
    from services.proposal_service import ProposalService
    calls = []
    monkeypatch.setattr('services.device_push.enqueue_date_push', lambda **kw: calls.append(kw))
    with app.app_context():
        tx, access, _ = _file(seed)
        contract = _contract(tx)
        row = contract.milestones.filter_by(milestone_key='closing_date').one()
        completed = contract.milestones.filter_by(milestone_key='final_walkthrough').one()
        completed.status = 'completed'
        completed_date = completed.due_at
        db.session.commit()
        original_id = row.id
        before_uids = {line for line in render_calendar(access).splitlines() if line.startswith('UID:')}
        proposal = TransactionChangeProposal(organization_id=tx.organization_id, transaction_id=tx.id,
            change_type='extracted_contract_fields', status='approved', proposed_changes={'closing_date': '2026-11-12'})
        db.session.add(proposal)
        db.session.commit()
        calls.clear()
        ProposalService.apply_proposal(proposal.id, actor_id=seed['owner_a'])
        db.session.commit()
        assert row.id == original_id and row.due_at.date() == date(2026, 11, 12)
        assert completed.due_at == completed_date
        assert before_uids == {line for line in render_calendar(access).splitlines() if line.startswith('UID:')}
        assert len(calls) == 1
        calls.clear()
        ProposalService.apply_proposal(proposal.id, actor_id=seed['owner_a'])
        db.session.commit()
        assert calls == []
        row.source_data = {'removed_at': '2026-10-01'}
        db.session.flush()
        db.session.rollback()
        assert calls == []
        assert not row.is_removed


def test_accepted_amendment_updates_client_schedule(app, seed, owner_a_client):
    from services import amendment_service
    with app.app_context():
        tx, _, _ = _file(seed)
        contract = _contract(tx)
        row = contract.milestones.filter_by(milestone_key='closing_date').one()
        mid, tid = row.id, tx.id
        doc = TransactionDocument(organization_id=tx.organization_id, transaction_id=tx.id,
            template_slug='amendment', template_name='Amendment', status='signed',
            document_source='external', field_data={'document_classification': 'amendment', 'closing_date': '2026-11-15'})
        db.session.add(doc)
        db.session.flush()
        amendment = amendment_service.create_from_document(doc, actor_id=seed['owner_a'])
        db.session.commit()
        aid = amendment.id
    response = owner_a_client.post(f'/transactions/{tid}/amendments/{aid}/accept', json={'selected': {'closing_date': True}})
    assert response.status_code == 200
    with app.app_context():
        rows = transaction_date_rows(db.session, seed['org_a'], tid)
        assert next(r for r in rows if r['id'] == mid)['due_at'].date() == date(2026, 11, 15)
