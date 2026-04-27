import io
from unittest.mock import patch


def test_offer_document_type_inference():
    from services.seller_workflow import infer_offer_document_type

    assert infer_offer_document_type('One to Four Family Residential Contract (Resale) - 1124.pdf') == 'buyer_offer'
    assert infer_offer_document_type('6004 Lakeside SD.pdf') == 'sellers_disclosure'
    assert infer_offer_document_type('Addendum for Property Subject to Mandatory Member. in Owners Assoc. #4.pdf') == 'hoa_addendum'
    assert infer_offer_document_type('Draughn PreQual.pdf') == 'pre_approval'
    assert infer_offer_document_type('Third Party Financing Addendum for Credit Approval.pdf') == 'third_party_financing'


def test_offer_extraction_schemas_registered():
    from services.document_extractor import EXTRACTION_SCHEMAS

    for slug in (
        'seller-offer-contract',
        'sellers-disclosure',
        'hoa-addendum',
        'pre-approval-or-proof-of-funds',
        'third-party-financing-addendum',
    ):
        assert slug in EXTRACTION_SCHEMAS
        assert EXTRACTION_SCHEMAS[slug]['fields']


def test_supporting_document_merge_preserves_primary_terms(app, db, seed):
    from models import SellerOffer, SellerOfferDocument, SellerOfferVersion, TransactionDocument, User
    from services.seller_workflow import merge_offer_supporting_document

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            buyer_names='Existing Buyer',
            status='reviewing',
            terms_summary={'offer_price': '260000'},
        )
        db.session.add(offer)
        db.session.flush()

        version = SellerOfferVersion(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer.id,
            created_by_id=owner.id,
            version_number=1,
            direction='buyer_offer',
            status='reviewed',
            terms_data={'offer_price': '260000'},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id

        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='third-party-financing-addendum',
            template_name='Third Party Financing Addendum',
            status='signed',
            field_data={
                'financing_type': 'conventional',
                'first_mortgage_amount': '234000',
                'buyer_approval_required': True,
                'buyer_approval_days': '15',
            },
        )
        db.session.add(doc)
        db.session.flush()

        offer_doc = SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer.id,
            transaction_document_id=doc.id,
            created_by_id=owner.id,
            document_type='third_party_financing',
            display_name='Third Party Financing Addendum',
            is_primary_terms_document=False,
        )
        db.session.add(offer_doc)
        db.session.flush()

        merge_offer_supporting_document(offer_doc)

        assert offer.terms_summary['offer_price'] == '260000'
        assert offer.terms_summary['financing_type'] == 'conventional'
        assert offer.terms_summary['financing_contingency'] is True
        assert offer.terms_summary['addenda']['third_party_financing_addendum']['buyer_approval_days'] == '15'
        assert version.terms_data['supporting_documents']['third_party_financing']['first_mortgage_amount'] == '234000'


def test_offer_upload_accepts_multiple_documents(owner_a_client, app, db, seed):
    from models import SellerOffer, SellerOfferDocument

    def fake_upload(transaction_id, file_data, original_filename, content_type):
        return {'path': f'test/{transaction_id}/{original_filename}'}

    data = {
        'buyer_names': 'Upload Buyer',
        'buyer_agent_name': 'Upload Agent',
        'files': [
            (io.BytesIO(b'%PDF-1.4 contract'), 'One to Four Family Residential Contract (Resale) - 1124.pdf'),
            (io.BytesIO(b'%PDF-1.4 prequal'), 'Draughn PreQual.pdf'),
        ],
    }

    with patch('services.supabase_storage.upload_external_document', side_effect=fake_upload), \
         patch('routes.transactions.offers.post_upload_processing') as post_upload_processing:
        response = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/offers/upload',
            data=data,
            content_type='multipart/form-data',
        )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['offer_id']
    assert [doc['document_type'] for doc in payload['documents']] == ['buyer_offer', 'pre_approval']
    assert post_upload_processing.call_count == 2

    offers_response = owner_a_client.get(f'/transactions/{seed["tx_a"]}/offers')
    assert offers_response.status_code == 200
    offer_payload = next(
        offer for offer in offers_response.get_json()['offers']
        if offer['id'] == payload['offer_id']
    )
    assert offer_payload['document_count'] == 2
    assert offer_payload['version_count'] == 1
    assert offer_payload['extraction_status'] == 'pending'

    with app.app_context():
        offer = db.session.get(SellerOffer, payload['offer_id'])
        assert offer.buyer_names == 'Upload Buyer'
        assert offer.buyer_agent_name == 'Upload Agent'
        assert offer.status == 'needs_review'

        offer_docs = SellerOfferDocument.query.filter_by(offer_id=payload['offer_id']).all()
        assert len(offer_docs) == 2
        assert {doc.document_type for doc in offer_docs} == {'buyer_offer', 'pre_approval'}


