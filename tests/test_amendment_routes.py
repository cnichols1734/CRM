"""HTTP routes for seller contract amendment review / accept / reject."""

from datetime import date

import pytest

from models import (
    AuditEvent,
    SellerAcceptedContract,
    SellerContractAmendment,
    SellerContractAmendmentVersion,
    TransactionAssignment,
    TransactionDocument,
    TransactionRequirement,
    db,
)
from services import amendment_service


def _create_primary_contract(seed, *, closing_date=None, sales_price=500000, **extra):
    contract = SellerAcceptedContract(
        organization_id=seed['org_a'],
        transaction_id=seed['tx_a'],
        created_by_id=seed['owner_a'],
        position='primary',
        status='active',
        accepted_price=sales_price,
        effective_date=date(2026, 8, 1),
        closing_date=closing_date or date(2026, 9, 15),
        option_period_days=7,
        financing_type='Conventional',
        frozen_terms={
            'sales_price': str(sales_price),
            'closing_date': (closing_date or date(2026, 9, 15)).isoformat(),
            'option_period_days': 7,
            'financing_type': 'Conventional',
            'option_fee': '250',
            'earnest_money': '5000',
        },
        **extra,
    )
    db.session.add(contract)
    db.session.flush()
    return contract


def _create_document(seed, field_data, *, transaction_id=None, organization_id=None):
    doc = TransactionDocument(
        organization_id=organization_id or seed['org_a'],
        transaction_id=transaction_id or seed['tx_a'],
        template_slug='amendment',
        template_name='Amendment',
        status='signed',
        document_source='external',
        field_data=field_data,
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def _cleanup_amendments(transaction_id, organization_id):
    amendment_ids = [
        row.id
        for row in SellerContractAmendment.query.filter_by(
            transaction_id=transaction_id,
            organization_id=organization_id,
        ).all()
    ]
    if amendment_ids:
        SellerContractAmendmentVersion.query.filter(
            SellerContractAmendmentVersion.amendment_id.in_(amendment_ids),
        ).delete(synchronize_session=False)
        SellerContractAmendment.query.filter(
            SellerContractAmendment.id.in_(amendment_ids),
        ).delete(synchronize_session=False)
    AuditEvent.query.filter(
        AuditEvent.transaction_id == transaction_id,
        AuditEvent.organization_id == organization_id,
        AuditEvent.event_type.in_((
            'amendment_created',
            'amendment_accepted',
            'amendment_rejected',
        )),
    ).delete(synchronize_session=False)
    SellerAcceptedContract.query.filter_by(
        transaction_id=transaction_id,
        organization_id=organization_id,
    ).delete(synchronize_session=False)
    TransactionRequirement.query.filter_by(
        transaction_id=transaction_id,
        organization_id=organization_id,
    ).delete(synchronize_session=False)
    TransactionDocument.query.filter_by(
        transaction_id=transaction_id,
        organization_id=organization_id,
        template_slug='amendment',
    ).delete(synchronize_session=False)
    db.session.commit()


def _clear_agent_assignments(org_id, tx_id, user_id):
    TransactionAssignment.query.filter_by(
        organization_id=org_id,
        transaction_id=tx_id,
        user_id=user_id,
    ).delete()
    db.session.commit()


def test_amendment_review_page_renders_changed_label(app, seed, owner_a_client):
    with app.app_context():
        try:
            _create_primary_contract(seed, closing_date=date(2026, 9, 15))
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'closing_date': '2026-10-01',
                'sales_price': '525000',
            })
            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            db.session.commit()
            amendment_id = amendment.id
            tx_id = seed['tx_a']
        except Exception:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])
            raise

    try:
        response = owner_a_client.get(
            f'/transactions/{tx_id}/amendments/{amendment_id}',
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Closing date' in html
        assert 'Proposed changes' in html
        assert 'AMENDMENT' in html.upper() or 'Amendment' in html
    finally:
        with app.app_context():
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_amendment_review_other_org_returns_404(app, seed, owner_a_client):
    with app.app_context():
        try:
            contract_b = SellerAcceptedContract(
                organization_id=seed['org_b'],
                transaction_id=seed['tx_b'],
                created_by_id=seed['owner_b'],
                position='primary',
                status='active',
                accepted_price=400000,
                closing_date=date(2026, 11, 1),
                frozen_terms={'closing_date': '2026-11-01'},
            )
            db.session.add(contract_b)
            db.session.flush()
            amendment_b = SellerContractAmendment(
                organization_id=seed['org_b'],
                transaction_id=seed['tx_b'],
                accepted_contract_id=contract_b.id,
                created_by_id=seed['owner_b'],
                status='received',
                amendment_type='amendment',
            )
            db.session.add(amendment_b)
            db.session.commit()
            amendment_id = amendment_b.id
            tx_b = seed['tx_b']
        except Exception:
            _cleanup_amendments(seed['tx_b'], seed['org_b'])
            raise

    try:
        response = owner_a_client.get(
            f'/transactions/{tx_b}/amendments/{amendment_id}',
        )
        assert response.status_code == 404
    finally:
        with app.app_context():
            _cleanup_amendments(seed['tx_b'], seed['org_b'])


def test_accept_selected_keys_via_route(app, seed, owner_a_client):
    with app.app_context():
        try:
            _create_primary_contract(
                seed,
                closing_date=date(2026, 9, 15),
                sales_price=500000,
            )
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'closing_date': '2026-10-01',
                'sales_price': '525000',
            })
            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            db.session.commit()
            amendment_id = amendment.id
            tx_id = seed['tx_a']
        except Exception:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])
            raise

    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/amendments/{amendment_id}/accept',
            json={'selected': {'closing_date': True, 'sales_price': False}},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['success'] is True
        assert payload['applied_keys'] == ['closing_date']

        with app.app_context():
            row = db.session.get(SellerContractAmendment, amendment_id)
            assert row.status == 'accepted'
    finally:
        with app.app_context():
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_accept_already_accepted_returns_400(app, seed, owner_a_client):
    with app.app_context():
        try:
            contract = _create_primary_contract(seed)
            amendment = SellerContractAmendment(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                accepted_contract_id=contract.id,
                created_by_id=seed['owner_a'],
                status='accepted',
                amendment_type='amendment',
            )
            db.session.add(amendment)
            db.session.commit()
            amendment_id = amendment.id
            tx_id = seed['tx_a']
        except Exception:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])
            raise

    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/amendments/{amendment_id}/accept',
            json={'selected': {'closing_date': True}},
        )
        assert response.status_code == 400
        payload = response.get_json()
        assert payload['success'] is False
        assert 'already accepted' in payload['error'].lower()
    finally:
        with app.app_context():
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_reject_via_route(app, seed, owner_a_client):
    with app.app_context():
        try:
            _create_primary_contract(seed)
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'closing_date': '2026-10-01',
            })
            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            db.session.commit()
            amendment_id = amendment.id
            tx_id = seed['tx_a']
        except Exception:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])
            raise

    try:
        response = owner_a_client.post(
            f'/transactions/{tx_id}/amendments/{amendment_id}/reject',
            json={'reason': 'Seller declined'},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['success'] is True

        with app.app_context():
            row = db.session.get(SellerContractAmendment, amendment_id)
            assert row.status == 'rejected'
    finally:
        with app.app_context():
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_collaborator_cannot_accept(app, seed, agent_a_client):
    with app.app_context():
        try:
            _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])
            _create_primary_contract(seed)
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'closing_date': '2026-10-01',
            })
            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            assignment = TransactionAssignment(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                user_id=seed['agent_a'],
                role='collaborator',
            )
            db.session.add(assignment)
            db.session.commit()
            amendment_id = amendment.id
            tx_id = seed['tx_a']
        except Exception:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])
            _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])
            raise

    try:
        response = agent_a_client.post(
            f'/transactions/{tx_id}/amendments/{amendment_id}/accept',
            json={'selected': {'closing_date': True}},
        )
        assert response.status_code != 200
        assert response.status_code in (403, 404)
    finally:
        with app.app_context():
            _cleanup_amendments(seed['tx_a'], seed['org_a'])
            _clear_agent_assignments(seed['org_a'], seed['tx_a'], seed['agent_a'])
