"""Phase-2 safety/completeness defect fixes."""

from datetime import date
from unittest.mock import patch

from models import (
    SellerAcceptedContract,
    SellerContractDocument,
    Transaction,
    TransactionChangeProposal,
    TransactionDocument,
    TransactionRequirement,
    TransactionType,
    db,
)
from services.controlling_contracts import (
    ControllingContractConflict,
    create_baseline_from_document,
)
from services.document_classification_confirm import (
    ClassificationConfirmError,
    confirm_document_classification,
)
from services.document_classification_policy import parse_strict_bool
from services.document_package_workspace import (
    STATE_DETECTED_IN_PACKAGE,
    STATE_NEEDS_CLASSIFICATION,
    STATE_UPLOADED,
    build_document_packages,
)
from services.document_identity import DocumentIdentity
from services.proposal_service import ProposalService


def _buyer_tx(seed, address='Safety Buyer'):
    tx_type = TransactionType.query.filter_by(
        organization_id=seed['org_a'], name='buyer',
    ).first()
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=tx_type.id,
        street_address=address,
        status='showing',
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _doc(seed, tx, slug='completed', field_data=None):
    doc = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        template_slug=slug,
        template_name='Doc',
        status='signed',
        document_source='completed',
        signed_file_path=f'org/tx/{slug}.pdf',
        field_data=field_data or {},
    )
    db.session.add(doc)
    db.session.flush()
    return doc


# --- 1. Baseline conflict ---

def test_second_document_baseline_conflict_preserves_terms(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, 'Conflict Ave')
        doc1 = _doc(seed, tx, slug='one-to-four-family-contract')
        first = create_baseline_from_document(
            transaction=tx,
            document=doc1,
            approved_terms={
                'sales_price': '400000',
                'closing_date': '2026-09-01',
                'effective_date': '2026-08-01',
            },
            actor_id=seed['owner_a'],
        )
        original_price = first.accepted_price
        original_close = first.closing_date

        doc2 = _doc(seed, tx, slug='one-to-four-family-contract')
        try:
            create_baseline_from_document(
                transaction=tx,
                document=doc2,
                approved_terms={
                    'sales_price': '999999',
                    'closing_date': '2026-12-31',
                },
                actor_id=seed['owner_a'],
            )
            assert False, 'expected ControllingContractConflict'
        except ControllingContractConflict as exc:
            assert exc.code == 'baseline_conflict'
            assert exc.existing_contract_id == first.id

        db.session.refresh(first)
        assert first.accepted_price == original_price
        assert first.closing_date == original_close
        assert SellerContractDocument.query.filter_by(
            transaction_document_id=doc2.id,
            is_primary_contract_document=True,
        ).count() == 0
        assert SellerAcceptedContract.query.filter_by(
            transaction_id=tx.id, position='primary', status='active',
        ).count() == 1
        db.session.rollback()


def test_same_document_retry_updates_selected_terms_only(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, 'Retry Ave')
        doc = _doc(seed, tx, slug='one-to-four-family-contract')
        create_baseline_from_document(
            transaction=tx,
            document=doc,
            approved_terms={
                'sales_price': '410000',
                'closing_date': '2026-09-10',
                'effective_date': '2026-08-01',
            },
            actor_id=seed['owner_a'],
        )
        again = create_baseline_from_document(
            transaction=tx,
            document=doc,
            approved_terms={'closing_date': '2026-10-10'},
            actor_id=seed['owner_a'],
        )
        assert again.closing_date == date(2026, 10, 10)
        assert SellerAcceptedContract.query.filter_by(transaction_id=tx.id).count() == 1
        db.session.rollback()


def test_attach_as_supporting_without_applying_terms(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, 'Support Ave')
        doc1 = _doc(seed, tx, slug='one-to-four-family-contract')
        first = create_baseline_from_document(
            transaction=tx,
            document=doc1,
            approved_terms={
                'sales_price': '420000',
                'closing_date': '2026-09-15',
                'effective_date': '2026-08-01',
            },
            actor_id=seed['owner_a'],
        )
        doc2 = _doc(seed, tx, slug='hoa-addendum')
        create_baseline_from_document(
            transaction=tx,
            document=doc2,
            approved_terms={'sales_price': '1'},
            actor_id=seed['owner_a'],
            attach_as_supporting=True,
        )
        db.session.refresh(first)
        assert str(first.accepted_price) in ('420000', '420000.00')
        link = SellerContractDocument.query.filter_by(
            transaction_document_id=doc2.id,
        ).one()
        assert link.is_primary_contract_document is False
        db.session.rollback()