def test_offer_upload_marks_existing_new_offer_needs_review(owner_a_client, app, db, seed):
    from models import SellerOffer, SellerOfferDocument, User

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            buyer_names='Existing Upload Buyer',
            status='new',
        )
        db.session.add(offer)
        db.session.commit()
        offer_id = offer.id

    def fake_upload(transaction_id, file_data, original_filename, content_type):
        return {'path': f'test/{transaction_id}/{original_filename}'}

    data = {
        'offer_id': str(offer_id),
        'files': [
            (io.BytesIO(b'%PDF-1.4 contract'), 'One to Four Family Residential Contract (Resale) - 1124.pdf'),
        ],
    }

    with patch('services.supabase_storage.upload_external_document', side_effect=fake_upload), \
         patch('routes.transactions.offers.post_upload_processing') as post_upload_processing:
        response = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/offers/upload',
            data=data,
            content_type='multipart/form-data',
        )

    assert response.status_code == 201
    assert post_upload_processing.call_count == 1

    with app.app_context():
        offer = db.session.get(SellerOffer, offer_id)
        assert offer.status == 'needs_review'
        assert SellerOfferDocument.query.filter_by(offer_id=offer_id).count() == 1


def test_accept_offer_freezes_package_documents_and_supporting_data(owner_a_client, app, db, seed):
    from models import (
        SellerAcceptedContract,
        SellerOffer,
        SellerOfferDocument,
        SellerOfferVersion,
        TransactionDocument,
        User,
    )

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        survey_choice = (
            "Seller's existing survey with affidavit or declaration; "
            "Buyer to obtain new survey at Seller's expense if needed."
        )
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            buyer_names='Clark Draughn',
            status='reviewing',
            terms_summary={
                'offer_price': '260000',
                'survey_choice': survey_choice,
                'supporting_documents': {
                    'third_party_financing': {
                        'financing_type': 'conventional',
                        'buyer_approval_days': '15',
                    },
                    'sellers_disclosure': {
                        'buyer_received_date': '2026-04-23',
                        'built_before_1978': True,
                    },
                },
                'addenda': {
                    'third_party_financing_addendum': {
                        'financing_type': 'conventional',
                        'buyer_approval_days': '15',
                    },
                },
            },
        )
        db.session.add(offer)
        db.session.flush()

        version = SellerOfferVersion(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer.id,
            created_by_id=owner.id,
            version_number=1,
            direction='buyer_offer',
            status='reviewed',
            terms_data={'offer_price': '260000', 'effective_date': '2026-04-24'},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id

        docs = [
            ('seller-offer-contract', 'Offer Contract', 'buyer_offer', True),
            ('third-party-financing-addendum', 'Third Party Financing Addendum', 'third_party_financing', False),
            ('sellers-disclosure', "Seller's Disclosure Notice", 'sellers_disclosure', False),
        ]
        for slug, name, document_type, primary in docs:
            doc = TransactionDocument(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                template_slug=slug,
                template_name=name,
                status='signed',
                signed_original_filename=f'{name}.pdf',
                signed_file_path=f'test/{slug}.pdf',
                extraction_status='complete',
                field_data={'sample': 'value'},
            )
            db.session.add(doc)
            db.session.flush()
            db.session.add(SellerOfferDocument(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                offer_id=offer.id,
                transaction_document_id=doc.id,
                offer_version_id=version.id if primary else None,
                created_by_id=owner.id,
                document_type=document_type,
                display_name=name,
                is_primary_terms_document=primary,
                extraction_summary={'sample': 'value'},
            ))
        db.session.commit()
        offer_id = offer.id

    response = owner_a_client.post(
        f'/transactions/{seed["tx_a"]}/offers/{offer_id}/accept',
        json={'position': 'primary'},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload['success'] is True

    with app.app_context():
        contract = db.session.get(SellerAcceptedContract, payload['accepted_contract_id'])
        assert contract.accepted_price == 260000
        assert contract.seller_disclosure_required is True
        assert contract.lead_based_paint_required is True
        assert contract.seller_disclosure_delivered_at.date().isoformat() == '2026-04-23'
        assert contract.financing_approval_deadline.isoformat() == '2026-05-09'
        assert contract.survey_choice == survey_choice
        assert 'third_party_financing' in contract.frozen_terms['supporting_documents']
        assert len(contract.extra_data['offer_package_documents']) == 3
        assert len(contract.extra_data['supporting_document_ids']) == 2


def test_accept_offer_handles_free_form_addendum_text(owner_a_client, app, db, seed):
    from models import SellerAcceptedContract, SellerOffer, SellerOfferVersion, User

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            buyer_names='Free Form Buyer',
            status='reviewing',
            terms_summary={
                'offer_price': '275000',
                'effective_date': '2026-04-24',
                'addenda': {
                    'third_party_financing_addendum': 'Third party financing addendum attached.',
                },
            },
        )
        db.session.add(offer)
        db.session.flush()

        version = SellerOfferVersion(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer.id,
            created_by_id=owner.id,
            version_number=1,
            direction='buyer_offer',
            status='reviewed',
            terms_data={'offer_price': '275000'},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id
        db.session.commit()
        offer_id = offer.id

    response = owner_a_client.post(
        f'/transactions/{seed["tx_a"]}/offers/{offer_id}/accept',
        json={'position': 'primary'},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()

    with app.app_context():
        contract = db.session.get(SellerAcceptedContract, payload['accepted_contract_id'])
        assert contract.accepted_price == 275000
        assert contract.frozen_terms['addenda']['third_party_financing_addendum'] == 'Third party financing addendum attached.'
