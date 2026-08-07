"""Offer package UX: sync summary, coherent package rows, inbound review profile."""

from decimal import Decimal
from types import SimpleNamespace

from models import (
    SellerOffer,
    SellerOfferDocument,
    SellerOfferVersion,
    Transaction,
    TransactionDocument,
    TransactionParticipant,
    db,
)
from services.document_identity import DocumentIdentity, identify_from_text
from services.document_package_workspace import (
    STATE_DETECTED_IN_PACKAGE,
    STATE_NEEDS_CLASSIFICATION,
    STATE_UPLOADED,
    _build_package_rows,
)
from services.document_review import build_findings
from services.expected_documents import expected_documents_for_context
from services.seller_workflow import (
    _normalize_extracted_money,
    sync_offer_thread_from_extraction,
)


def _seller_tx(seed):
    return Transaction.query.get(seed['tx_a'])


def _link_offer(seed, tx, docs, *, buyer_names=None, offer_price=None):
    offer = SellerOffer(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        created_by_id=seed['owner_a'],
        status='needs_review',
        buyer_names=buyer_names,
        offer_price=offer_price,
        creation_source='test',
    )
    db.session.add(offer)
    db.session.flush()
    for doc in docs:
        link = SellerOfferDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            offer_id=offer.id,
            transaction_document_id=doc.id,
            created_by_id=seed['owner_a'],
            document_type=doc.template_slug or 'supporting',
            display_name=doc.template_name or 'Doc',
            is_primary_terms_document=False,
            extraction_summary={},
        )
        db.session.add(link)
    db.session.flush()
    return offer


def test_normalize_extracted_money_fixes_scale_blowup():
    assert _normalize_extracted_money('44000000', list_price='485000') == Decimal('440000.00')
    assert _normalize_extracted_money('440000', offer_price='440000') == Decimal('440000.00')
    assert _normalize_extracted_money(
        '25000', offer_price='440000', field_role='ancillary',
    ) == Decimal('250.00')
    assert _normalize_extracted_money(
        '440000', offer_price='440000', field_role='ancillary',
    ) == Decimal('4400.00')
    # Must not shrink a real offer price that is merely below list.
    assert _normalize_extracted_money('440000', list_price='485000') == Decimal('440000.00')


def test_trec_49_identity_not_tpf():
    text = (
        "ADDENDUM CONCERNING RIGHT TO TERMINATE DUE TO LENDER'S APPRAISAL "
        "TREC NO. 49-1 TXR 1948 Jeffrey Rushing Amy Rushing"
    )
    identity = identify_from_text(text, filename="Addendum Concerning Right to Terminate Due to Lender's Appraisal (TXR 1948 TREC 49-1).pdf")
    assert identity.template_slug == 'appraisal-termination-addendum'
    assert identity.addendum_key == 'appraisal_termination'
    assert identity.form_number == 'TREC 49'


def test_sync_offer_thread_sets_buyer_price_and_primary(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        tx.extra_data = {'list_price': '485000'}
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='completed',
            template_name='One to Four Family Residential Contract',
            status='signed',
            document_source='completed',
            signed_file_path='org/tx/offer.pdf',
            signed_original_filename='contract.pdf',
            field_data={
                'sales_price': '44000000',
                'buyer_names': ['Jeffrey Rushing', 'Amy Rushing'],
                'earnest_money': '440000',
                'option_fee': '25000',
                'document_classification': 'purchase_contract',
                '_document_identity': {
                    'kind': 'purchase_contract',
                    'template_slug': 'seller-offer-contract',
                    'label': 'Purchase Contract',
                    'confidence': 0.95,
                    'possible_scopes': ['offer', 'contract'],
                },
            },
        )
        db.session.add(doc)
        db.session.flush()
        offer = _link_offer(seed, tx, [doc])

        result = sync_offer_thread_from_extraction(doc.id)
        assert result is not None
        db.session.refresh(offer)
        db.session.refresh(doc)
        link = SellerOfferDocument.query.filter_by(transaction_document_id=doc.id).one()

        assert doc.template_slug == 'seller-offer-contract'
        assert link.is_primary_terms_document is True
        assert link.document_type == 'buyer_offer'
        assert offer.offer_price == Decimal('440000.00')
        assert 'Jeffrey' in (offer.buyer_names or '')
        assert offer.earnest_money == Decimal('4400.00')
        assert offer.option_fee == Decimal('250.00')
        assert SellerOfferVersion.query.filter_by(offer_id=offer.id).count() == 1
        db.session.rollback()


