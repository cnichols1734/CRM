"""Amendment actor safety + evidence attach after high-confidence retag."""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from models import (
    SellerAcceptedContract,
    SellerOffer,
    SellerOfferVersion,
    SellerContractAmendment,
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    TransactionType,
    db,
)
from services import amendment_service
from services.document_extractor import extract_document_data
from services.requirement_evidence import auto_attach_for_document


def _seller_tx_with_primary(org_id, user_id):
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
        street_address='910 Actor Lane',
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
        frozen_terms={},
    )
    db.session.add(contract)
    db.session.flush()
    return tx


def test_create_from_document_rejects_invalid_actor_id(app, seed):
    with app.app_context():
        tx = _seller_tx_with_primary(seed['org_a'], seed['owner_a'])
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='amendment',
            template_name='Amendment',
            status='signed',
            document_source='completed',
            sent_by_id=None,
            field_data={
                'document_classification': 'amendment',
                'new_closing_date': '2026-09-01',
            },
            extraction_status='complete',
        )
        db.session.add(doc)
        db.session.flush()

        assert amendment_service.create_from_document(doc, actor_id=0) is None
        assert amendment_service.create_from_document(doc, actor_id=-1) is None
        assert SellerContractAmendment.query.filter_by(transaction_id=tx.id).count() == 0

        created = amendment_service.create_from_document(
            doc, actor_id=seed['owner_a'],
        )
        assert created is not None
        assert created.created_by_id == seed['owner_a']
        db.session.rollback()


def test_extract_uses_transaction_owner_when_sent_by_missing(app, seed):
    with app.app_context():
        tx = _seller_tx_with_primary(seed['org_a'], seed['owner_a'])
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='amendment',
            template_name='Amendment',
            status='signed',
            document_source='completed',
            sent_by_id=None,
            signed_file_path='test/amendment.pdf',
            field_data={},
            extraction_status='pending',
        )
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id

        from services.document_identity import DocumentIdentity

        fake_identity = DocumentIdentity(
            kind='amendment',
            template_slug='amendment',
            confidence=0.95,
            label='Amendment',
            possible_scopes=('amendment', 'contract'),
        )

        with patch(
            'services.document_identity.resolve_upload_identity_for_extraction',
            return_value=('amendment', fake_identity, False),
        ), patch(
            'services.document_extractor._render_pdf_to_images',
            return_value=['img'],
        ), patch(
            'services.document_extractor._extract_pdf_text',
            return_value='AMENDMENT TO CONTRACT TREC NO. 39-9',
        ), patch(
            'services.ai_service.generate_document_extraction',
            return_value={
                'document_classification': 'amendment',
                'new_closing_date': '2026-09-01',
                'document_summary': 'Extends closing',
            },
        ), patch(
            'services.document_extractor._create_extraction_run',
        ) as run_mock, patch(
            'services.document_review.finalize_document_review',
            return_value=None,
        ), patch(
            'services.seller_workflow.split_offer_package_into_children',
            return_value=[],
        ), patch(
            'services.seller_workflow.split_contract_package_into_children',
            return_value=[],
        ):
            run_mock.return_value = type('R', (), {'id': 1})()
            extract_document_data(doc_id, seed['org_a'], b'%PDF-1.4 amendment')

        amendments = SellerContractAmendment.query.filter_by(
            transaction_id=tx.id,
            organization_id=seed['org_a'],
        ).all()
        assert len(amendments) == 1
        assert amendments[0].created_by_id == seed['owner_a']
        db.session.rollback()


def test_retag_triggers_evidence_attach_without_completing(app, seed):
    with app.app_context():
        tx_type = TransactionType.query.filter_by(
            organization_id=seed['org_a'], name='seller',
        ).first()
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=tx_type.id,
            street_address='920 Retag Lane',
            city='Austin',
            state='TX',
            status='preparing_to_list',
        )
        db.session.add(tx)
        db.session.flush()
        req = TransactionRequirement(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            package_key='listing',
            phase_key='prep',
            requirement_key='listing_agreement',
            title='Listing Agreement Signed',
            work_status='pending',
            source='deadline_pack',
        )
        db.session.add(req)
        db.session.flush()

        # Simulate retagged document
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            document_source='completed',
            signed_file_path='test/listing.pdf',
            field_data={},
            extraction_status='complete',
        )
        db.session.add(doc)
        db.session.flush()

        with patch(
            'services.deadline_rules.DeadlineRulesService.expected_document_slug',
            return_value='listing-agreement',
        ), patch(
            'services.requirement_evidence._load_pack_for_requirement',
            return_value={'requirements': {'listing_agreement': {}}},
        ):
            touched = auto_attach_for_document(doc, actor_id=seed['owner_a'])

        assert touched
        db.session.refresh(req)
        assert req.work_status == 'in_progress'
        assert req.work_status != 'completed'
        # Idempotent second attach
        touched2 = auto_attach_for_document(doc, actor_id=seed['owner_a'])
        db.session.refresh(req)
        assert req.work_status == 'in_progress'
        db.session.rollback()
