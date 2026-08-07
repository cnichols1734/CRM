"""Seller contract amendment service lifecycle tests."""

from datetime import date, datetime

import pytest

from models import (
    AuditEvent,
    SellerAcceptedContract,
    SellerContractAmendment,
    SellerContractAmendmentVersion,
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    db,
)
from services import amendment_service


def _create_primary_contract(seed, *, closing_date=None, sales_price=500000, **extra):
    contract = SellerAcceptedContract(
        organization_id=seed['org_a'],
        transaction_id=seed['tx_a'],
        created_by_id=seed['owner_a'],
        position='primary',
        status='active',
        accepted_price=sales_price,
        effective_date=date(2026, 8, 1),
        closing_date=closing_date or date(2026, 9, 15),
        option_period_days=7,
        financing_type='Conventional',
        frozen_terms={
            'sales_price': str(sales_price),
            'closing_date': (closing_date or date(2026, 9, 15)).isoformat(),
            'option_period_days': 7,
            'financing_type': 'Conventional',
            'option_fee': '250',
            'earnest_money': '5000',
        },
        **extra,
    )
    db.session.add(contract)
    db.session.flush()
    return contract


def _create_document(seed, field_data, *, transaction_id=None, organization_id=None):
    doc = TransactionDocument(
        organization_id=organization_id or seed['org_a'],
        transaction_id=transaction_id or seed['tx_a'],
        template_slug='amendment',
        template_name='Amendment',
        status='signed',
        document_source='external',
        field_data=field_data,
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def _cleanup_amendments(transaction_id, organization_id):
    amendment_ids = [
        row.id
        for row in SellerContractAmendment.query.filter_by(
            transaction_id=transaction_id,
            organization_id=organization_id,
        ).all()
    ]
    if amendment_ids:
        SellerContractAmendmentVersion.query.filter(
            SellerContractAmendmentVersion.amendment_id.in_(amendment_ids),
        ).delete(synchronize_session=False)
        SellerContractAmendment.query.filter(
            SellerContractAmendment.id.in_(amendment_ids),
        ).delete(synchronize_session=False)
    AuditEvent.query.filter(
        AuditEvent.transaction_id == transaction_id,
        AuditEvent.organization_id == organization_id,
        AuditEvent.event_type.in_((
            'amendment_created',
            'amendment_accepted',
            'amendment_rejected',
        )),
    ).delete(synchronize_session=False)
    SellerAcceptedContract.query.filter_by(
        transaction_id=transaction_id,
        organization_id=organization_id,
    ).delete(synchronize_session=False)
    TransactionRequirement.query.filter_by(
        transaction_id=transaction_id,
        organization_id=organization_id,
    ).delete(synchronize_session=False)
    db.session.commit()


def test_create_from_document_builds_amendment_and_filters_terms(app, seed):
    with app.app_context():
        try:
            _create_primary_contract(seed)
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'document_summary': 'Buyer extends closing.',
                'closing_date': '2026-10-01',
                'sales_price': '510000',
                'random_non_allowlisted': 'ignore-me',
                '_meta': {'closing_date': {'page': 1}},
                '_private': 'secret',
            })

            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            db.session.flush()

            assert amendment is not None
            assert amendment.status == 'received'
            assert amendment.amendment_type == 'amendment'
            assert amendment.summary == 'Buyer extends closing.'
            assert amendment.current_version_id is not None

            version = amendment_service.current_version(amendment)
            assert version is not None
            assert version.version_number == 1
            assert version.direction == 'buyer_amendment'
            assert version.status == 'submitted'
            assert version.transaction_document_id == doc.id
            assert version.terms_data == {
                'closing_date': '2026-10-01',
                'sales_price': '510000',
            }
            assert '_meta' not in version.terms_data
            assert 'random_non_allowlisted' not in version.terms_data

            audit = AuditEvent.query.filter_by(
                event_type='amendment_created',
                transaction_id=seed['tx_a'],
                organization_id=seed['org_a'],
            ).order_by(AuditEvent.id.desc()).first()
            assert audit is not None
            assert audit.event_data['amendment_id'] == amendment.id
        finally:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_create_from_document_returns_none_without_primary_contract(app, seed):
    with app.app_context():
        try:
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'closing_date': '2026-10-01',
            })
            result = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            assert result is None
            assert SellerContractAmendment.query.filter_by(
                transaction_id=seed['tx_a'],
                organization_id=seed['org_a'],
            ).count() == 0
        finally:
            TransactionDocument.query.filter_by(
                transaction_id=seed['tx_a'],
                organization_id=seed['org_a'],
                template_slug='amendment',
            ).delete(synchronize_session=False)
            db.session.commit()


