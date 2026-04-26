from datetime import datetime


def _create_contract(app, db, seed):
    from models import SellerAcceptedContract, User

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        contract = SellerAcceptedContract(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            position='primary',
            status='active',
            accepted_price=250000,
        )
        db.session.add(contract)
        db.session.commit()
        return contract.id


def test_update_seller_contract_milestone(owner_a_client, app, db, seed):
    from models import SellerContractMilestone

    contract_id = _create_contract(app, db, seed)
    with app.app_context():
        milestone = SellerContractMilestone(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            accepted_contract_id=contract_id,
            milestone_key='survey_due',
            title='Survey due',
            due_at=datetime(2026, 5, 1, 9, 0),
            status='not_started',
            source='calculated',
        )
        db.session.add(milestone)
        db.session.commit()
        milestone_id = milestone.id

    response = owner_a_client.post(
        f'/transactions/{seed["tx_a"]}/seller/contracts/{contract_id}/milestones/{milestone_id}',
        json={
            'title': 'Survey delivered to title',
            'due_at': '2026-05-03T13:30',
            'status': 'completed',
            'notes': 'Confirmed by title.',
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True

    with app.app_context():
        milestone = db.session.get(SellerContractMilestone, milestone_id)
        assert milestone.title == 'Survey delivered to title'
        assert milestone.due_at.isoformat() == '2026-05-03T13:30:00'
        assert milestone.status == 'completed'
        assert milestone.completed_at is not None
        assert milestone.source == 'manual'
        assert milestone.notes == 'Confirmed by title.'


def test_create_manual_seller_contract_milestone(owner_a_client, app, db, seed):
    from models import SellerContractMilestone

    contract_id = _create_contract(app, db, seed)
    response = owner_a_client.post(
        f'/transactions/{seed["tx_a"]}/seller/contracts/{contract_id}/milestones',
        json={
            'title': 'Order HOA resale certificate',
            'due_at': '2026-05-04T10:00',
            'status': 'waiting',
            'notes': 'Waiting on management company.',
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload['success'] is True

    with app.app_context():
        milestone = db.session.get(SellerContractMilestone, payload['milestone']['id'])
        assert milestone.milestone_key == 'manual'
        assert milestone.title == 'Order HOA resale certificate'
        assert milestone.due_at.isoformat() == '2026-05-04T10:00:00'
        assert milestone.status == 'waiting'
        assert milestone.source == 'manual'