def test_offer_package_rows_prefer_uploaded_over_detected_and_needs_filing(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        contract = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='seller-offer-contract',
            template_name='Purchase Contract',
            status='signed',
            document_source='completed',
            signed_file_path='org/tx/contract.pdf',
            field_data={
                'detected_documents': [
                    {'document_type': 'hoa_addendum', 'pages': '1'},
                ],
                '_document_identity': {
                    'kind': 'purchase_contract',
                    'template_slug': 'seller-offer-contract',
                    'confidence': 0.95,
                    'extras': {'package_authority': 'ai_detected_documents'},
                },
            },
        )
        hoa = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='completed',
            template_name='HOA Addendum',
            status='signed',
            document_source='completed',
            signed_file_path='org/tx/hoa.pdf',
            field_data={
                '_document_identity': {
                    'kind': 'addendum',
                    'template_slug': 'hoa-addendum',
                    'confidence': 0.9,
                    'possible_scopes': ['offer'],
                },
            },
        )
        db.session.add_all([contract, hoa])
        db.session.flush()
        expected = expected_documents_for_context(
            scope='offer',
            terms={'hoa_applicable': True, 'third_party_financing': True},
            identities=[
                DocumentIdentity.from_dict(contract.field_data['_document_identity']),
                DocumentIdentity.from_dict(hoa.field_data['_document_identity']),
            ],
        )
        rows = _build_package_rows(
            expected_list=expected,
            docs=[contract, hoa],
            scope='offer',
            scope_id=1,
            transaction_id=tx.id,
        )
        hoa_rows = [r for r in rows if r.get('canonical_slug') == 'hoa-addendum' or 'HOA' in (r.get('label') or '')]
        assert hoa_rows
        assert any(r['state'] == STATE_UPLOADED and r.get('document_id') == hoa.id for r in hoa_rows)
        assert not any(r['state'] == STATE_DETECTED_IN_PACKAGE for r in hoa_rows)
        assert not any(r['state'] == STATE_NEEDS_CLASSIFICATION for r in rows)
        db.session.rollback()


def test_inbound_offer_findings_suppress_noise(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        # Listing seller party — buyer mismatch would fire without the inbound gate.
        db.session.add(TransactionParticipant(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            role='seller',
            name='Michael Mayeux',
        ))
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='seller-offer-contract',
            template_name='Purchase Contract',
            status='signed',
            document_source='completed',
            signed_file_path='org/tx/c.pdf',
            signed_original_filename='One to Four Family Residential Contract.pdf',
            field_data={},
        )
        db.session.add(doc)
        db.session.flush()
        _link_offer(seed, tx, [doc])

        findings, _ = build_findings(
            transaction=tx,
            document=doc,
            field_data={
                'sales_price': '44000000',
                'buyer_names': ['Jeffrey Rushing', 'Amy Rushing'],
                'title_policy_payer': 'Seller',
                'seller_signature_detected': False,
                'buyer_signature_detected': True,
                'document_classification': 'purchase_contract',
                'sanity_flags': [
                    {
                        'code': 'sales_price_format_anomaly',
                        'severity': 'attention',
                        'message': 'Sales price written with two decimal places; extracted as digits only which may overstate the intended amount by a factor of 100.',
                        'field_key': 'sales_price',
                        'page': 1,
                    },
                    {
                        'code': 'missing_seller_signatures',
                        'severity': 'attention',
                        'message': 'Seller signature lines appear present but are blank.',
                        'field_key': 'seller_signature_detected',
                        'page': 10,
                    },
                    {
                        'code': 'missing_seller_signature',
                        'severity': 'attention',
                        'message': 'Seller signature lines are present but not signed on this addendum.',
                        'field_key': 'seller_signature_detected',
                        'page': 2,
                    },
                    {
                        'code': 'party_mismatch',
                        'severity': 'attention',
                        'message': 'The buyer names (Jeffrey Rushing, Amy Rushing) differs from the CRM contact/party records.',
                        'field_key': 'buyer_names',
                        'page': 1,
                    },
                ],
                '_document_identity': {
                    'kind': 'purchase_contract',
                    'template_slug': 'seller-offer-contract',
                    'confidence': 0.95,
                },
            },
        )
        codes = {f['code'] for f in findings}
        assert 'party_mismatch' not in codes
        assert 'missing_title_email' not in codes
        assert 'sales_price_format_anomaly' not in codes
        assert 'wrong_document_type' not in codes
        assert 'inbound_offer_not_executed' in codes
        # Seller signature_unconfirmed suppressed; informational note kept instead.
        assert not any(
            f['code'] == 'signature_unconfirmed' and 'seller' in (f.get('message') or '').lower()
            for f in findings
        )
        db.session.rollback()