def test_diff_against_contract_marks_changed_and_sorts(app, seed):
    with app.app_context():
        try:
            contract = _create_primary_contract(seed, closing_date=date(2026, 9, 15))
            amendment = SellerContractAmendment(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                accepted_contract_id=contract.id,
                created_by_id=seed['owner_a'],
                status='received',
                amendment_type='amendment',
            )
            db.session.add(amendment)
            db.session.flush()

            version = SellerContractAmendmentVersion(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                amendment_id=amendment.id,
                created_by_id=seed['owner_a'],
                version_number=1,
                direction='buyer_amendment',
                status='submitted',
                terms_data={
                    'closing_date': '2026-10-01',
                    'financing_type': 'Conventional',
                },
            )
            db.session.add(version)
            db.session.flush()
            amendment.current_version_id = version.id
            db.session.flush()

            diffs = amendment_service.diff_against_contract(amendment)
            by_key = {item['key']: item for item in diffs}

            assert by_key['closing_date']['changed'] is True
            assert by_key['financing_type']['changed'] is False
            assert diffs[0]['changed'] is True
            assert diffs[0]['key'] == 'closing_date'
            assert any(not item['changed'] for item in diffs[1:])
        finally:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_accept_selected_keys_only(app, seed):
    with app.app_context():
        try:
            contract = _create_primary_contract(
                seed,
                closing_date=date(2026, 9, 15),
                sales_price=500000,
            )
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'closing_date': '2026-10-01',
                'sales_price': '525000',
                'option_fee': '400',
            })
            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )

            result = amendment_service.accept(
                amendment,
                actor_id=seed['owner_a'],
                selected_keys=['closing_date'],
            )
            db.session.flush()

            assert 'closing_date' in result['applied_keys']
            assert 'sales_price' not in result['applied_keys']
            assert amendment.status == 'accepted'

            db.session.refresh(contract)
            assert contract.closing_date == date(2026, 10, 1)
            assert contract.accepted_price == 500000
            assert (contract.frozen_terms or {}).get('sales_price') == '500000'
            assert (contract.frozen_terms or {}).get('option_fee') == '250'

            tx = Transaction.query.filter_by(
                id=seed['tx_a'],
                organization_id=seed['org_a'],
            ).first()
            assert tx.expected_close_date == date(2026, 10, 1)
        finally:
            tx = Transaction.query.get(seed['tx_a'])
            tx.expected_close_date = None
            db.session.commit()
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_accept_supersedes_open_requirement_skips_completed(app, seed):
    with app.app_context():
        prior_status = None
        try:
            tx = Transaction.query.filter_by(
                id=seed['tx_a'],
                organization_id=seed['org_a'],
            ).first()
            prior_status = tx.status
            tx.status = 'under_contract'

            contract = _create_primary_contract(
                seed,
                closing_date=date(2026, 9, 15),
            )

            open_req = TransactionRequirement(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                package_key='seller_ctc',
                phase_key='closing',
                requirement_key='final_walkthrough',
                title='Final Walkthrough',
                work_status='pending',
                due_at=datetime(2026, 9, 14, 0, 0, 0),
            )
            done_req = TransactionRequirement(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                package_key='seller_ctc',
                phase_key='closing',
                requirement_key='closing',
                title='Closing',
                work_status='completed',
                due_at=datetime(2026, 9, 15, 0, 0, 0),
            )
            db.session.add_all([open_req, done_req])
            db.session.flush()
            open_id = open_req.id
            done_id = done_req.id
            done_due = done_req.due_at

            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'closing_date': '2026-10-20',
            })
            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            amendment_service.accept(amendment, actor_id=seed['owner_a'])
            db.session.flush()

            open_req = db.session.get(TransactionRequirement, open_id)
            done_req = db.session.get(TransactionRequirement, done_id)

            assert open_req.prior_due_at == datetime(2026, 9, 14, 0, 0, 0)
            assert open_req.due_at == datetime(2026, 10, 19, 0, 0, 0)
            assert done_req.due_at == done_due
            assert done_req.prior_due_at is None
        finally:
            tx = Transaction.query.get(seed['tx_a'])
            if prior_status is not None:
                tx.status = prior_status
            tx.expected_close_date = None
            db.session.commit()
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_accept_raises_on_already_accepted(app, seed):
    with app.app_context():
        try:
            contract = _create_primary_contract(seed)
            amendment = SellerContractAmendment(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                accepted_contract_id=contract.id,
                created_by_id=seed['owner_a'],
                status='accepted',
                amendment_type='amendment',
            )
            db.session.add(amendment)
            db.session.flush()

            with pytest.raises(ValueError, match='already accepted'):
                amendment_service.accept(amendment, actor_id=seed['owner_a'])
        finally:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_reject_sets_statuses_and_writes_audit(app, seed):
    with app.app_context():
        try:
            contract = _create_primary_contract(seed)
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                'closing_date': '2026-10-01',
            })
            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            amendment_service.reject(
                amendment,
                actor_id=seed['owner_a'],
                reason='Seller declined closing extension',
            )
            db.session.flush()

            version = amendment_service.current_version(amendment)
            assert amendment.status == 'rejected'
            assert version.status == 'declined'
            assert version.reviewed_by_id == seed['owner_a']
            assert version.reviewed_at is not None

            audit = AuditEvent.query.filter_by(
                event_type='amendment_rejected',
                transaction_id=seed['tx_a'],
                organization_id=seed['org_a'],
            ).order_by(AuditEvent.id.desc()).first()
            assert audit is not None
            assert audit.event_data['amendment_id'] == amendment.id
            assert audit.event_data['reason'] == 'Seller declined closing extension'
        finally:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])


