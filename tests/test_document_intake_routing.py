"""Bootstrap / intake routing for listing, offers, buyer contracts, mismatches."""

from datetime import date
from unittest.mock import patch

from models import (
    ContractBootstrapSession,
    SellerAcceptedContract,
    SellerOffer,
    SellerOfferDocument,
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    TransactionType,
    User,
    db,
)
from services.contract_bootstrap import (
    approve_selected,
    classify_and_extract,
    record_upload_metadata,
)
from services.document_identity import (
    EXEC_EXECUTED,
    EXEC_PARTY_SIGNED,
    KIND_DISCLOSURE,
    KIND_LISTING_AGREEMENT,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
)


def _user(seed):
    return User.query.get(seed['owner_a'])


def _session(user, org_id, filename='doc.pdf'):
    return record_upload_metadata(
        file_bytes=b'%PDF-1.4 intake-routing-test',
        filename=filename,
        mime_type='application/pdf',
        source='inbox',
        user=user,
        org_id=org_id,
    )


def _prepare_session(session, *, side, identity, fields, destination_choice=None):
    session.classification = {
        'side': side,
        'side_confirmed_by_user': True,
        'document_identity': identity.to_dict(),
    }
    if destination_choice:
        session.classification['destination_choice'] = destination_choice
    classify_and_extract(
        session=session,
        field_data=fields,
        identity=identity,
    )
    session.match_status = ContractBootstrapSession.MATCH_CREATE_NEW
    session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW
    db.session.flush()
    return session


def _make_tx(org_id, user_id, type_name, address, status='active'):
    tx_type = TransactionType.query.filter_by(
        organization_id=org_id, name=type_name,
    ).first()
    if tx_type is None:
        tx_type = TransactionType(
            organization_id=org_id,
            name=type_name,
            display_name=type_name.title(),
        )
        db.session.add(tx_type)
        db.session.flush()
    tx = Transaction(
        organization_id=org_id,
        created_by_id=user_id,
        transaction_type_id=tx_type.id,
        street_address=address,
        city='Austin',
        state='TX',
        status=status,
        extra_data={},
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def test_seller_listing_real_link_creates_listing_not_under_contract(app, seed):
    """Integration-style: real _link_document_to_transaction, mocked storage only."""
    with app.app_context():
        user = _user(seed)
        session = _session(user, seed['org_a'], filename='TXR-1101.pdf')
        identity = DocumentIdentity(
            kind=KIND_LISTING_AGREEMENT,
            template_slug='listing-agreement',
            form_number='TXR-1101',
            label='Residential Real Estate Listing Agreement',
            confidence=0.95,
            possible_scopes=('listing',),
        )
        _prepare_session(
            session,
            side='seller',
            identity=identity,
            fields={
                'property_address': '700 Listing Lane',
                'list_price': '525000',
                'listing_start_date': '2026-08-01',
                'seller_name': 'Pat Seller',
                'document_type': 'listing_agreement',
            },
        )
        selected = {
            'property_address': True,
            'list_price': True,
            'listing_start_date': True,
        }
        with patch(
            'services.contract_bootstrap.read_bootstrap_file',
            return_value=b'%PDF-1.4 listing',
        ), patch(
            'services.supabase_storage.upload_external_document',
            return_value={'path': 'test/listing.pdf'},
        ):
            transaction, _proposal = approve_selected(
                session=session,
                user_id=user.id,
                selected_fields=selected,
                corrections={},
                confirmed_side='seller',
            )

        assert transaction.transaction_type.name == 'seller'
        assert transaction.status == 'preparing_to_list'
        assert transaction.expected_close_date is None
        doc = TransactionDocument.query.filter_by(
            transaction_id=transaction.id,
            organization_id=seed['org_a'],
        ).one()
        assert doc.template_slug == 'listing-agreement'
        assert SellerOffer.query.filter_by(transaction_id=transaction.id).count() == 0
        assert SellerAcceptedContract.query.filter_by(
            transaction_id=transaction.id,
        ).count() == 0
        reqs = TransactionRequirement.query.filter_by(
            transaction_id=transaction.id,
            organization_id=seed['org_a'],
        ).all()
        assert reqs
        assert {r.package_key for r in reqs} == {'listing'}
        db.session.rollback()


def test_matched_seller_listing_seeds_listing_pack(app, seed):
    with app.app_context():
        user = _user(seed)
        tx = _make_tx(
            seed['org_a'], user.id, 'seller', '701 Existing Listing', status='active',
        )
        session = _session(user, seed['org_a'], filename='TXR-1101.pdf')
        identity = DocumentIdentity(
            kind=KIND_LISTING_AGREEMENT,
            template_slug='listing-agreement',
            form_number='TXR-1101',
            confidence=0.95,
            possible_scopes=('listing',),
        )
        _prepare_session(
            session,
            side='seller',
            identity=identity,
            fields={
                'property_address': '701 Existing Listing',
                'listing_start_date': '2026-08-01',
                'list_price': '400000',
            },
        )
        session.match_status = ContractBootstrapSession.MATCH_MATCHED
        session.matched_transaction_id = tx.id
        before_status = tx.status
        before_extra = dict(tx.extra_data or {})

        with patch(
            'services.contract_bootstrap.read_bootstrap_file',
            return_value=b'%PDF-1.4 listing',
        ), patch(
            'services.supabase_storage.upload_external_document',
            return_value={'path': 'test/listing2.pdf'},
        ):
            approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={
                    'property_address': True,
                    'listing_start_date': True,
                    'list_price': True,
                },
                corrections={},
                confirmed_side='seller',
            )

        db.session.refresh(tx)
        assert tx.status == before_status  # listing attach must not force under_contract
        assert tx.expected_close_date is None
        reqs = TransactionRequirement.query.filter_by(
            transaction_id=tx.id, organization_id=seed['org_a'],
        ).all()
        assert reqs
        assert {r.package_key for r in reqs} == {'listing'}
        doc = TransactionDocument.query.filter_by(
            transaction_id=tx.id, template_slug='listing-agreement',
        ).one()
        assert doc is not None
        # sales_price must not be written from list_price on listing attach
        assert (tx.extra_data or {}).get('sales_price') == before_extra.get('sales_price')
        db.session.rollback()


