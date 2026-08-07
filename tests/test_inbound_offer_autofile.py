"""High-confidence seller purchase contracts auto-file as inbound offers."""

from datetime import datetime, timedelta
from decimal import Decimal

from models import (
    SellerOffer,
    SellerOfferDocument,
    Transaction,
    TransactionDocument,
    db,
)
from services.document_identity import DocumentIdentity, KIND_PURCHASE_CONTRACT
from services.scoped_document_intake import (
    maybe_auto_file_seller_inbound_offer,
    terms_from_document_field_data,
)


def _seller_tx(seed):
    return Transaction.query.get(seed['tx_a'])


def _make_doc(seed, tx, *, slug='completed', field_data=None, minutes_ago=0):
    doc = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug=slug,
        template_name='Upload',
        status='signed',
        document_source='completed',
        field_data=field_data or {},
        created_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def test_terms_normalize_decimal_price_blowup():
    terms = terms_from_document_field_data(
        {'sales_price': '44000000', 'buyer_names': ['Jeffrey Rushing', 'Amy Rushing']},
        list_price='485000',
    )
    assert terms['offer_price'] == '440000.00'
    assert terms['_offer_price_decimal'] == Decimal('440000.00')
    assert 'Jeffrey' in terms['buyer_names']


def test_auto_file_creates_offer_and_attaches_support(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        tx.extra_data = {'list_price': '485000'}
        db.session.flush()

        support = _make_doc(
            seed,
            tx,
            slug='third-party-financing-addendum',
            field_data={
                'buyer_names': ['Jeffrey Rushing', 'Amy Rushing'],
                'document_classification': 'financing_addendum',
                '_document_identity': {
                    'kind': 'addendum',
                    'template_slug': 'third-party-financing-addendum',
                    'confidence': 0.92,
                    'possible_scopes': ['offer', 'contract'],
                },
            },
        )
        contract = _make_doc(
            seed,
            tx,
            slug='completed',
            field_data={
                'sales_price': '44000000',
                'buyer_names': ['Jeffrey Rushing', 'Amy Rushing'],
                'earnest_money': '5000',
                'document_classification': 'purchase_contract',
                '_document_identity': {
                    'kind': KIND_PURCHASE_CONTRACT,
                    'template_slug': 'seller-offer-contract',
                    'confidence': 0.95,
                    'possible_scopes': ['offer', 'contract'],
                    'offer_document_type': 'buyer_offer',
                    'is_high_confidence': True,
                },
            },
        )
        identity = DocumentIdentity.from_dict(contract.field_data['_document_identity'])

        result = maybe_auto_file_seller_inbound_offer(
            document=contract,
            actor_id=seed['owner_a'],
            identity=identity,
        )
        assert result is not None
        assert result['created'] is True
        assert contract.template_slug == 'seller-offer-contract'

        offer = SellerOffer.query.get(result['offer_id'])
        assert offer is not None
        assert offer.offer_price == Decimal('440000.00')
        assert 'Jeffrey' in (offer.buyer_names or '')

        linked = {
            row.transaction_document_id
            for row in SellerOfferDocument.query.filter_by(offer_id=offer.id).all()
        }
        assert contract.id in linked
        assert support.id in linked
        assert support.id in result['support_document_ids']

        again = maybe_auto_file_seller_inbound_offer(
            document=contract,
            actor_id=seed['owner_a'],
            identity=identity,
        )
        assert again['idempotent'] is True
        assert SellerOffer.query.filter_by(transaction_id=tx.id).count() == 1
        db.session.rollback()


def test_auto_file_does_not_attach_listing_hoa_with_different_buyers(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        listing_hoa = _make_doc(
            seed,
            tx,
            slug='hoa-addendum',
            field_data={
                'buyer_names': 'Michael Mayeux and Tricia Mayeux',
                '_document_identity': {
                    'kind': 'addendum',
                    'confidence': 0.9,
                    'possible_scopes': ['offer', 'contract', 'listing'],
                },
            },
            minutes_ago=2,
        )
        contract = _make_doc(
            seed,
            tx,
            slug='completed',
            field_data={
                'sales_price': '440000',
                'buyer_names': ['Jeffrey Rushing', 'Amy Rushing'],
                '_document_identity': {
                    'kind': KIND_PURCHASE_CONTRACT,
                    'template_slug': 'seller-offer-contract',
                    'confidence': 0.95,
                    'possible_scopes': ['offer', 'contract'],
                },
            },
        )
        result = maybe_auto_file_seller_inbound_offer(
            document=contract,
            actor_id=seed['owner_a'],
            identity=DocumentIdentity.from_dict(contract.field_data['_document_identity']),
        )
        linked = {
            row.transaction_document_id
            for row in SellerOfferDocument.query.filter_by(offer_id=result['offer_id']).all()
        }
        assert listing_hoa.id not in linked
        db.session.rollback()


def test_auto_file_skips_non_purchase(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        doc = _make_doc(
            seed,
            tx,
            slug='hoa-addendum',
            field_data={
                '_document_identity': {
                    'kind': 'addendum',
                    'confidence': 0.95,
                    'is_high_confidence': True,
                },
            },
        )
        assert maybe_auto_file_seller_inbound_offer(
            document=doc,
            actor_id=seed['owner_a'],
        ) is None
        assert SellerOffer.query.filter_by(transaction_id=tx.id).count() == 0
        db.session.rollback()
