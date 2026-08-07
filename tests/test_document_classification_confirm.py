"""Classification confirmation endpoint + service tests."""

from datetime import datetime

from models import (
    SellerAcceptedContract,
    SellerOffer,
    SellerOfferDocument,
    Transaction,
    TransactionDocument,
    TransactionType,
    db,
)
from services.controlling_contracts import create_baseline_from_document
from services.document_classification_confirm import (
    ClassificationConfirmError,
    confirm_document_classification,
)


def _doc(seed, tx, slug='completed'):
    doc = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug=slug,
        template_name='Upload',
        status='signed',
        document_source='completed',
        field_data={
            '_document_identity': {
                'kind': 'purchase_contract',
                'template_slug': 'one-to-four-family-contract',
                'confidence': 0.9,
            },
            'sales_price': '999999',
            'closing_date': '2026-12-01',
        },
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def test_confirm_creates_new_seller_offer_without_applying_terms(app, seed):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        original_status = tx.status
        original_close = tx.expected_close_date
        doc = _doc(seed, tx)
        result = confirm_document_classification(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            payload={
                'kind': 'purchase_contract',
                'template_slug': 'seller-offer-contract',
                'scope': 'offer',
                'create_new_offer': True,
            },
        )
        assert result['success'] is True
        assert result['offer_id']
        assert doc.template_slug == 'seller-offer-contract'
        meta = (doc.field_data or {}).get('_classification_confirmation')
        assert meta['confirmed_by_id'] == seed['owner_a']
        assert meta['model_identity_snapshot']['kind'] == 'purchase_contract'
        # Classification alone must not force under_contract / close date.
        db.session.refresh(tx)
        assert tx.status == original_status
        assert tx.expected_close_date == original_close
        assert SellerAcceptedContract.query.filter_by(
            transaction_id=tx.id,
        ).count() == 0
        db.session.rollback()


def test_confirm_contract_without_baseline_leaves_terms_pending(app, seed):
    with app.app_context():
        tx_type = TransactionType.query.filter_by(
            organization_id=seed['org_a'], name='buyer',
        ).first()
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=tx_type.id,
            street_address='Confirm Pending',
            status='showing',
        )
        db.session.add(tx)
        db.session.flush()
        doc = _doc(seed, tx)
        original_slug = doc.template_slug
        result = confirm_document_classification(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            payload={
                'kind': 'purchase_contract',
                'template_slug': 'one-to-four-family-contract',
                'scope': 'contract',
            },
        )
        assert result['baseline_pending_term_approval'] is True
        assert SellerAcceptedContract.query.filter_by(transaction_id=tx.id).count() == 0
        assert tx.status == 'showing'
        assert doc.template_slug == 'one-to-four-family-contract'
        assert original_slug == 'completed'
        db.session.rollback()


def test_confirm_invalid_slug_and_scope(app, seed):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        doc = _doc(seed, tx)
        try:
            confirm_document_classification(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                payload={
                    'kind': 'purchase_contract',
                    'template_slug': 'totally-made-up-slug',
                    'scope': 'offer',
                    'create_new_offer': True,
                },
            )
            assert False
        except ClassificationConfirmError as exc:
            assert exc.code == 'invalid_slug'

        try:
            confirm_document_classification(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                payload={
                    'kind': 'purchase_contract',
                    'template_slug': 'seller-offer-contract',
                    'scope': 'spaceship',
                },
            )
            assert False
        except ClassificationConfirmError as exc:
            assert exc.code == 'invalid_scope'
        db.session.rollback()


def test_confirm_wrong_offer_and_idor(app, seed, owner_a_client):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        # Offer on a different transaction in the same org.
        other_tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=tx.transaction_type_id,
            street_address='Other Confirm Tx',
            status='active',
        )
        db.session.add(other_tx)
        db.session.flush()
        foreign_offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=other_tx.id,
            created_by_id=seed['owner_a'],
            received_at=datetime.utcnow(),
            status='new',
        )
        db.session.add(foreign_offer)
        db.session.flush()
        doc = _doc(seed, tx)
        # Cross-org document on org B transaction.
        doc_b = TransactionDocument(
            organization_id=seed['org_b'],
            transaction_id=seed['tx_b'],
            template_slug='completed',
            template_name='Foreign',
            status='signed',
            document_source='completed',
            field_data={},
        )
        db.session.add(doc_b)
        db.session.commit()
        doc_id = doc.id
        offer_id = foreign_offer.id
        tx_id = tx.id
        tx_b_id = seed['tx_b']
        doc_b_id = doc_b.id

    # Wrong offer (other transaction) → 400 offer_not_found
    resp = owner_a_client.post(
        f'/transactions/{tx_id}/documents/{doc_id}/classification/confirm',
        json={
            'kind': 'disclosure',
            'template_slug': 'sellers-disclosure',
            'scope': 'offer',
            'offer_id': offer_id,
        },
    )
    assert resp.status_code == 400
    assert resp.get_json().get('code') == 'offer_not_found'

    # Cross-org transaction/document IDs → 404 (IDOR)
    resp_idor = owner_a_client.post(
        f'/transactions/{tx_b_id}/documents/{doc_b_id}/classification/confirm',
        json={
            'kind': 'disclosure',
            'template_slug': 'sellers-disclosure',
            'scope': 'other',
        },
    )
    assert resp_idor.status_code == 404

    with app.app_context():
        db.session.rollback()


def test_confirm_retry_idempotent(app, seed):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        doc = _doc(seed, tx)
        payload = {
            'kind': 'purchase_contract',
            'template_slug': 'seller-offer-contract',
            'scope': 'offer',
            'create_new_offer': True,
        }
        first = confirm_document_classification(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            payload=payload,
        )
        second = confirm_document_classification(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            payload=payload,
        )
        assert first['offer_id'] == second['offer_id']
        assert second.get('idempotent') is True
        assert SellerOfferDocument.query.filter_by(
            transaction_document_id=doc.id,
        ).count() == 1
        db.session.rollback()


def test_confirm_amendment_opens_review_when_contract_exists(app, seed):
    with app.app_context():
        tx_type = TransactionType.query.filter_by(
            organization_id=seed['org_a'], name='buyer',
        ).first()
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=tx_type.id,
            street_address='Confirm Amend',
            status='under_contract',
        )
        db.session.add(tx)
        db.session.flush()
        primary_doc = _doc(seed, tx, slug='one-to-four-family-contract')
        create_baseline_from_document(
            transaction=tx,
            document=primary_doc,
            approved_terms={
                'effective_date': '2026-08-01',
                'closing_date': '2026-09-01',
            },
            actor_id=seed['owner_a'],
        )
        amend_doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='completed',
            template_name='Amendment upload',
            status='signed',
            document_source='completed',
            field_data={
                'new_closing_date': '2026-10-01',
                'closing_date': '2026-10-01',
                'document_classification': 'amendment',
            },
        )
        db.session.add(amend_doc)
        db.session.flush()
        result = confirm_document_classification(
            transaction=tx,
            document=amend_doc,
            actor_id=seed['owner_a'],
            payload={
                'kind': 'amendment',
                'template_slug': 'amendment',
                'scope': 'amendment',
            },
        )
        assert result['amendment_id']
        assert 'amendments' in result['next_url']
        db.session.rollback()
