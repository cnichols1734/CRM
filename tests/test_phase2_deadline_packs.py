"""Phase 2 deadline packs + pack picker by side/status."""

from types import SimpleNamespace

from services.deadline_rules import DeadlineRulesService


def test_load_buyer_and_listing_packs():
    buyer = DeadlineRulesService.load_pack('buyer_ctc', 'v1')
    assert buyer['pack_key'] == 'buyer_ctc'
    assert 'earnest_money' in buyer['requirements']
    assert 'clear_to_close' in buyer['requirements']

    listing = DeadlineRulesService.load_pack('listing', 'v1')
    assert listing['pack_key'] == 'listing'
    assert 'mls_input_attested' in listing['requirements']
    assert 'listing_docs_complete' in listing['requirements']
    assert 'listing_description' in listing['requirements']
    assert 'confirm_property_details' in listing['requirements']
    assert 'photos_ready' in listing['requirements']
    assert listing['requirements']['photos_ready'].get('hidden') is True
    assert listing['requirements']['offer_intake_ready'].get('hidden') is True


def test_resolve_pack_by_side_and_status():
    DeadlineRulesService._packs_cache.clear()

    buyer_uc = SimpleNamespace(
        status='under_contract',
        transaction_type=SimpleNamespace(name='buyer'),
    )
    key, pack = DeadlineRulesService.resolve_pack_for_transaction(buyer_uc)
    assert key == 'buyer_ctc'
    assert pack['pack_key'] == 'buyer_ctc'

    seller_prep = SimpleNamespace(
        status='preparing_to_list',
        transaction_type=SimpleNamespace(name='seller'),
    )
    key, pack = DeadlineRulesService.resolve_pack_for_transaction(seller_prep)
    assert key == 'listing'

    seller_uc = SimpleNamespace(
        status='under_contract',
        transaction_type=SimpleNamespace(name='seller'),
    )
    key, pack = DeadlineRulesService.resolve_pack_for_transaction(seller_uc)
    assert key == 'seller_ctc'


def test_active_contract_outranks_listing_status(app, seed):
    """A seller left on 'active' after going under contract still gets the CTC pack."""
    from datetime import date

    from models import SellerAcceptedContract, Transaction, db

    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        prior_status = tx.status
        try:
            # Other tests in the suite may leave a contract on this transaction.
            SellerAcceptedContract.query.filter_by(
                transaction_id=tx.id, organization_id=tx.organization_id,
            ).delete(synchronize_session=False)
            tx.status = 'active'
            db.session.flush()
            key, _ = DeadlineRulesService.resolve_pack_for_transaction(
                tx, side_hint='seller',
            )
            assert key == 'listing'

            contract = SellerAcceptedContract(
                organization_id=tx.organization_id,
                transaction_id=tx.id,
                created_by_id=seed['owner_a'],
                position='primary',
                status='active',
                effective_date=date(2026, 7, 1),
                closing_date=date(2026, 8, 15),
            )
            db.session.add(contract)
            db.session.flush()

            key, _ = DeadlineRulesService.resolve_pack_for_transaction(
                tx, side_hint='seller',
            )
            assert key == 'seller_ctc'
        finally:
            SellerAcceptedContract.query.filter_by(
                transaction_id=seed['tx_a'],
                organization_id=seed['org_a'],
            ).delete(synchronize_session=False)
            tx.status = prior_status
            db.session.commit()


def test_reminder_cadence_prefs(app, seed):
    from models import User, db
    from services.reminder_scheduler import ReminderScheduler

    with app.app_context():
        user = User.query.get(seed['owner_a'])
        prior = user.notification_prefs
        try:
            user.notification_prefs = {
                'cadence': {'enabled_windows': ['t1', 'due_today', 'overdue']},
            }
            db.session.commit()

            assert ReminderScheduler.user_wants_window(user.id, 't1') is True
            assert ReminderScheduler.user_wants_window(user.id, 't7') is False
            # Critical windows always pass
            assert ReminderScheduler.user_wants_window(user.id, 'overdue') is True
            assert ReminderScheduler.user_wants_window(user.id, 'due_today') is True
        finally:
            # Session-scoped seed — restore so later reminder tests stay clean.
            user.notification_prefs = prior
            db.session.commit()
