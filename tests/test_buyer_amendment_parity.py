"""Buyer controlling-contract amendment create + accept parity."""

from datetime import date

from models import (
    SellerAcceptedContract,
    SellerContractAmendment,
    SellerContractAmendmentVersion,
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    TransactionType,
    db,
)
from services import amendment_service
from services.amendment_service import (
    amendment_direction_label,
    opening_amendment_direction_for_side,
)
from services.controlling_contracts import create_baseline_from_document
from services.document_routing import (
    ACTION_CREATE_AMENDMENT,
    TransactionContext,
    decide_route,
)
from services.document_identity import KIND_AMENDMENT, DocumentIdentity, HIGH_CONFIDENCE


def _buyer_tx_with_baseline(seed):
    tx_type = TransactionType.query.filter_by(
        organization_id=seed['org_a'], name='buyer',
    ).first()
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=tx_type.id,
        street_address='900 Amend Buyer',
        status='under_contract',
        expected_close_date=date(2026, 9, 15),
    )
    db.session.add(tx)
    db.session.flush()
    doc = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug='one-to-four-family-contract',
        template_name='Purchase Contract',
        status='signed',
        document_source='completed',
        field_data={},
    )
    db.session.add(doc)
    db.session.flush()
    contract = create_baseline_from_document(
        transaction=tx,
        document=doc,
        approved_terms={
            'sales_price': '500000',
            'effective_date': '2026-08-01',
            'closing_date': '2026-09-15',
            'option_period_days': 7,
        },
        actor_id=seed['owner_a'],
    )
    return tx, contract


def test_opening_amendment_direction_is_side_aware():
    assert opening_amendment_direction_for_side('seller') == 'buyer_amendment'
    assert opening_amendment_direction_for_side('buyer') == 'seller_amendment'
    assert amendment_direction_label('seller_amendment') == 'Seller amendment'
    assert amendment_direction_label('buyer_amendment') == 'Buyer amendment'


def test_routing_buyer_amendment_creates_amendment_review(app, seed):
    with app.app_context():
        identity = DocumentIdentity(
            kind=KIND_AMENDMENT,
            template_slug='amendment',
            confidence=HIGH_CONFIDENCE,
            possible_scopes=('amendment', 'contract'),
        )
        decision = decide_route(
            identity=identity,
            representation_side='buyer',
            side_confirmed=True,
            transaction=TransactionContext(
                transaction_id=1,
                side='buyer',
                status='under_contract',
                has_primary_contract=True,
            ),
        )
        assert decision.action == ACTION_CREATE_AMENDMENT
        db.session.rollback()


def test_create_and_accept_buyer_amendment_recomputes_closing(app, seed):
    with app.app_context():
        tx, contract = _buyer_tx_with_baseline(seed)
        amend_doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='amendment',
            template_name='Amendment',
            status='signed',
            document_source='completed',
            field_data={
                'document_classification': 'amendment',
                'document_summary': 'Extend closing',
                'new_closing_date': '2026-10-20',
                'closing_date': '2026-10-20',
            },
        )
        db.session.add(amend_doc)
        db.session.flush()

        amendment = amendment_service.create_from_document(
            amend_doc, actor_id=seed['owner_a'],
        )
        assert amendment is not None
        assert amendment.accepted_contract_id == contract.id
        version = amendment_service.current_version(amendment)
        assert version is not None
        assert version.direction == 'seller_amendment'
        assert version.direction == opening_amendment_direction_for_side('buyer')
        assert (amendment.extra_data or {}).get('representation_side') == 'buyer'

        result = amendment_service.accept(
            amendment,
            actor_id=seed['owner_a'],
            selected_keys=['closing_date'],
        )
        assert 'closing_date' in result['applied_keys']
        db.session.refresh(tx)
        db.session.refresh(contract)
        assert tx.expected_close_date == date(2026, 10, 20)
        assert contract.closing_date == date(2026, 10, 20)

        # buyer_ctc requirements should still exist / be recomputed (not wiped).
        assert TransactionRequirement.query.filter_by(
            transaction_id=tx.id, package_key='buyer_ctc',
        ).count() >= 1
        db.session.rollback()


def test_buyer_amendment_accept_route(app, seed, owner_a_client):
    with app.app_context():
        tx, contract = _buyer_tx_with_baseline(seed)
        amend_doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='amendment',
            template_name='Amendment',
            status='signed',
            document_source='completed',
            field_data={
                'new_closing_date': '2026-11-01',
                'closing_date': '2026-11-01',
            },
        )
        db.session.add(amend_doc)
        db.session.flush()
        amendment = amendment_service.create_from_document(
            amend_doc, actor_id=seed['owner_a'],
        )
        db.session.commit()
        amendment_id = amendment.id
        tx_id = tx.id

    resp = owner_a_client.post(
        f'/transactions/{tx_id}/amendments/{amendment_id}/accept',
        json={'selected_keys': ['closing_date']},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True

    with app.app_context():
        tx = Transaction.query.get(tx_id)
        contract = SellerAcceptedContract.query.filter_by(
            transaction_id=tx_id, position='primary', status='active',
        ).one()
        assert tx.expected_close_date == date(2026, 11, 1)
        assert contract.closing_date == date(2026, 11, 1)
        db.session.rollback()
