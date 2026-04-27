import io
from unittest.mock import patch


def test_offer_document_type_inference():
    from services.seller_workflow import infer_offer_document_type, infer_offer_document_type_from_text

    assert infer_offer_document_type('One to Four Family Residential Contract (Resale) - 1124.pdf') == 'buyer_offer'
    assert infer_offer_document_type('6004 Lakeside SD.pdf') == 'sellers_disclosure'
    assert infer_offer_document_type('Addendum for Property Subject to Mandatory Member. in Owners Assoc. #4.pdf') == 'hoa_addendum'
    assert infer_offer_document_type('Draughn PreQual.pdf') == 'pre_approval'
    assert infer_offer_document_type('Third Party Financing Addendum for Credit Approval.pdf') == 'third_party_financing'
    assert infer_offer_document_type_from_text(
        'ONE TO FOUR FAMILY RESIDENTIAL CONTRACT (RESALE) Third Party Financing Addendum '
        'ADDENDUM FOR PROPERTY SUBJECT TO MANDATORY MEMBERSHIP IN A PROPERTY OWNERS ASSOCIATION',
        filename='random upload.pdf',
        explicit_type='pre_approval',
    ) == 'offer_package'


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
                'cash_down_payment': '26000',
                'financing_amount': '234000',
                'seller_concessions_amount': '5000',
                'survey_choice': survey_choice,
                'survey_furnished_by': 'Seller existing survey',
                'residential_service_contract': 'Seller to reimburse buyer up to $650.',
                'buyer_agent_commission_percent': '3',
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
        json={'position': 'primary', 'effective_date': '2026-04-24'},
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
        assert contract.cash_down_payment == 26000
        assert contract.financing_amount == 234000
        assert contract.seller_concessions_amount == 5000
        assert contract.survey_furnished_by == 'Seller existing survey'
        assert contract.residential_service_contract == 'Seller to reimburse buyer up to $650.'
        assert contract.buyer_agent_commission_percent == 3
        assert 'third_party_financing' in contract.frozen_terms['supporting_documents']
        assert len(contract.extra_data['offer_package_documents']) == 3
        assert len(contract.extra_data['supporting_document_ids']) == 2


def test_primary_combined_package_extraction_populates_offer_terms(app, db, seed):
    from models import SellerOffer, SellerOfferDocument, SellerOfferVersion, TransactionDocument, User
    from services.seller_workflow import sync_offer_version_from_document

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            status='needs_review',
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
            status='submitted',
            terms_data={},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id

        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='seller-offer-contract',
            template_name='Offer Package',
            status='signed',
            extraction_status='complete',
            field_data={
                'detected_document_types': [
                    'residential_contract',
                    'third_party_financing_addendum',
                    'hoa_addendum',
                ],
                'buyer_names': ['Clark Draughn', 'Rachel Draughn'],
                'buyer_agent_name': 'Marilyn Kittrell',
                'offer_price': '260000',
                'cash_down_payment': '26000',
                'seller_concessions_amount': '3',
                'addenda': {
                    'third_party_financing_addendum': {
                        'financing_type': 'conventional',
                        'first_mortgage_amount': '234000',
                        'buyer_approval_required': True,
                        'buyer_approval_days': '12',
                    },
                    'hoa_addendum': {
                        'association_name': 'Travis Park Home Owners Association',
                        'title_company_info_payer': 'seller',
                    },
                },
            },
        )
        db.session.add(doc)
        db.session.flush()
        version.transaction_document_id = doc.id
        db.session.add(SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer.id,
            transaction_document_id=doc.id,
            offer_version_id=version.id,
            created_by_id=owner.id,
            document_type='offer_package',
            display_name='Offer Package',
            is_primary_terms_document=True,
        ))
        db.session.flush()

        sync_offer_version_from_document(doc.id)

        assert offer.buyer_names == 'Clark Draughn, Rachel Draughn'
        assert offer.buyer_agent_name == 'Marilyn Kittrell'
        assert offer.offer_price == 260000
        assert offer.cash_down_payment == 26000
        assert offer.financing_amount == 234000
        assert offer.financing_type == 'conventional'
        assert offer.financing_contingency is True
        assert offer.hoa_resale_certificate_payer == 'seller'
        assert offer.terms_summary['supporting_documents']['third_party_financing']['buyer_approval_days'] == '12'
        assert offer.terms_summary['supporting_documents']['hoa_addendum']['association_name'] == 'Travis Park Home Owners Association'


