"""Primary offer acceptance sets close date and seeds seller_ctc idempotently."""

from datetime import date, datetime
from decimal import Decimal

from models import (
    SellerAcceptedContract,
    SellerOffer,
    SellerOfferVersion,
    Transaction,
    TransactionRequirement,
    TransactionType,
    db,
)
from services.controlling_contracts import create_baseline_from_accepted_offer
from services.seller_workflow import seed_ctc_requirements_from_accepted_contract


def _seller_tx(org_id, user_id, address='800 Acceptance Way'):
    tx_type = TransactionType.query.filter_by(
        organization_id=org_id, name='seller',
    ).first()
    if tx_type is None:
        tx_type = TransactionType(
            organization_id=org_id,
            name='seller',
            display_name='Seller',
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
        status='active',
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def test_seed_seller_ctc_from_accepted_contract_idempotent(app, seed):
    with app.app_context():
        tx = _seller_tx(seed['org_a'], seed['owner_a'])
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            status='accepted_primary',
            offer_price=Decimal('500000'),
            received_at=datetime.utcnow(),
            creation_source='test',
        )
        db.session.add(offer)
        db.session.flush()
        version = SellerOfferVersion(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            offer_id=offer.id,
            created_by_id=seed['owner_a'],
            version_number=1,
            direction='buyer_offer',
            status='submitted',
            submitted_at=datetime.utcnow(),
            terms_data={},
        )
        db.session.add(version)
        db.session.flush()
        contract = SellerAcceptedContract(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            offer_id=offer.id,
            accepted_version_id=version.id,
            created_by_id=seed['owner_a'],
            position='primary',
            status='active',
            accepted_price=Decimal('500000'),
            effective_date=date(2026, 8, 1),
            closing_date=date(2026, 9, 15),
            option_period_days=7,
            frozen_terms={},
        )
        db.session.add(contract)
        db.session.flush()

        first = seed_ctc_requirements_from_accepted_contract(
            transaction=tx,
            accepted_contract=contract,
            actor_id=seed['owner_a'],
        )
        assert first['created'] > 0
        keys_after_first = {
            r.requirement_key
            for r in TransactionRequirement.query.filter_by(
                transaction_id=tx.id,
                organization_id=seed['org_a'],
            ).all()
        }
        assert keys_after_first
        assert all(
            r.package_key == 'seller_ctc'
            for r in TransactionRequirement.query.filter_by(transaction_id=tx.id).all()
        )

        second = seed_ctc_requirements_from_accepted_contract(
            transaction=tx,
            accepted_contract=contract,
            actor_id=seed['owner_a'],
        )
        assert second['created'] == 0
        assert second['skipped'] >= first['created']
        assert TransactionRequirement.query.filter_by(
            transaction_id=tx.id,
        ).count() == len(keys_after_first)
        db.session.rollback()


