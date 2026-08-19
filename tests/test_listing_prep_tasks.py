"""Listing-prep due dates create, update, and close linked tasks."""
from __future__ import annotations

from datetime import datetime

from models import (
    Task,
    Transaction,
    TransactionParticipant,
    TransactionRequirement,
    TransactionRequirementEvent,
    TransactionType,
    db,
)
from services.listing_prep_checklist import (
    create_custom_listing_item,
    delete_custom_listing_item,
    listing_prep_groups,
    seed_listing_prep_checklist,
)
from services.requirements_service import RequirementsService


def _seller_tx(seed, address='410 Date Task Ln'):
    tx_type = TransactionType.query.filter_by(
        organization_id=seed['org_a'], name='seller',
    ).first()
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=tx_type.id,
        street_address=address,
        city='Houston',
        state='TX',
        status='preparing_to_list',
    )
    db.session.add(tx)
    db.session.flush()
    db.session.add(TransactionParticipant(
        organization_id=seed['org_a'],
        transaction_id=tx.id,
        contact_id=seed['contact_a'],
        role='seller',
        is_primary=True,
    ))
    seed_listing_prep_checklist(tx, seed['org_a'], actor_id=seed['owner_a'])
    db.session.flush()
    return tx


def _cleanup(org_id, tx_id):
    req_ids = [
        r.id for r in TransactionRequirement.query.filter_by(
            organization_id=org_id, transaction_id=tx_id,
        ).all()
    ]
    if req_ids:
        TransactionRequirementEvent.query.filter(
            TransactionRequirementEvent.requirement_id.in_(req_ids),
        ).delete(synchronize_session=False)
        TransactionRequirement.query.filter(
            TransactionRequirement.id.in_(req_ids),
        ).delete(synchronize_session=False)
    Task.query.filter_by(
        organization_id=org_id, transaction_id=tx_id,
    ).delete(synchronize_session=False)
    TransactionParticipant.query.filter_by(transaction_id=tx_id).delete(
        synchronize_session=False,
    )
    Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
    db.session.commit()


def _photo_req(tx_id):
    return TransactionRequirement.query.filter_by(
        transaction_id=tx_id,
        requirement_key='schedule_photography',
    ).one()


def test_due_date_creates_and_updates_task(app, seed):
    tx_id = None
    with app.app_context():
        try:
            tx = _seller_tx(seed)
            tx_id = tx.id
            req = _photo_req(tx_id)
            assert req.task_id is None

            first = datetime(2026, 9, 1)
            RequirementsService.update_due_at(
                req.id, first, actor_id=seed['owner_a'], manual=True,
            )
            db.session.commit()
            req = db.session.get(TransactionRequirement, req.id)
            task = db.session.get(Task, req.task_id)
            assert task is not None
            assert task.status == 'pending'
            assert task.transaction_id == tx_id
            assert task.contact_id == seed['contact_a']
            assert task.subject == req.title
            assert task.due_date.date() == first.date()

            second = datetime(2026, 9, 15)
            RequirementsService.update_due_at(
                req.id, second, actor_id=seed['owner_a'], manual=True,
            )
            db.session.commit()
            task = db.session.get(Task, req.task_id)
            assert task.due_date.date() == second.date()
            assert Task.query.filter_by(transaction_id=tx_id).count() == 1
        finally:
            if tx_id:
                _cleanup(seed['org_a'], tx_id)


def test_complete_closes_task_and_clear_cancels(app, seed):
    tx_id = None
    with app.app_context():
        try:
            tx = _seller_tx(seed, address='411 Close Task Ln')
            tx_id = tx.id
            req = _photo_req(tx_id)
            RequirementsService.update_due_at(
                req.id, datetime(2026, 10, 1), actor_id=seed['owner_a'], manual=True,
            )
            db.session.commit()
            task_id = req.task_id

            RequirementsService.update_work_status(
                req.id, 'completed', actor_id=seed['owner_a'],
            )
            db.session.commit()
            task = db.session.get(Task, task_id)
            assert task.status == 'completed'
            assert task.completed_at is not None

            RequirementsService.update_work_status(
                req.id, 'pending', actor_id=seed['owner_a'],
            )
            db.session.commit()
            task = db.session.get(Task, task_id)
            assert task.status == 'pending'

            RequirementsService.update_due_at(
                req.id, None, actor_id=seed['owner_a'], manual=True,
            )
            db.session.commit()
            req = db.session.get(TransactionRequirement, req.id)
            task = db.session.get(Task, task_id)
            assert req.due_at is None
            assert req.task_id == task_id
            assert task.status == 'cancelled'
        finally:
            if tx_id:
                _cleanup(seed['org_a'], tx_id)


