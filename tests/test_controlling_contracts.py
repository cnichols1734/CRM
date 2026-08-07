"""Side-neutral controlling contract baseline service."""

from datetime import date

from models import (
    SellerAcceptedContract,
    SellerContractDocument,
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    TransactionType,
    db,
)
from services.controlling_contracts import (
    create_baseline_from_document,
    get_active_primary_contract,
    has_active_primary_contract,
)


def _buyer_tx(seed, user_id, address='100 Buyer Ctrl'):
    tx_type = TransactionType.query.filter_by(
        organization_id=seed['org_a'], name='buyer',
    ).first()
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=user_id,
        transaction_type_id=tx_type.id,
        street_address=address,
        status='showing',
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _doc(seed, tx, slug='one-to-four-family-contract'):
    doc = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug=slug,
        template_name='Purchase Contract',
        status='signed',
        document_source='completed',
        field_data={},
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def test_create_baseline_sets_under_contract_and_seeds_buyer_ctc(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, seed['owner_a'])
        doc = _doc(seed, tx)
        contract = create_baseline_from_document(
            transaction=tx,
            document=doc,
            approved_terms={
                'sales_price': '450000',
                'effective_date': '2026-08-01',
                'closing_date': '2026-09-15',
                'option_period_days': 7,
            },
            actor_id=seed['owner_a'],
        )
        db.session.flush()
        db.session.refresh(tx)

        assert contract.position == 'primary'
        assert contract.status == 'active'
        assert contract.offer_id is None
        assert tx.status == 'under_contract'
        assert tx.expected_close_date == date(2026, 9, 15)
        assert SellerContractDocument.query.filter_by(
            accepted_contract_id=contract.id,
            transaction_document_id=doc.id,
            is_primary_contract_document=True,
        ).count() == 1
        reqs = TransactionRequirement.query.filter_by(
            transaction_id=tx.id, package_key='buyer_ctc',
        ).all()
        assert reqs
        assert has_active_primary_contract(tx.id, seed['org_a'])
        assert get_active_primary_contract(tx.id, seed['org_a']).id == contract.id
        db.session.rollback()


def test_create_baseline_idempotent_on_document_retry(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, seed['owner_a'], address='101 Idempotent')
        doc = _doc(seed, tx)
        first = create_baseline_from_document(
            transaction=tx,
            document=doc,
            approved_terms={'closing_date': '2026-10-01', 'effective_date': '2026-08-10'},
            actor_id=seed['owner_a'],
        )
        second = create_baseline_from_document(
            transaction=tx,
            document=doc,
            approved_terms={'closing_date': '2026-10-15', 'effective_date': '2026-08-10'},
            actor_id=seed['owner_a'],
        )
        assert first.id == second.id
        assert SellerAcceptedContract.query.filter_by(transaction_id=tx.id).count() == 1
        assert SellerContractDocument.query.filter_by(
            transaction_document_id=doc.id,
        ).count() == 1
        db.session.rollback()


def test_create_baseline_does_not_apply_when_terms_empty_on_existing(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, seed['owner_a'], address='102 Empty')
        doc = _doc(seed, tx)
        contract = create_baseline_from_document(
            transaction=tx,
            document=doc,
            approved_terms={'closing_date': '2026-11-01'},
            actor_id=seed['owner_a'],
        )
        # Retry with empty approved terms must not wipe closing.
        create_baseline_from_document(
            transaction=tx,
            document=doc,
            approved_terms={},
            actor_id=seed['owner_a'],
        )
        db.session.refresh(contract)
        assert contract.closing_date == date(2026, 11, 1)
        db.session.rollback()