def test_accept_primary_offer_sets_expected_close_and_seeds_ctc(app, seed, owner_a_client):
    with app.app_context():
        tx = _seller_tx(seed['org_a'], seed['owner_a'], '801 Close Date Lane')
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            status='needs_review',
            offer_price=Decimal('480000'),
            proposed_close_date=date(2026, 10, 20),
            option_period_days=10,
            received_at=datetime.utcnow(),
            creation_source='test',
        )
        db.session.add(offer)
        db.session.flush()
        version = SellerOfferVersion(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            offer_id=offer.id,
            created_by_id=seed['owner_a'],
            version_number=1,
            direction='buyer_offer',
            status='submitted',
            submitted_at=datetime.utcnow(),
            terms_data={
                'effective_date': '2026-08-05',
                'proposed_close_date': '2026-10-20',
                'option_period_days': 10,
            },
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id
        db.session.commit()
        tx_id = tx.id
        offer_id = offer.id

    response = owner_a_client.post(
        f'/transactions/{tx_id}/offers/{offer_id}/accept',
        json={'position': 'primary', 'effective_date': '2026-08-05'},
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body['success'] is True

    with app.app_context():
        tx = Transaction.query.get(tx_id)
        assert tx.status == 'under_contract'
        assert tx.expected_close_date == date(2026, 10, 20)
        contract = SellerAcceptedContract.query.filter_by(
            transaction_id=tx_id,
            offer_id=offer_id,
            position='primary',
            status='active',
        ).one()
        assert contract.accepted_version_id is not None
        assert (contract.extra_data or {}).get('created_via') == (
            'controlling_contracts.create_baseline_from_accepted_offer'
        )
        reqs = TransactionRequirement.query.filter_by(
            transaction_id=tx_id,
            organization_id=seed['org_a'],
        ).all()
        assert reqs
        assert {r.package_key for r in reqs} == {'seller_ctc'}
        db.session.rollback()


def test_accept_manual_offer_without_pdf_creates_baseline(app, seed):
    with app.app_context():
        tx = _seller_tx(seed['org_a'], seed['owner_a'], '802 Manual Offer')
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            status='needs_review',
            offer_price=Decimal('450000'),
            proposed_close_date=date(2026, 11, 1),
            received_at=datetime.utcnow(),
            creation_source='manual',
        )
        db.session.add(offer)
        db.session.flush()
        version = SellerOfferVersion(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            offer_id=offer.id,
            created_by_id=seed['owner_a'],
            version_number=1,
            direction='buyer_offer',
            status='submitted',
            submitted_at=datetime.utcnow(),
            terms_data={'proposed_close_date': '2026-11-01'},
            transaction_document_id=None,
        )
        db.session.add(version)
        db.session.flush()
        offer.current_version_id = version.id

        contract = create_baseline_from_accepted_offer(
            transaction=tx,
            offer=offer,
            actor_id=seed['owner_a'],
            position='primary',
            version=version,
            effective_date=date(2026, 8, 5),
        )
        assert contract.offer_id == offer.id
        assert contract.accepted_version_id == version.id
        assert contract.position == 'primary'
        assert tx.status == 'under_contract'
        assert tx.expected_close_date == date(2026, 11, 1)
        assert offer.status == 'accepted_primary'
        db.session.rollback()


def test_accept_backup_requires_primary_and_preserves_status(app, seed):
    with app.app_context():
        tx = _seller_tx(seed['org_a'], seed['owner_a'], '803 Backup Offer')
        primary_offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            status='needs_review',
            offer_price=Decimal('500000'),
            proposed_close_date=date(2026, 12, 1),
            received_at=datetime.utcnow(),
            creation_source='test',
        )
        backup_offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            status='needs_review',
            offer_price=Decimal('490000'),
            proposed_close_date=date(2026, 12, 15),
            received_at=datetime.utcnow(),
            creation_source='test',
        )
        db.session.add_all([primary_offer, backup_offer])
        db.session.flush()

        try:
            create_baseline_from_accepted_offer(
                transaction=tx,
                offer=backup_offer,
                actor_id=seed['owner_a'],
                position='backup',
            )
            assert False, 'backup without primary should fail'
        except ValueError as exc:
            assert 'primary' in str(exc).lower()

        create_baseline_from_accepted_offer(
            transaction=tx,
            offer=primary_offer,
            actor_id=seed['owner_a'],
            position='primary',
            effective_date=date(2026, 8, 5),
        )
        assert tx.status == 'under_contract'
        primary_close = tx.expected_close_date

        backup = create_baseline_from_accepted_offer(
            transaction=tx,
            offer=backup_offer,
            actor_id=seed['owner_a'],
            position='backup',
            backup_position=1,
        )
        assert backup.position == 'backup'
        assert backup_offer.status == 'accepted_backup'
        assert tx.status == 'under_contract'
        assert tx.expected_close_date == primary_close
        assert SellerAcceptedContract.query.filter_by(
            transaction_id=tx.id, position='primary', status='active',
        ).count() == 1
        db.session.rollback()


def test_accept_duplicate_offer_rejected(app, seed):
    with app.app_context():
        tx = _seller_tx(seed['org_a'], seed['owner_a'], '804 Dup Accept')
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            status='needs_review',
            offer_price=Decimal('410000'),
            proposed_close_date=date(2026, 9, 1),
            received_at=datetime.utcnow(),
            creation_source='test',
        )
        db.session.add(offer)
        db.session.flush()
        create_baseline_from_accepted_offer(
            transaction=tx,
            offer=offer,
            actor_id=seed['owner_a'],
            position='primary',
            effective_date=date(2026, 8, 1),
        )
        try:
            create_baseline_from_accepted_offer(
                transaction=tx,
                offer=offer,
                actor_id=seed['owner_a'],
                position='primary',
            )
            assert False, 'duplicate acceptance should fail'
        except ValueError as exc:
            assert 'already' in str(exc).lower()
        assert SellerAcceptedContract.query.filter_by(
            transaction_id=tx.id, status='active',
        ).count() == 1
        db.session.rollback()


def test_accept_coerces_formatted_money_strings_including_zero(app, seed):
    """Display-formatted terms ($0) must not blow up Numeric columns on accept."""
    with app.app_context():
        tx = _seller_tx(seed['org_a'], seed['owner_a'], '805 Money Format')
        offer = SellerOffer(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            created_by_id=seed['owner_a'],
            status='needs_review',
            offer_price=Decimal('440000'),
            cash_down_payment=Decimal('88000'),
            financing_amount=Decimal('352000'),
            buyer_agent_commission_percent=Decimal('3'),
            buyer_agent_commission_flat=Decimal('0'),
            seller_concessions_amount=None,
            proposed_close_date=date(2026, 8, 17),
            received_at=datetime.utcnow(),
            creation_source='test',
            terms_summary={
                'offer_price': '$440,000',
                'buyer_agent_commission_flat': '$0',
                'seller_concessions_amount': '$0',
            },
        )
        db.session.add(offer)
        db.session.flush()

        contract = create_baseline_from_accepted_offer(
            transaction=tx,
            offer=offer,
            actor_id=seed['owner_a'],
            position='primary',
            effective_date=date(2026, 8, 6),
        )
        db.session.flush()

        assert contract.accepted_price == Decimal('440000')
        assert contract.buyer_agent_commission_flat == Decimal('0')
        assert contract.seller_concessions_amount == Decimal('0')
        assert contract.buyer_agent_commission_percent == Decimal('3')
        db.session.rollback()