def test_custom_item_with_and_without_date(app, seed):
    tx_id = None
    with app.app_context():
        try:
            tx = _seller_tx(seed, address='412 Custom Item Ln')
            tx_id = tx.id
            undated = create_custom_listing_item(
                tx, 'Call the photographer', actor_id=seed['owner_a'],
            )
            dated = create_custom_listing_item(
                tx, 'Order lockbox',
                due_at=datetime(2026, 11, 2),
                actor_id=seed['owner_a'],
            )
            db.session.commit()

            assert undated.task_id is None
            assert dated.task_id is not None
            task = db.session.get(Task, dated.task_id)
            assert task.subject == 'Order lockbox'
            assert task.due_date.date().isoformat() == '2026-11-02'

            groups = listing_prep_groups(tx)
            labels = [group['label'] for group in groups]
            assert 'Your items' in labels
            custom = next(group for group in groups if group['key'] == 'custom')
            titles = [row['title'] for row in custom['rows']]
            assert titles == ['Call the photographer', 'Order lockbox']

            delete_custom_listing_item(dated, actor_id=seed['owner_a'])
            db.session.commit()
            task = db.session.get(Task, task.id)
            assert task.status == 'cancelled'
            assert db.session.get(TransactionRequirement, dated.id) is None
        finally:
            if tx_id:
                _cleanup(seed['org_a'], tx_id)


def test_http_add_date_and_custom_item(app, seed, owner_a_client):
    tx_id = None
    with app.app_context():
        tx = _seller_tx(seed, address='413 Http Checklist Ln')
        tx_id = tx.id
        req_id = _photo_req(tx_id).id
        db.session.commit()

    try:
        res = owner_a_client.post(
            f'/transactions/{tx_id}/requirements/{req_id}/due-date',
            json={'due_date': '2026-12-01'},
        )
        assert res.status_code == 200
        due_body = res.get_json()
        assert due_body['success'] is True
        assert due_body['due_at']
        assert due_body['task_id']
        assert due_body['task']
        assert due_body['task']['id'] == due_body['task_id']
        assert due_body['task']['status'] == 'pending'
        assert due_body['task']['subject']
        assert due_body['task']['url']
        assert str(due_body['due_at']).startswith('2026-12-01')

        res = owner_a_client.post(
            f'/transactions/{tx_id}/requirements',
            json={'title': 'Measure the lot', 'due_date': '2026-12-08'},
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True
        assert body['task_id']
        assert body['requirement_id']
        assert body['title'] == 'Measure the lot'
        assert body['task']
        assert body['task']['id'] == body['task_id']
        assert body['task']['subject'] == 'Measure the lot'
        assert str(body['due_at']).startswith('2026-12-08')

        page = owner_a_client.get(f'/transactions/{tx_id}')
        html = page.get_data(as_text=True)
        assert 'Measure the lot' in html
        assert 'Your items' in html
        assert 'Add date' in html

        with app.app_context():
            req = db.session.get(TransactionRequirement, req_id)
            linked = db.session.get(Task, req.task_id)
            custom = db.session.get(Task, body['task_id'])
            assert linked.contact_id == seed['contact_a']
            assert custom.contact_id == seed['contact_a']

        page = owner_a_client.get(f'/tasks/{due_body["task_id"]}')
        assert page.status_code == 200
        assert b'Jane Doe' in page.data
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)


def test_task_view_without_contact_does_not_crash(app, seed, owner_a_client):
    tx_id = None
    task_id = None
    with app.app_context():
        tx = _seller_tx(seed, address='415 Orphan Task Ln')
        tx_id = tx.id
        task = Task(
            organization_id=seed['org_a'],
            contact_id=None,
            transaction_id=tx.id,
            assigned_to_id=seed['owner_a'],
            created_by_id=seed['owner_a'],
            type_id=seed['task_type_a'],
            subtype_id=seed['subtype_a'],
            subject='Orphan checklist task',
            priority='medium',
            status='pending',
            due_date=datetime(2026, 12, 20),
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id

    try:
        page = owner_a_client.get(f'/tasks/{task_id}')
        assert page.status_code == 200
        assert b'Orphan checklist task' in page.data
        assert b'No related contact' in page.data
    finally:
        with app.app_context():
            _cleanup(seed['org_a'], tx_id)


def test_delete_transaction_with_checklist_tasks(app, seed, owner_a_client):
    tx_id = None
    kept_task_id = None
    orphan_id = None
    with app.app_context():
        tx = _seller_tx(seed, address='416 Delete Task Ln')
        tx_id = tx.id
        req = _photo_req(tx_id)
        RequirementsService.update_due_at(
            req.id, datetime(2026, 12, 22), actor_id=seed['owner_a'], manual=True,
        )
        orphan = Task(
            organization_id=seed['org_a'],
            contact_id=None,
            transaction_id=tx.id,
            assigned_to_id=seed['owner_a'],
            created_by_id=seed['owner_a'],
            type_id=seed['task_type_a'],
            subtype_id=seed['subtype_a'],
            subject='Orphan delete task',
            priority='medium',
            status='pending',
            due_date=datetime(2026, 12, 23),
        )
        db.session.add(orphan)
        db.session.commit()
        kept_task_id = req.task_id
        orphan_id = orphan.id

    try:
        resp = owner_a_client.post(
            f'/transactions/{tx_id}/delete',
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert b'Error deleting transaction' not in (resp.data or b'')

        with app.app_context():
            assert db.session.get(Transaction, tx_id) is None
            kept = db.session.get(Task, kept_task_id)
            assert kept is not None
            assert kept.contact_id == seed['contact_a']
            assert kept.transaction_id is None
            assert db.session.get(Task, orphan_id) is None
            tx_id = None
    finally:
        with app.app_context():
            if kept_task_id:
                leftover = db.session.get(Task, kept_task_id)
                if leftover:
                    db.session.delete(leftover)
                    db.session.commit()
            if tx_id:
                _cleanup(seed['org_a'], tx_id)
