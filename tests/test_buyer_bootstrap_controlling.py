"""Buyer bootstrap creates controlling baseline (not just status + CTC)."""

from datetime import date
from unittest.mock import patch

from models import (
    ContractBootstrapSession,
    SellerAcceptedContract,
    SellerContractDocument,
    SellerOffer,
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    TransactionType,
    User,
    db,
)
from services.contract_bootstrap import approve_selected, record_upload_metadata
from services.document_identity import (
    EXEC_EXECUTED,
    EXEC_PARTY_SIGNED,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
)


def _user(seed):
    return User.query.get(seed['owner_a'])


def _session(user, org_id, filename='contract.pdf'):
    return record_upload_metadata(
        file_bytes=b'%PDF-1.4 buyer-bootstrap-test',
        filename=filename,
        mime_type='application/pdf',
        source='inbox',
        user=user,
        org_id=org_id,
    )


def _prepare_session(session, *, side, identity, fields, destination_choice=None):
    from services.contract_bootstrap import classify_and_extract
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
    session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW
    session.match_status = ContractBootstrapSession.MATCH_CREATE_NEW
    db.session.flush()


def test_buyer_executed_creates_controlling_baseline_and_ctc(app, seed):
    with app.app_context():
        user = _user(seed)
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
                'property_address': '800 Control Ln',
                'effective_date': '2026-08-04',
                'closing_date': '2026-09-04',
                'option_period_days': 7,
                'sales_price': '410000',
            },
        )
        with patch(
            'services.contract_bootstrap.read_bootstrap_file',
            return_value=b'%PDF-1.4 buyer-exec',
        ), patch(
            'services.supabase_storage.upload_external_document',
            return_value={'path': 'test/buyer-exec.pdf'},
        ):
            transaction, _proposal = approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={
                    'property_address': True,
                    'effective_date': True,
                    'closing_date': True,
                    'option_period_days': True,
                    'sales_price': True,
                },
                corrections={},
                confirmed_side='buyer',
            )

        assert transaction.status == 'under_contract'
        assert transaction.expected_close_date == date(2026, 9, 4)
        contract = SellerAcceptedContract.query.filter_by(
            transaction_id=transaction.id,
            organization_id=seed['org_a'],
            position='primary',
            status='active',
        ).one()
        assert contract.offer_id is None
        doc = TransactionDocument.query.filter_by(transaction_id=transaction.id).one()
        assert SellerContractDocument.query.filter_by(
            accepted_contract_id=contract.id,
            transaction_document_id=doc.id,
            is_primary_contract_document=True,
        ).count() == 1
        assert TransactionRequirement.query.filter_by(
            transaction_id=transaction.id, package_key='buyer_ctc',
        ).count() >= 1
        assert SellerOffer.query.filter_by(transaction_id=transaction.id).count() == 0
        db.session.rollback()


def test_buyer_offer_thread_does_not_create_controlling_baseline(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _session(user, seed['org_a'], filename='offer.pdf')
        identity = DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug='one-to-four-family-contract',
            confidence=0.93,
            execution_state=EXEC_PARTY_SIGNED,
            possible_scopes=('offer', 'contract'),
        )
        _prepare_session(
            session,
            side='buyer',
            identity=identity,
            fields={
                'property_address': '801 Offer Ln',
                'offer_price': '400000',
                'closing_date': '2026-10-01',
            },
        )
        classification = dict(session.classification or {})
        classification['destination_choice'] = 'offer_thread'
        session.classification = classification

        with patch(
            'services.contract_bootstrap.read_bootstrap_file',
            return_value=b'%PDF-1.4 buyer-offer',
        ), patch(
            'services.supabase_storage.upload_external_document',
            return_value={'path': 'test/buyer-offer.pdf'},
        ):
            transaction, _proposal = approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={
                    'property_address': True,
                    'offer_price': True,
                    'closing_date': True,
                },
                corrections={},
                confirmed_side='buyer',
                destination_choice='offer_thread',
            )

        assert transaction.status == 'showing'
        assert SellerAcceptedContract.query.filter_by(
            transaction_id=transaction.id,
        ).count() == 0
        assert SellerOffer.query.filter_by(transaction_id=transaction.id).count() == 1
        db.session.rollback()


def test_matched_buyer_executed_creates_same_baseline(app, seed):
    with app.app_context():
        user = _user(seed)
        tx_type = TransactionType.query.filter_by(
            organization_id=seed['org_a'], name='buyer',
        ).first()
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=user.id,
            transaction_type_id=tx_type.id,
            street_address='802 Match Blvd',
            status='showing',
        )
        db.session.add(tx)
        db.session.flush()

        session = _session(user, seed['org_a'], filename='matched-exec.pdf')
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
                'property_address': '802 Match Blvd',
                'effective_date': '2026-08-05',
                'closing_date': '2026-09-05',
                'option_period_days': 10,
            },
        )
        session.match_status = ContractBootstrapSession.MATCH_MATCHED
        session.matched_transaction_id = tx.id

        with patch(
            'services.contract_bootstrap.read_bootstrap_file',
            return_value=b'%PDF-1.4 matched',
        ), patch(
            'services.supabase_storage.upload_external_document',
            return_value={'path': 'test/matched.pdf'},
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
        assert SellerAcceptedContract.query.filter_by(
            transaction_id=tx.id, position='primary', status='active',
        ).count() == 1
        assert TransactionRequirement.query.filter_by(
            transaction_id=tx.id, package_key='buyer_ctc',
        ).count() >= 1
        db.session.rollback()