def test_matched_buyer_executed_seeds_buyer_ctc(app, seed):
    with app.app_context():
        user = _user(seed)
        tx = _make_tx(
            seed['org_a'], user.id, 'buyer', '702 Buyer Match', status='showing',
        )
        session = _session(user, seed['org_a'], filename='executed.pdf')
        identity = DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug='one-to-four-family-contract',
            confidence=0.93,
            execution_state=EXEC_EXECUTED,
            possible_scopes=('offer', 'contract'),
        )
        _prepare_session(
            session,
            side='buyer',
            identity=identity,
            fields={
                'property_address': '702 Buyer Match',
                'effective_date': '2026-08-04',
                'closing_date': '2026-09-04',
                'option_period_days': 7,
            },
        )
        session.match_status = ContractBootstrapSession.MATCH_MATCHED
        session.matched_transaction_id = tx.id

        with patch(
            'services.contract_bootstrap._link_document_to_transaction',
            return_value=None,
        ):
            approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={
                    'property_address': True,
                    'effective_date': True,
                    'closing_date': True,
                    'option_period_days': True,
                },
                corrections={},
                confirmed_side='buyer',
            )

        db.session.refresh(tx)
        assert tx.status == 'under_contract'
        reqs = TransactionRequirement.query.filter_by(
            transaction_id=tx.id, organization_id=seed['org_a'],
        ).all()
        assert reqs
        assert {r.package_key for r in reqs} == {'buyer_ctc'}
        db.session.rollback()


def test_seller_purchase_creates_offer_without_ctc_or_tx_price(app, seed):
    with app.app_context():
        user = _user(seed)
        tx = _make_tx(
            seed['org_a'], user.id, 'seller', '710 Offer Ave', status='active',
        )
        original_extra = dict(tx.extra_data or {})
        session = _session(user, seed['org_a'], filename='buyer-offer.pdf')
        identity = DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug='one-to-four-family-contract',
            form_number='TREC 20',
            confidence=0.93,
            execution_state=EXEC_EXECUTED,
            possible_scopes=('offer', 'contract'),
        )
        _prepare_session(
            session,
            side='seller',
            identity=identity,
            fields={
                'property_address': '710 Offer Ave',
                'buyer_name': 'Casey Buyer',
                'offer_price': '510000',
                'closing_date': '2026-10-01',
                'effective_date': '2026-08-10',
                'sales_price': '510000',
            },
        )
        session.match_status = ContractBootstrapSession.MATCH_MATCHED
        session.matched_transaction_id = tx.id

        with patch(
            'services.contract_bootstrap.read_bootstrap_file',
            return_value=b'%PDF-1.4 offer',
        ), patch(
            'services.supabase_storage.upload_external_document',
            return_value={'path': 'test/offer.pdf'},
        ):
            approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={
                    k: True for k in (
                        'property_address', 'buyer_name', 'offer_price',
                        'closing_date', 'effective_date', 'sales_price',
                    )
                },
                corrections={},
                confirmed_side='seller',
            )

        db.session.refresh(tx)
        assert tx.status == 'active'
        assert tx.expected_close_date is None
        assert (tx.extra_data or {}).get('sales_price') == original_extra.get('sales_price')
        assert TransactionRequirement.query.filter_by(
            transaction_id=tx.id, package_key='seller_ctc',
        ).count() == 0
        offer = SellerOffer.query.filter_by(
            transaction_id=tx.id, organization_id=seed['org_a'],
        ).one()
        assert offer.status == 'needs_review'
        doc = TransactionDocument.query.filter_by(transaction_id=tx.id).first()
        assert doc.template_slug == 'seller-offer-contract'
        assert SellerOfferDocument.query.filter_by(
            offer_id=offer.id, transaction_document_id=doc.id,
        ).count() == 1
        assert SellerAcceptedContract.query.filter_by(transaction_id=tx.id).count() == 0
        db.session.rollback()