# --- 2. Strict validation ---

def test_parse_strict_bool_false_string():
    assert parse_strict_bool('false') is False
    assert parse_strict_bool('true') is True
    assert parse_strict_bool(False) is False


def test_normalize_selected_field_flags_rejects_false_string_as_true():
    from services.document_classification_policy import (
        ClassificationPolicyError,
        normalize_selected_field_flags,
    )

    flags = normalize_selected_field_flags({
        'sales_price': True,
        'closing_date': 'false',
        'earnest_money': False,
    })
    assert flags == {
        'sales_price': True,
        'closing_date': False,
        'earnest_money': False,
    }
    try:
        normalize_selected_field_flags({'sales_price': 'maybe'})
        assert False
    except ClassificationPolicyError as exc:
        assert exc.code == 'invalid_boolean'


def test_approve_selected_string_false_does_not_apply_field(app, seed, owner_a_client):
    with app.app_context():
        tx = _buyer_tx(seed, 'Strict Bool Apply')
        doc = _doc(
            seed, tx, slug='one-to-four-family-contract',
            field_data={
                'sales_price': '400000',
                'closing_date': '2026-09-01',
                'effective_date': '2026-08-01',
            },
        )
        confirm_document_classification(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            payload={
                'kind': 'purchase_contract',
                'template_slug': 'one-to-four-family-contract',
                'scope': 'contract',
            },
        )
        proposal = ProposalService.create_proposal(
            transaction_id=tx.id,
            organization_id=seed['org_a'],
            change_type='extracted_contract_fields',
            proposed_changes={
                'sales_price': '400000',
                'closing_date': '2026-09-01',
                'effective_date': '2026-08-01',
            },
            rationale='test',
            source_document_id=doc.id,
        )
        db.session.commit()
        tx_id, proposal_id = tx.id, proposal.id

    resp = owner_a_client.post(
        f'/transactions/{tx_id}/proposals/{proposal_id}/approve-selected',
        json={
            'selected': {
                'sales_price': True,
                'closing_date': 'false',
                'effective_date': True,
            },
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert 'closing_date' not in (body.get('applied_keys') or [])
    assert 'sales_price' in (body.get('applied_keys') or [])

    with app.app_context():
        from models import SellerAcceptedContract, SellerContractDocument
        contract = SellerAcceptedContract.query.filter_by(
            transaction_id=tx_id, status='active',
        ).one()
        assert contract.closing_date is None
        frozen = contract.frozen_terms or {}
        assert 'closing_date' not in frozen
        assert frozen.get('sales_price') == '400000'
        SellerContractDocument.query.filter_by(transaction_id=tx_id).delete()
        SellerAcceptedContract.query.filter_by(transaction_id=tx_id).delete()
        db.session.commit()


def test_kind_slug_mismatch_rejected(app, seed):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        doc = _doc(seed, tx)
        try:
            confirm_document_classification(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                payload={
                    'kind': 'listing_agreement',
                    'template_slug': 'one-to-four-family-contract',
                    'scope': 'listing',
                },
            )
            assert False
        except ClassificationConfirmError as exc:
            assert exc.code == 'kind_slug_mismatch'
        assert doc.template_slug == 'completed'  # no mutation
        db.session.rollback()


def test_buyer_listing_side_mismatch(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, 'No Listing')
        doc = _doc(seed, tx)
        try:
            confirm_document_classification(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                payload={
                    'kind': 'listing_agreement',
                    'template_slug': 'listing-agreement',
                    'scope': 'listing',
                },
            )
            assert False
        except ClassificationConfirmError as exc:
            assert exc.code == 'side_mismatch_listing'
        db.session.rollback()


def test_listing_cannot_file_to_offer(app, seed):
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        doc = _doc(seed, tx)
        try:
            confirm_document_classification(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                payload={
                    'kind': 'listing_agreement',
                    'template_slug': 'listing-agreement',
                    'scope': 'offer',
                    'create_new_offer': True,
                },
            )
            assert False
        except ClassificationConfirmError as exc:
            assert exc.code in ('scope_incompatible',)
        db.session.rollback()


def test_seller_purchase_contract_requires_explicit_controlling(app, seed):
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
                    'template_slug': 'seller-offer-contract',
                    'scope': 'contract',
                },
            )
            assert False
        except ClassificationConfirmError as exc:
            assert exc.code == 'seller_controlling_unconfirmed'
        # string "false" must not unlock controlling
        try:
            confirm_document_classification(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                payload={
                    'kind': 'purchase_contract',
                    'template_slug': 'seller-offer-contract',
                    'scope': 'contract',
                    'explicit_controlling_confirmation': 'false',
                },
            )
            assert False
        except ClassificationConfirmError as exc:
            assert exc.code == 'seller_controlling_unconfirmed'
        db.session.rollback()