def test_list_for_transaction_filters_by_organization(app, seed):
    with app.app_context():
        try:
            contract_a = _create_primary_contract(seed)
            contract_b = SellerAcceptedContract(
                organization_id=seed['org_b'],
                transaction_id=seed['tx_b'],
                created_by_id=seed['owner_b'],
                position='primary',
                status='active',
                accepted_price=400000,
                closing_date=date(2026, 11, 1),
                frozen_terms={},
            )
            db.session.add(contract_b)
            db.session.flush()

            amd_a = SellerContractAmendment(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                accepted_contract_id=contract_a.id,
                created_by_id=seed['owner_a'],
                status='received',
            )
            # Same transaction_id would be wrong; craft a row that would leak
            # if organization_id were ignored by using org_b on tx_b and
            # asserting org_a list never includes it.
            amd_b = SellerContractAmendment(
                organization_id=seed['org_b'],
                transaction_id=seed['tx_b'],
                accepted_contract_id=contract_b.id,
                created_by_id=seed['owner_b'],
                status='received',
            )
            db.session.add_all([amd_a, amd_b])
            db.session.flush()

            listed = amendment_service.list_for_transaction(
                seed['tx_a'], seed['org_a'],
            )
            assert [row.id for row in listed] == [amd_a.id]
            assert all(row.organization_id == seed['org_a'] for row in listed)
        finally:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])
            _cleanup_amendments(seed['tx_b'], seed['org_b'])


def test_create_from_document_maps_new_prefixed_extraction_fields(app, seed):
    """The amendment schema emits `new_*` terms; they win over the plain values,
    which on an amendment usually restate the original contract."""
    with app.app_context():
        try:
            _create_primary_contract(seed)
            doc = _create_document(seed, {
                'document_classification': 'amendment',
                # Plain keys here restate the ORIGINAL contract terms.
                'closing_date': '2026-09-15',
                'sales_price': '500000',
                'option_fee': '250',
                # `new_*` keys carry what the amendment actually changes.
                'new_closing_date': '2026-10-20',
                'new_sales_price': '515000',
                'new_option_period_days': 10,
                'new_option_fee': '400',
                'new_earnest_money': '9000',
            })

            amendment = amendment_service.create_from_document(
                doc, actor_id=seed['owner_a'],
            )
            db.session.flush()

            terms = amendment_service.current_version(amendment).terms_data
            assert terms['closing_date'] == '2026-10-20'
            assert terms['sales_price'] == '515000'
            assert terms['option_period_days'] == 10
            assert terms['option_fee'] == '400'
            assert terms['earnest_money'] == '9000'
            assert 'new_closing_date' not in terms

            changed = {
                entry['key']
                for entry in amendment_service.diff_against_contract(amendment)
                if entry['changed']
            }
            assert changed == {
                'closing_date', 'sales_price', 'option_period_days',
                'option_fee', 'earnest_money',
            }
        finally:
            _cleanup_amendments(seed['tx_a'], seed['org_a'])