def test_buyer_executed_contract_seeds_buyer_ctc(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _session(user, seed['org_a'], filename='executed-contract.pdf')
        identity = DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug='one-to-four-family-contract',
            confidence=0.93,
            execution_state=EXEC_EXECUTED,
            possible_scopes=('offer', 'contract'),
        )
        _prepare_session(
            session,
            side='buyer',
            identity=identity,
            fields={
                'property_address': '720 Buyer Blvd',
                'effective_date': '2026-08-04',
                'closing_date': '2026-09-04',
                'option_period_days': 7,
                'buyer_signature_present': True,
                'seller_signature_present': True,
            },
        )
        with patch(
            'services.contract_bootstrap._link_document_to_transaction',
            return_value=None,
        ):
            transaction, _proposal = approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={
                    'property_address': True,
                    'effective_date': True,
                    'closing_date': True,
                    'option_period_days': True,
                },
                corrections={},
                confirmed_side='buyer',
            )

        assert transaction.transaction_type.name == 'buyer'
        assert transaction.status == 'under_contract'
        reqs = TransactionRequirement.query.filter_by(
            transaction_id=transaction.id,
            organization_id=seed['org_a'],
        ).all()
        assert reqs
        assert {r.package_key for r in reqs} == {'buyer_ctc'}
        db.session.rollback()


def test_buyer_listing_agreement_rejected(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _session(user, seed['org_a'], filename='TXR-1101.pdf')
        identity = DocumentIdentity(
            kind=KIND_LISTING_AGREEMENT,
            template_slug='listing-agreement',
            form_number='TXR-1101',
            confidence=0.95,
            possible_scopes=('listing',),
        )
        _prepare_session(
            session,
            side='buyer',
            identity=identity,
            fields={'property_address': '730 Mismatch Rd'},
        )
        before = Transaction.query.filter_by(organization_id=seed['org_a']).count()
        try:
            approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={'property_address': True},
                corrections={},
                confirmed_side='buyer',
            )
            assert False, 'expected side mismatch ValueError'
        except ValueError as exc:
            assert 'listing agreement' in str(exc).lower() or 'seller' in str(exc).lower()
        assert Transaction.query.filter_by(organization_id=seed['org_a']).count() == before
        db.session.rollback()


def test_classify_and_extract_refreshes_execution_without_losing_identity(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _session(user, seed['org_a'], filename='contract.pdf')
        identity = DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug='one-to-four-family-contract',
            form_number='TREC 20',
            confidence=0.93,
            execution_state='unknown',
            possible_scopes=('offer', 'contract'),
            ambiguous=False,
            extras={'embedded_components': [{'kind': 'addendum', 'form_number': 'TREC 40'}]},
        )
        session.classification = {
            'side': 'buyer',
            'side_confirmed_by_user': True,
            'document_identity': identity.to_dict(),
        }

        classify_and_extract(
            session=session,
            field_data={
                'property_address': '1 Sig Way',
                'buyer_signature_present': True,
                'seller_signature_present': True,
            },
            identity=identity,
        )
        stored = session.classification['document_identity']
        assert stored['kind'] == KIND_PURCHASE_CONTRACT
        assert stored['form_number'] == 'TREC 20'
        assert stored['ambiguous'] is False
        assert stored['execution_state'] == EXEC_EXECUTED
        assert stored['extras']['embedded_components']

        classify_and_extract(
            session=session,
            field_data={
                'buyer_signature_present': True,
                'seller_signature_present': False,
            },
            identity=DocumentIdentity.from_dict(stored),
        )
        assert session.classification['document_identity']['execution_state'] == EXEC_PARTY_SIGNED
        db.session.rollback()


def test_classify_and_extract_preserves_upload_batch_id(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _session(user, seed['org_a'], filename='iabs.pdf')
        session.classification = {
            'upload_batch_id': 'batch-keep-me',
            'storage_backend': 'local',
            'side': 'seller',
            'side_confirmed_by_user': True,
        }
        classify_and_extract(
            session=session,
            field_data={'property_address': '9 Keep Batch Ln'},
            identity=DocumentIdentity(
                kind=KIND_DISCLOSURE,
                template_slug='iabs',
                label='Information About Brokerage Services',
                confidence=0.92,
                possible_scopes=('listing',),
            ),
        )
        assert session.classification.get('upload_batch_id') == 'batch-keep-me'
        db.session.rollback()