def test_amendment_scope_requires_baseline(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, 'No Amend Base')
        doc = _doc(seed, tx)
        try:
            confirm_document_classification(
                transaction=tx,
                document=doc,
                actor_id=seed['owner_a'],
                payload={
                    'kind': 'amendment',
                    'template_slug': 'amendment',
                    'scope': 'amendment',
                },
            )
            assert False
        except ClassificationConfirmError as exc:
            assert exc.code == 'no_controlling_contract'
        db.session.rollback()


# --- 3. Direct contract upload completion ---

def test_buyer_direct_contract_classify_then_apply_selected(app, seed, owner_a_client):
    with app.app_context():
        tx = _buyer_tx(seed, 'Direct Contract')
        doc = _doc(
            seed, tx, slug='completed',
            field_data={
                '_document_identity': {
                    'kind': 'purchase_contract',
                    'template_slug': 'one-to-four-family-contract',
                    'confidence': 0.93,
                    'execution_state': 'executed',
                },
                'sales_price': '455000',
                'closing_date': '2026-09-20',
                'effective_date': '2026-08-05',
                'option_period_days': 7,
                'financing_type': 'Conventional',
                'buyer_name': 'Should Stay Unapplied',
            },
        )
        confirm_document_classification(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            payload={
                'kind': 'purchase_contract',
                'template_slug': 'one-to-four-family-contract',
                'scope': 'contract',
            },
        )
        proposal = ProposalService.create_proposal(
            transaction_id=tx.id,
            organization_id=seed['org_a'],
            change_type='extracted_contract_fields',
            proposed_changes={
                'sales_price': '455000',
                'closing_date': '2026-09-20',
                'effective_date': '2026-08-05',
                'option_period_days': 7,
                'financing_type': 'Conventional',
                'buyer_name': 'Should Stay Unapplied',
            },
            rationale='test',
            source_document_id=doc.id,
        )
        db.session.commit()
        tx_id, proposal_id, doc_id = tx.id, proposal.id, doc.id

    resp = owner_a_client.post(
        f'/transactions/{tx_id}/proposals/{proposal_id}/approve-selected',
        json={
            'selected': {
                'sales_price': True,
                'closing_date': True,
                'effective_date': True,
                'option_period_days': True,
                'financing_type': False,
                'buyer_name': False,
            },
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['accepted_contract_id']
    assert data['transaction_status'] == 'under_contract'
    assert 'financing_type' not in data['applied_keys']
    assert 'buyer_name' not in data['applied_keys']

    with app.app_context():
        tx = Transaction.query.get(tx_id)
        contract = SellerAcceptedContract.query.filter_by(
            transaction_id=tx_id, position='primary', status='active',
        ).one()
        assert tx.status == 'under_contract'
        assert tx.expected_close_date == date(2026, 9, 20)
        assert contract.closing_date == date(2026, 9, 20)
        frozen = contract.frozen_terms or {}
        assert 'buyer_name' not in frozen or frozen.get('buyer_name') is None
        assert TransactionRequirement.query.filter_by(
            transaction_id=tx_id, package_key='buyer_ctc',
        ).count() >= 1
        assert SellerContractDocument.query.filter_by(
            accepted_contract_id=contract.id,
            transaction_document_id=doc_id,
            is_primary_contract_document=True,
        ).count() == 1
        SellerContractDocument.query.filter_by(transaction_id=tx_id).delete()
        SellerAcceptedContract.query.filter_by(transaction_id=tx_id).delete()
        TransactionChangeProposal.query.filter_by(transaction_id=tx_id).delete()
        TransactionRequirement.query.filter_by(transaction_id=tx_id).delete()
        TransactionDocument.query.filter_by(transaction_id=tx_id).delete()
        Transaction.query.filter_by(id=tx_id).delete()
        db.session.commit()


def test_seller_explicit_controlling_then_apply(app, seed, owner_a_client):
    with app.app_context():
        tx_type = TransactionType.query.filter_by(
            organization_id=seed['org_a'], name='seller',
        ).first()
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=tx_type.id,
            street_address='Seller Explicit Ctrl',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        doc = _doc(
            seed, tx, slug='completed',
            field_data={
                'sales_price': '500000',
                'closing_date': '2026-10-01',
                'effective_date': '2026-08-10',
            },
        )
        confirm_document_classification(
            transaction=tx,
            document=doc,
            actor_id=seed['owner_a'],
            payload={
                'kind': 'purchase_contract',
                'template_slug': 'seller-accepted-contract',
                'scope': 'contract',
                'explicit_controlling_confirmation': True,
            },
        )
        proposal = ProposalService.create_proposal(
            transaction_id=tx.id,
            organization_id=seed['org_a'],
            change_type='extracted_contract_fields',
            proposed_changes={
                'sales_price': '500000',
                'closing_date': '2026-10-01',
                'effective_date': '2026-08-10',
            },
            rationale='seller explicit',
            source_document_id=doc.id,
        )
        db.session.commit()
        tx_id, proposal_id = tx.id, proposal.id

    resp = owner_a_client.post(
        f'/transactions/{tx_id}/proposals/{proposal_id}/approve-selected',
        json={'selected': {
            'sales_price': True, 'closing_date': True, 'effective_date': True,
        }},
    )
    assert resp.status_code == 200
    assert resp.get_json()['accepted_contract_id']

    with app.app_context():
        assert SellerAcceptedContract.query.filter_by(
            transaction_id=tx_id, position='primary', status='active',
        ).count() == 1
        # Clean committed rows so later seed['tx_a'] tests stay isolated.
        SellerContractDocument.query.filter_by(transaction_id=tx_id).delete()
        SellerAcceptedContract.query.filter_by(transaction_id=tx_id).delete()
        TransactionChangeProposal.query.filter_by(transaction_id=tx_id).delete()
        TransactionDocument.query.filter_by(transaction_id=tx_id).delete()
        TransactionRequirement.query.filter_by(transaction_id=tx_id).delete()
        Transaction.query.filter_by(id=tx_id).delete()
        db.session.commit()


def test_approve_selected_baseline_conflict_409(app, seed, owner_a_client):
    with app.app_context():
        tx = _buyer_tx(seed, 'Conflict Approve')
        primary = _doc(seed, tx, slug='one-to-four-family-contract')
        create_baseline_from_document(
            transaction=tx,
            document=primary,
            approved_terms={
                'sales_price': '300000',
                'closing_date': '2026-09-01',
                'effective_date': '2026-08-01',
            },
            actor_id=seed['owner_a'],
        )
        other = _doc(
            seed, tx, slug='completed',
            field_data={
                '_classification_confirmation': {
                    'scope': 'contract',
                    'template_slug': 'one-to-four-family-contract',
                    'kind': 'purchase_contract',
                    'confirmed_by_id': seed['owner_a'],
                },
            },
        )
        proposal = ProposalService.create_proposal(
            transaction_id=tx.id,
            organization_id=seed['org_a'],
            change_type='extracted_contract_fields',
            proposed_changes={'closing_date': '2026-12-01'},
            rationale='conflict',
            source_document_id=other.id,
        )
        db.session.commit()
        tx_id, proposal_id = tx.id, proposal.id
        original_close = date(2026, 9, 1)

    resp = owner_a_client.post(
        f'/transactions/{tx_id}/proposals/{proposal_id}/approve-selected',
        json={'selected': {'closing_date': True}},
    )
    assert resp.status_code == 409
    assert resp.get_json().get('code') == 'baseline_conflict'

    with app.app_context():
        contract = SellerAcceptedContract.query.filter_by(
            transaction_id=tx_id, position='primary', status='active',
        ).one()
        assert contract.closing_date == original_close
        SellerContractDocument.query.filter_by(transaction_id=tx_id).delete()
        SellerAcceptedContract.query.filter_by(transaction_id=tx_id).delete()
        TransactionChangeProposal.query.filter_by(transaction_id=tx_id).delete()
        TransactionRequirement.query.filter_by(transaction_id=tx_id).delete()
        TransactionDocument.query.filter_by(transaction_id=tx_id).delete()
        Transaction.query.filter_by(id=tx_id).delete()
        db.session.commit()


# --- 4/5. Package workspace ---

def test_package_includes_unmatched_and_embedded(app, seed):
    with app.app_context():
        tx = _buyer_tx(seed, 'Package Visible')
        primary = _doc(
            seed, tx,
            slug='one-to-four-family-contract',
            field_data={
                'detected_documents': [
                    {
                        'document_type': 'residential_contract',
                        'start_page': 1,
                        'end_page': 10,
                    },
                    {
                        'document_type': 'hoa_addendum',
                        'start_page': 11,
                        'end_page': 12,
                    },
                    {
                        'document_type': 'third_party_financing',
                        'start_page': 13,
                        'end_page': 14,
                    },
                ],
                '_document_identity': DocumentIdentity(
                    kind='purchase_contract',
                    template_slug='one-to-four-family-contract',
                    form_number='TREC 20',
                    confidence=0.93,
                    extras={
                        'package_authority': 'ai_detected_documents',
                        'embedded_components': [
                            {
                                'kind': 'addendum',
                                'template_slug': 'hoa-addendum',
                                'form_number': 'TREC 36',
                                'source': 'ai_detected_documents',
                            },
                            {
                                'kind': 'addendum',
                                'template_slug': 'third-party-financing-addendum',
                                'form_number': 'TREC 40',
                                'source': 'ai_detected_documents',
                            },
                        ],
                    },
                ).to_dict(),
                'financing_type': 'Conventional',
            },
        )
        create_baseline_from_document(
            transaction=tx,
            document=primary,
            approved_terms={
                'sales_price': '440000',
                'closing_date': '2026-09-12',
                'effective_date': '2026-08-02',
                'financing_type': 'Conventional',
            },
            actor_id=seed['owner_a'],
        )
        generic = _doc(seed, tx, slug='completed')
        # Exact generic slug only — custom-* learned templates count as filed.
        custom = _doc(seed, tx, slug='custom')
        extra = _doc(seed, tx, slug='sellers-disclosure')

        packages = build_document_packages(tx)
        assert packages['listing_package'] is None
        assert packages['buyer_transaction_package'] is not None
        contract_rows = packages['controlling_contract_package']['documents']
        buyer_rows = packages['buyer_transaction_package']['documents']
        unfiled_rows = packages['unfiled_documents']['documents']

        hoa = next(r for r in contract_rows if r['canonical_slug'] == 'hoa-addendum')
        tpf = next(
            r for r in contract_rows
            if r['canonical_slug'] == 'third-party-financing-addendum'
        )
        assert hoa['state'] == STATE_DETECTED_IN_PACKAGE
        assert hoa['detected_in_package'] is True
        assert hoa['document_id'] is None
        assert hoa['parent_document_id'] == primary.id
        assert tpf['state'] == STATE_DETECTED_IN_PACKAGE

        all_rows = buyer_rows + contract_rows + unfiled_rows
        generic_row = next(r for r in unfiled_rows if r.get('document_id') == generic.id)
        custom_row = next(r for r in unfiled_rows if r.get('document_id') == custom.id)
        extra_row = next(r for r in buyer_rows if r.get('document_id') == extra.id)
        assert generic_row['state'] == STATE_NEEDS_CLASSIFICATION
        assert custom_row['state'] == STATE_NEEDS_CLASSIFICATION
        assert extra_row['state'] == STATE_UPLOADED
        unfiled_ids = [r['document_id'] for r in unfiled_rows if r.get('document_id')]
        assert unfiled_ids.count(generic.id) == 1
        assert unfiled_ids.count(custom.id) == 1
        assert extra.id not in unfiled_ids

        seen_ids = {
            r['document_id']
            for r in all_rows
            if r.get('document_id')
        }
        assert {primary.id, generic.id, custom.id, extra.id} <= seen_ids

        # POF is never presented as required; with nothing calling for it, the
        # speculative row stays out of the package entirely.
        assert not [
            r for r in contract_rows
            if r.get('canonical_slug') == 'pre-approval-or-proof-of-funds'
        ]
        db.session.rollback()
