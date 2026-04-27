from datetime import datetime
import io
from unittest.mock import patch


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


def test_upload_seller_contract_document(owner_a_client, app, db, seed):
    from models import SellerContractDocument, TransactionDocument

    contract_id = _create_contract(app, db, seed)

    def fake_upload(transaction_id, file_data, original_filename, content_type):
        return {'path': f'test/{transaction_id}/{original_filename}'}

    with patch('services.supabase_storage.upload_external_document', side_effect=fake_upload), \
         patch('routes.transactions.seller_contracts.post_upload_processing') as post_upload_processing:
        response = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/seller/contracts/{contract_id}/documents/upload',
            data={
                'files': [
                    (io.BytesIO(b'%PDF-1.4 executed contract'), 'executed_contract.pdf'),
                ],
                'document_type': 'final_acceptance',
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 201, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['accepted_contract_id'] == contract_id
    assert payload['documents'][0]['document_type'] == 'final_acceptance'
    assert post_upload_processing.call_count == 1

    list_response = owner_a_client.get(
        f'/transactions/{seed["tx_a"]}/seller/contracts/{contract_id}/documents'
    )
    assert list_response.status_code == 200
    assert len(list_response.get_json()['documents']) == 1

    with app.app_context():
        contract_document = SellerContractDocument.query.filter_by(
            accepted_contract_id=contract_id,
        ).one()
        doc = db.session.get(TransactionDocument, contract_document.transaction_document_id)
        assert contract_document.display_name == 'Executed Contract'
        assert contract_document.is_primary_contract_document is True
        assert doc.template_slug == 'seller-accepted-contract'
        assert doc.document_source == 'completed'
        assert doc.extraction_status == 'pending'


def test_contract_document_extraction_updates_contract_terms(app, db, seed):
    from models import SellerAcceptedContract, SellerContractDocument, TransactionDocument, User
    from services.seller_workflow import sync_contract_from_document

    contract_id = _create_contract(app, db, seed)

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='seller-accepted-contract',
            template_name='Executed Contract',
            status='signed',
            field_data={
                'offer_price': '315000',
                'effective_date': '2026-04-24',
                'proposed_close_date': '2026-05-30',
                'option_period_days': '7',
                'financing_type': 'conventional',
                'seller_concessions_amount': '4500',
                'addenda': {
                    'third_party_financing_addendum': {
                        'buyer_approval_days': '15',
                    },
                },
            },
        )
        db.session.add(doc)
        db.session.flush()
        db.session.add(SellerContractDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            accepted_contract_id=contract_id,
            transaction_document_id=doc.id,
            created_by_id=owner.id,
            document_type='final_acceptance',
            display_name='Executed Contract',
            is_primary_contract_document=True,
        ))
        db.session.flush()

        sync_contract_from_document(doc.id)
        db.session.commit()

        contract = db.session.get(SellerAcceptedContract, contract_id)
        contract_document = SellerContractDocument.query.filter_by(
            accepted_contract_id=contract_id,
            transaction_document_id=doc.id,
        ).one()
        assert contract.accepted_price == 315000
        assert contract.effective_date.isoformat() == '2026-04-24'
        assert contract.closing_date.isoformat() == '2026-05-30'
        assert contract.financing_approval_deadline.isoformat() == '2026-05-09'
        assert contract.seller_concessions_amount == 4500
        assert contract_document.extraction_summary['offer_price'] == '315000'
