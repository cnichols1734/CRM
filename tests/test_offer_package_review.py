"""One-page offer package review: upload URL, confirm, quiet filing."""

from decimal import Decimal

from models import (
    DocumentReviewReport,
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    Transaction,
    TransactionDocument,
    db,
)
from services.document_package_workspace import build_document_packages
from services.offer_package_review import (
    build_offer_package_review,
    confirm_offer_package,
    offer_package_review_url,
)


def _seller_tx(seed):
    return Transaction.query.get(seed['tx_a'])


def _make_offer_with_docs(seed, tx, *, buyer_names='Jeffrey Rushing, Amy Rushing'):
    offer = SellerOffer(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        created_by_id=seed['owner_a'],
        status='needs_review',
        buyer_names=buyer_names,
        offer_price=Decimal('440000.00'),
        earnest_money=Decimal('4400.00'),
        creation_source='test',
    )
    db.session.add(offer)
    db.session.flush()

    contract = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug='seller-offer-contract',
        template_name='Purchase Contract',
        status='signed',
        document_source='completed',
        signed_file_path='org/tx/contract.pdf',
        signed_original_filename='contract.pdf',
        extraction_status='complete',
        field_data={
            'buyer_names': ['Jeffrey Rushing', 'Amy Rushing'],
            'sales_price': '440000',
            '_document_identity': {
                'kind': 'purchase_contract',
                'template_slug': 'seller-offer-contract',
                'confidence': 0.95,
            },
        },
    )
    hoa = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug='hoa-addendum',
        template_name='HOA Addendum',
        status='signed',
        document_source='completed',
        signed_file_path='org/tx/hoa.pdf',
        signed_original_filename='hoa.pdf',
        extraction_status='complete',
        field_data={},
    )
    db.session.add_all([contract, hoa])
    db.session.flush()

    version = SellerOfferVersion(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        offer_id=offer.id,
        created_by_id=seed['owner_a'],
        transaction_document_id=contract.id,
        version_number=1,
        direction='buyer_offer',
        status='submitted',
        terms_data={'offer_price': '440000', 'buyer_names': buyer_names},
    )
    db.session.add(version)
    db.session.flush()
    offer.current_version_id = version.id

    for doc, primary, dtype in (
        (contract, True, 'buyer_offer'),
        (hoa, False, 'hoa_addendum'),
    ):
        db.session.add(SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            offer_id=offer.id,
            transaction_document_id=doc.id,
            offer_version_id=version.id if primary else None,
            created_by_id=seed['owner_a'],
            document_type=dtype,
            display_name=doc.template_name,
            is_primary_terms_document=primary,
            extraction_summary={},
        ))
    db.session.flush()
    return offer, contract, hoa


def test_offer_package_review_url_shape():
    assert '/offers/9/review' in offer_package_review_url(3, 9)