def test_accept_offer_waits_for_pending_extraction(owner_a_client, app, db, seed):
    from models import SellerOffer, SellerOfferDocument, SellerOfferVersion, TransactionDocument, User

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            buyer_names='Pending Buyer',
            status='needs_review',
            terms_summary={'offer_price': '250000'},
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
            status='submitted',
            terms_data={'offer_price': '250000'},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id

        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='seller-offer-contract',
            template_name='Offer Contract',
            status='signed',
            extraction_status='processing',
            field_data={},
        )
        db.session.add(doc)
        db.session.flush()
        db.session.add(SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer.id,
            transaction_document_id=doc.id,
            offer_version_id=version.id,
            created_by_id=owner.id,
            document_type='buyer_offer',
            display_name='Offer Contract',
            is_primary_terms_document=True,
        ))
        db.session.commit()
        offer_id = offer.id

    response = owner_a_client.post(
        f'/transactions/{seed["tx_a"]}/offers/{offer_id}/accept',
        json={'position': 'primary'},
    )

    assert response.status_code == 409
    assert response.get_json()['success'] is False


def test_contract_execution_date_update_recalculates_financing_deadline(owner_a_client, app, db, seed):
    from models import SellerAcceptedContract, SellerOffer, SellerOfferVersion, User

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            buyer_names='Execution Date Buyer',
            status='reviewing',
            terms_summary={
                'offer_price': '300000',
                'addenda': {
                    'third_party_financing_addendum': {
                        'buyer_approval_days': '12',
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
            terms_data={'offer_price': '300000'},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id
        db.session.commit()
        offer_id = offer.id

    accept_response = owner_a_client.post(
        f'/transactions/{seed["tx_a"]}/offers/{offer_id}/accept',
        json={'position': 'primary'},
    )
    assert accept_response.status_code == 200, accept_response.get_data(as_text=True)
    contract_id = accept_response.get_json()['accepted_contract_id']

    with app.app_context():
        contract = db.session.get(SellerAcceptedContract, contract_id)
        assert contract.effective_date is None
        assert contract.financing_approval_deadline is None

    update_response = owner_a_client.post(
        f'/transactions/{seed["tx_a"]}/seller/contracts/{contract_id}/details',
        json={'effective_date': '2026-04-24'},
    )
    assert update_response.status_code == 200, update_response.get_data(as_text=True)

    with app.app_context():
        contract = db.session.get(SellerAcceptedContract, contract_id)
        assert contract.effective_date.isoformat() == '2026-04-24'
        assert contract.financing_approval_deadline.isoformat() == '2026-05-06'


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


# ---------------------------------------------------------------------------
# PDF splitter helpers + AI packet split flow
# ---------------------------------------------------------------------------


def _build_test_pdf(page_count: int) -> bytes:
    import fitz

    doc = fitz.open()
    try:
        for index in range(page_count):
            doc.insert_page(index, text=f'Test page {index + 1}')
        return doc.tobytes()
    finally:
        doc.close()


def test_pdf_splitter_normalizes_and_slices_segments():
    from services.pdf_splitter import (
        SplitSegment,
        get_pdf_page_count,
        normalize_segments,
        split_pdf_by_segments,
    )

    pdf_bytes = _build_test_pdf(5)
    assert get_pdf_page_count(pdf_bytes) == 5

    raw_segments = [
        {'document_type': 'Buyer_Offer', 'start_page': 1, 'end_page': 3, 'title': 'TREC Contract'},
        {'document_type': 'third_party_financing', 'start_page': 4, 'end_page': 4},
        {'document_type': 'hoa_addendum', 'start_page': 5, 'end_page': 8},  # clamped to 5
        {'document_type': 'noise', 'start_page': None, 'end_page': 2},  # dropped
        {'document_type': 'Buyer_Offer', 'start_page': 1, 'end_page': 3},  # duplicate of first
    ]
    segments = normalize_segments(raw_segments, total_pages=5)
    assert [(s.document_type, s.start_page, s.end_page) for s in segments] == [
        ('buyer_offer', 1, 3),
        ('third_party_financing', 4, 4),
        ('hoa_addendum', 5, 5),
    ]

    results = split_pdf_by_segments(pdf_bytes, segments)
    assert len(results) == 3
    assert [r.page_count for r in results] == [3, 1, 1]
    assert all(r.pdf_bytes.startswith(b'%PDF') for r in results)


def test_split_offer_package_creates_children_with_inherited_data(app, db, seed):
    from models import (
        SellerOffer,
        SellerOfferDocument,
        SellerOfferVersion,
        TransactionDocument,
        User,
    )
    from services.seller_workflow import split_offer_package_into_children

    pdf_bytes = _build_test_pdf(6)

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            buyer_names='Split Packet Buyer',
            status='needs_review',
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
            status='submitted',
            terms_data={},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id

        parent_doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='seller-offer-contract',
            template_name='Offer Package',
            status='signed',
            signed_original_filename='combined_packet.pdf',
            signed_file_path='test/combined_packet.pdf',
            extraction_status='complete',
            field_data={
                'detected_documents': [
                    {'document_type': 'buyer_offer', 'start_page': 1, 'end_page': 4, 'title': 'Residential Contract'},
                    {'document_type': 'third_party_financing', 'start_page': 5, 'end_page': 5, 'title': 'TPF Addendum'},
                    {'document_type': 'hoa_addendum', 'start_page': 6, 'end_page': 6},
                ],
                'addenda': {
                    'third_party_financing_addendum': {
                        'financing_type': 'conventional',
                        'first_mortgage_amount': '320000',
                        'buyer_approval_days': '14',
                    },
                    'hoa_addendum': {
                        'association_name': 'Lakeside HOA',
                        'title_company_info_payer': 'seller',
                    },
                },
            },
        )
        db.session.add(parent_doc)
        db.session.flush()
        version.transaction_document_id = parent_doc.id

        parent_offer_doc = SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer.id,
            transaction_document_id=parent_doc.id,
            offer_version_id=version.id,
            created_by_id=owner.id,
            document_type='offer_package',
            display_name='Offer Package',
            is_primary_terms_document=True,
        )
        db.session.add(parent_offer_doc)
        db.session.commit()

        parent_doc_id = parent_doc.id
        offer_id = offer.id

    with app.app_context():
        with patch(
            'services.supabase_storage.upload_external_document',
            side_effect=lambda transaction_id, file_data, original_filename, content_type: {'path': f'test/{original_filename}'},
        ):
            children = split_offer_package_into_children(parent_doc_id, pdf_bytes)
        db.session.commit()

        assert len(children) == 3
        offer_documents = SellerOfferDocument.query.filter_by(offer_id=offer_id).order_by(SellerOfferDocument.id.asc()).all()
        types = [od.document_type for od in offer_documents]
        assert types == ['offer_package', 'buyer_offer', 'third_party_financing', 'hoa_addendum']

        buyer_offer_child = next(od for od in offer_documents if od.document_type == 'buyer_offer')
        assert buyer_offer_child.is_primary_terms_document is False
        assert buyer_offer_child.document.parent_document_id == parent_doc_id
        assert buyer_offer_child.document.page_start == 1
        assert buyer_offer_child.document.page_end == 4

        tpf = next(od for od in offer_documents if od.document_type == 'third_party_financing')
        tpf_doc = tpf.document
        assert tpf_doc.parent_document_id == parent_doc_id
        assert tpf_doc.split_source == 'ai_packet_split'
        assert tpf_doc.page_start == 5 and tpf_doc.page_end == 5
        assert tpf_doc.field_data.get('financing_type') == 'conventional'
        assert tpf_doc.field_data.get('buyer_approval_days') == '14'

        hoa = next(od for od in offer_documents if od.document_type == 'hoa_addendum')
        hoa_doc = hoa.document
        assert hoa_doc.parent_document_id == parent_doc_id
        assert hoa_doc.page_start == 6 and hoa_doc.page_end == 6
        assert hoa_doc.field_data.get('association_name') == 'Lakeside HOA'

        # Re-running the splitter should be idempotent (existing children block re-creation).
        with patch(
            'services.supabase_storage.upload_external_document',
            side_effect=lambda transaction_id, file_data, original_filename, content_type: {'path': f'test/{original_filename}'},
        ):
            second_run = split_offer_package_into_children(parent_doc_id, pdf_bytes)
        assert second_run == []


def test_split_offer_package_skips_when_only_one_segment(app, db, seed):
    from models import (
        SellerOffer,
        SellerOfferDocument,
        SellerOfferVersion,
        TransactionDocument,
        User,
    )
    from services.seller_workflow import split_offer_package_into_children

    pdf_bytes = _build_test_pdf(2)

    with app.app_context():
        owner = User.query.filter_by(username='owner_a').first()
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            created_by_id=owner.id,
            buyer_names='Single Document Buyer',
            status='needs_review',
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
            status='submitted',
            terms_data={},
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id

        parent_doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='seller-offer-contract',
            template_name='Offer Contract',
            status='signed',
            signed_original_filename='single.pdf',
            signed_file_path='test/single.pdf',
            extraction_status='complete',
            field_data={
                'detected_documents': [
                    {'document_type': 'buyer_offer', 'start_page': 1, 'end_page': 2},
                ],
            },
        )
        db.session.add(parent_doc)
        db.session.flush()
        version.transaction_document_id = parent_doc.id

        db.session.add(SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer.id,
            transaction_document_id=parent_doc.id,
            offer_version_id=version.id,
            created_by_id=owner.id,
            document_type='offer_package',
            display_name='Offer Package',
            is_primary_terms_document=True,
        ))
        db.session.commit()

        children = split_offer_package_into_children(parent_doc.id, pdf_bytes)
        assert children == []
        assert TransactionDocument.query.filter_by(parent_document_id=parent_doc.id).count() == 0


