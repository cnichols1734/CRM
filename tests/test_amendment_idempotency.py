"""Amendment create_from_document is idempotent across extraction retries."""

from datetime import date, datetime
from decimal import Decimal

from models import (
    SellerAcceptedContract,
    SellerOffer,
    SellerOfferVersion,
    SellerContractAmendment,
    SellerContractAmendmentVersion,
    Transaction,
    TransactionDocument,
    TransactionType,
    db,
)
from services import amendment_service


def _tx_with_primary_contract(org_id, user_id):
    tx_type = TransactionType.query.filter_by(
        organization_id=org_id, name='seller',
    ).first()
    if tx_type is None:
        tx_type = TransactionType(
            organization_id=org_id, name='seller', display_name='Seller',
        )
        db.session.add(tx_type)
        db.session.flush()
    tx = Transaction(
        organization_id=org_id,
        created_by_id=user_id,
        transaction_type_id=tx_type.id,
        street_address='900 Amendment Loop',
        city='Austin',
        state='TX',
        status='under_contract',
    )
    db.session.add(tx)
    db.session.flush()
    offer = SellerOffer(
        organization_id=org_id,
        transaction_id=tx.id,
        created_by_id=user_id,
        status='accepted_primary',
        received_at=datetime.utcnow(),
        creation_source='test',
    )
    db.session.add(offer)
    db.session.flush()
    version = SellerOfferVersion(
        organization_id=org_id,
        transaction_id=tx.id,
        offer_id=offer.id,
        created_by_id=user_id,
        version_number=1,
        direction='buyer_offer',
        status='submitted',
        submitted_at=datetime.utcnow(),
        terms_data={},
    )
    db.session.add(version)
    db.session.flush()
    contract = SellerAcceptedContract(
        organization_id=org_id,
        transaction_id=tx.id,
        offer_id=offer.id,
        accepted_version_id=version.id,
        created_by_id=user_id,
        position='primary',
        status='active',
        accepted_price=Decimal('400000'),
        effective_date=date(2026, 7, 1),
        closing_date=date(2026, 8, 15),
        frozen_terms={'closing_date': '2026-08-15'},
    )
    db.session.add(contract)
    db.session.flush()
    return tx, contract


def test_create_from_document_idempotent_on_retry(app, seed):
    with app.app_context():
        tx, _contract = _tx_with_primary_contract(seed['org_a'], seed['owner_a'])
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='amendment',
            template_name='Amendment',
            status='signed',
            document_source='completed',
            field_data={
                'document_classification': 'amendment',
                'new_closing_date': '2026-09-01',
                'document_summary': 'Extends closing',
            },
            extraction_status='complete',
        )
        db.session.add(doc)
        db.session.flush()

        first = amendment_service.create_from_document(doc, actor_id=seed['owner_a'])
        second = amendment_service.create_from_document(doc, actor_id=seed['owner_a'])
        assert first is not None
        assert second is not None
        assert first.id == second.id
        assert SellerContractAmendment.query.filter_by(
            transaction_id=tx.id,
            organization_id=seed['org_a'],
        ).count() == 1
        assert SellerContractAmendmentVersion.query.filter_by(
            transaction_document_id=doc.id,
        ).count() == 1
        db.session.rollback()
