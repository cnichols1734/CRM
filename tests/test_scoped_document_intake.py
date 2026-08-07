"""Scoped document intake attach helpers."""

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
from services.scoped_document_intake import (
    ScopedIntakeError,
    attach_document_to_scope,
    attach_to_offer,
)


def _seller_tx(seed):
    return Transaction.query.get(seed['tx_a'])


def _doc(seed, tx, slug='completed'):
    doc = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug=slug,
        template_name='Upload',
        status='signed',
        document_source='completed',
        field_data={},
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def test_attach_listing_retags_slug(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        doc = _doc(seed, tx, slug='completed')
        result = attach_document_to_scope(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            scope='listing',
            template_slug='listing-agreement',
            template_name='Listing Agreement',
        )
        assert result['scope'] == 'listing'
        assert doc.template_slug == 'listing-agreement'
        db.session.rollback()


def test_attach_offer_requires_explicit_new_or_existing(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        doc = _doc(seed, tx, slug='one-to-four-family-contract')
        try:
            attach_to_offer(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
            )
            assert False, 'expected ScopedIntakeError'
        except ScopedIntakeError as exc:
            assert exc.code == 'offer_unconfirmed'
        db.session.rollback()


def test_attach_offer_create_new_and_idempotent(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        doc = _doc(seed, tx, slug='one-to-four-family-contract')
        first = attach_to_offer(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            create_new_offer=True,
            template_slug='seller-offer-contract',
        )
        second = attach_to_offer(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            create_new_offer=True,
        )
        assert first['created'] is True
        assert second.get('idempotent') is True
        assert first['offer_id'] == second['offer_id']
        assert SellerOfferDocument.query.filter_by(
            transaction_document_id=doc.id,
        ).count() == 1
        db.session.rollback()


def test_attach_wrong_offer_transaction_rejected(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        other = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=tx.transaction_type_id,
            street_address='Other Offer Tx',
            status='active',
        )
        db.session.add(other)
        db.session.flush()
        foreign_offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=other.id,
            created_by_id=seed['owner_a'],
            received_at=datetime.utcnow(),
            status='new',
        )
        db.session.add(foreign_offer)
        db.session.flush()
        doc = _doc(seed, tx, slug='sellers-disclosure')
        try:
            attach_to_offer(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                offer_id=foreign_offer.id,
            )
            assert False, 'expected ScopedIntakeError'
        except ScopedIntakeError as exc:
            assert exc.code == 'offer_not_found'
        db.session.rollback()


def test_attach_contract_requires_baseline(app, seed):
    with app.app_context():
        tx = _seller_tx(seed)
        SellerAcceptedContract.query.filter_by(transaction_id=tx.id).delete()
        db.session.flush()
        doc = _doc(seed, tx, slug='hoa-addendum')
        try:
            attach_document_to_scope(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                scope='contract',
            )
            assert False, 'expected ScopedIntakeError'
        except ScopedIntakeError as exc:
            assert exc.code == 'no_controlling_contract'
        db.session.rollback()