def test_order_offer_package_documents_groups_children_under_parent(app, db, seed):
    """The renderer helper should keep AI splits underneath their parent packet."""
    from models import SellerOfferDocument, TransactionDocument
    from routes.transactions.crud import _order_offer_package_documents

    with app.app_context():
        parent = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='seller-offer-contract',
            template_name='Offer Package',
            status='signed',
            signed_original_filename='packet.pdf',
            signed_file_path='test/packet.pdf',
        )
        db.session.add(parent)
        db.session.flush()

        sibling = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='pre-approval-or-proof-of-funds',
            template_name='Pre-Approval',
            status='signed',
            signed_original_filename='preapproval.pdf',
            signed_file_path='test/preapproval.pdf',
        )
        db.session.add(sibling)
        db.session.flush()

        child_a = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='hoa-addendum',
            template_name='HOA Addendum',
            status='signed',
            signed_original_filename='packet_p6_hoa.pdf',
            signed_file_path='test/packet_p6_hoa.pdf',
            parent_document_id=parent.id,
            page_start=6,
            page_end=6,
            split_source='ai_packet_split',
        )
        child_b = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            template_slug='third-party-financing-addendum',
            template_name='TPF Addendum',
            status='signed',
            signed_original_filename='packet_p5_tpf.pdf',
            signed_file_path='test/packet_p5_tpf.pdf',
            parent_document_id=parent.id,
            page_start=5,
            page_end=5,
            split_source='ai_packet_split',
        )
        db.session.add_all([child_a, child_b])
        db.session.flush()

        owner_id = 1  # User exists from seed
        offer_id = 1
        # Add all offer documents so the .document relationship can be resolved.
        parent_od = SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer_id,
            transaction_document_id=parent.id,
            created_by_id=owner_id,
            document_type='offer_package',
            display_name='Offer Package',
            is_primary_terms_document=True,
        )
        child_a_od = SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer_id,
            transaction_document_id=child_a.id,
            created_by_id=owner_id,
            document_type='hoa_addendum',
            display_name='HOA Addendum',
            is_primary_terms_document=False,
        )
        sibling_od = SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer_id,
            transaction_document_id=sibling.id,
            created_by_id=owner_id,
            document_type='pre_approval',
            display_name='Pre-Approval',
            is_primary_terms_document=False,
        )
        child_b_od = SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            offer_id=offer_id,
            transaction_document_id=child_b.id,
            created_by_id=owner_id,
            document_type='third_party_financing',
            display_name='TPF Addendum',
            is_primary_terms_document=False,
        )
        db.session.add_all([parent_od, child_a_od, sibling_od, child_b_od])
        db.session.flush()

        # Simulate the DB query ordering offer documents by created_at desc,
        # so the most-recently uploaded packet appears first in the input list.
        offer_documents = [parent_od, child_a_od, sibling_od, child_b_od]

        ordered = _order_offer_package_documents(offer_documents)
        ordered_doc_ids = [od.transaction_document_id for od in ordered]
        # Parent first (preserves input parent order), splits sorted by page right after,
        # then unrelated sibling parent keeps its relative position.
        assert ordered_doc_ids == [parent.id, child_b.id, child_a.id, sibling.id]