def test_build_offer_package_review_aggregates(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        offer, contract, hoa = _make_offer_with_docs(seed, tx)
        report = DocumentReviewReport(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            document_id=hoa.id,
            severity=DocumentReviewReport.SEVERITY_ATTENTION,
            status=DocumentReviewReport.STATUS_OPEN,
            title='Noise',
            summary='noise',
            findings=[
                {
                    'code': 'party_mismatch',
                    'message': 'The buyer names differ from the CRM contact/party records.',
                    'severity': 'attention',
                },
                {
                    'code': 'blank_subdivision_days',
                    'message': 'Day-count blanks in Paragraph A are empty.',
                    'severity': 'attention',
                    'page': 1,
                },
            ],
            field_count=2,
            toast_required=True,
        )
        db.session.add(report)
        db.session.flush()

        payload = build_offer_package_review(transaction=tx, offer=offer)
        assert payload['offer_id'] == offer.id
        assert payload['terms']['buyer_names']
        assert payload['terms']['offer_price'] == '$440,000'
        assert payload['offer_price'] == 440000.0
        assert len(payload['documents']) == 2
        assert payload['primary_document_id'] == contract.id
        codes = {f['code'] for f in payload['findings']}
        assert 'party_mismatch' not in codes
        assert 'blank_subdivision_days' in codes
        assert '/offers/' in payload['confirm_url']
        db.session.rollback()


def test_confirm_offer_package_files_docs_and_updates_terms(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        offer, contract, hoa = _make_offer_with_docs(seed, tx)
        confirm_offer_package(
            offer=offer,
            actor_id=seed['owner_a'],
            terms_dict={
                'buyer_names': 'Jeffrey Rushing, Amy Rushing',
                'offer_price': '441000',
                'earnest_money': '4500',
                'option_fee': '250',
                'option_period_days': '7',
                'financing_type': 'conventional',
                'financing_amount': '396000',
                'title_policy_payer': 'Seller',
                'survey_payer': 'Buyer',
                'buyer_agent_commission_percent': '3',
                'residential_service_contract': '450',
                'seller_concessions_amount': '5000',
                'buyer_agent_name': 'Alex Agent',
                'buyer_agent_brokerage': 'Home Team Realty',
            },
            draft=False,
        )
        db.session.flush()
        db.session.refresh(offer)
        db.session.refresh(contract)
        db.session.refresh(hoa)

        assert offer.status == 'reviewing'
        assert offer.offer_price == Decimal('441000.00')
        assert offer.earnest_money == Decimal('4500.00')
        assert offer.option_period_days == 7
        assert offer.title_policy_payer == 'Seller'
        assert offer.survey_payer == 'Buyer'
        assert offer.buyer_agent_commission_percent == Decimal('3')
        assert offer.residential_service_contract == '450'
        assert offer.financing_amount == Decimal('396000.00')
        assert offer.buyer_agent_name == 'Alex Agent'
        assert offer.buyer_agent_brokerage == 'Home Team Realty'

        for doc in (contract, hoa):
            conf = (doc.field_data or {}).get('_classification_confirmation') or {}
            assert conf.get('scope') == 'offer'
            assert conf.get('offer_id') == offer.id

        packages = build_document_packages(tx)
        offer_pkgs = packages.get('offer_packages') or []
        match = next(p for p in offer_pkgs if p['scope_id'] == offer.id)
        assert '/offers/' in (match.get('review_url') or '')
        assert 'review' in (match.get('review_url') or '')
        for row in match.get('documents') or []:
            if row.get('document_id'):
                assert row.get('offer_package_review') is True
                assert row.get('state') != 'needs_classification'
        db.session.rollback()


def test_offer_package_review_route_and_confirm(app, owner_a_client, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        offer, _, _ = _make_offer_with_docs(seed, tx)
        offer_id = offer.id
        tx_id = tx.id
        db.session.commit()

    resp = owner_a_client.get(f'/transactions/{tx_id}/offers/{offer_id}/review')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Offer package review' in body
    assert 'Confirm offer' in body
    assert 'Jeffrey' in body
    assert 'Title paid by' in body
    assert 'Survey paid by' in body
    assert 'Closing date' in body
    assert 'Buyer’s agent commission paid by seller' in body or "Buyer's agent commission paid by seller" in body
    assert 'Residential service contract amount' in body
    assert 'Option days' in body
    assert 'Seller concessions' in body
    assert 'Buyer agent' in body
    assert 'Buyer brokerage' in body
    assert 'Financing type' in body
    assert 'Financing amount' in body
    assert 'offer-package-review__sidebar' in body

    confirm = owner_a_client.post(
        f'/transactions/{tx_id}/offers/{offer_id}/review/confirm',
        json={
            'buyer_names': 'Jeffrey Rushing, Amy Rushing',
            'offer_price': '440000',
            'earnest_money': '4400',
        },
        headers={'Accept': 'application/json'},
    )
    assert confirm.status_code == 200
    data = confirm.get_json()
    assert data['success'] is True
    assert data['offer_id'] == offer_id
