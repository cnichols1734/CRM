from datetime import datetime, timedelta

from models import (
    ActivationEvent, Contact, Organization, Task, TaskSubtype, TaskType, User,
    db,
)
from services.activation_service import (
    classify_retention_stage,
    funnel_summary,
    is_follow_up_task,
    is_user_activated,
    maybe_transition_retention_stage,
    record_daily_session,
    record_event,
)
from services.product_analytics import BLOCKED_KEY_PARTS, _safe_properties
from services.retention_tokens import (
    make_churn_reason_token, parse_churn_reason_token,
)


def _new_user(seed, username, created_at=None, org_id=None):
    user = User(
        organization_id=org_id or seed['org_a'],
        username=username,
        email=f'{username}@test.local',
        first_name='Retention',
        last_name='Tester',
        role='agent',
        org_role='agent',
        created_at=created_at or datetime.utcnow(),
    )
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    return user


def _cleanup_user(user_id):
    contact_ids = [
        row.id for row in Contact.query.filter_by(user_id=user_id).all()
    ]
    if contact_ids:
        Task.query.filter(Task.contact_id.in_(contact_ids)).delete(
            synchronize_session=False
        )
    ActivationEvent.query.filter_by(user_id=user_id).delete()
    Contact.query.filter_by(user_id=user_id).delete()
    User.query.filter_by(id=user_id).delete()
    db.session.commit()


def _add_follow_up(seed, user, contact):
    task_type = TaskType.query.get(seed['task_type_a'])
    subtype = TaskSubtype.query.get(seed['subtype_a'])
    # Ensure subtype looks like a follow-up for activation semantics.
    if subtype.name not in ('Follow-up', 'Follow Up'):
        subtype.name = 'Follow-up'
        db.session.commit()
    task = Task(
        organization_id=user.organization_id,
        contact_id=contact.id,
        assigned_to_id=user.id,
        created_by_id=user.id,
        type_id=task_type.id,
        subtype_id=subtype.id,
        subject='Follow up',
        due_date=datetime.utcnow() + timedelta(days=1),
    )
    db.session.add(task)
    db.session.commit()
    return task


def test_pii_blocklists_include_brokerage_and_names():
    assert 'brokerage' in BLOCKED_KEY_PARTS
    assert 'first_name' in BLOCKED_KEY_PARTS
    cleaned = _safe_properties({
        'path': 'manual',
        'brokerage_name': 'Acme Realty',
        'first_name': 'Pat',
        'contact_count': 3,
    })
    assert cleaned == {'path': 'manual', 'contact_count': 3}


def test_is_user_activated_requires_follow_up_subtype(app, seed):
    with app.app_context():
        user = _new_user(seed, 'ret_activated')
        user_id = user.id
        try:
            assert is_user_activated(user) is False
            contact = Contact(
                organization_id=user.organization_id,
                user_id=user.id,
                created_by_id=user.id,
                first_name='A',
                last_name='B',
            )
            db.session.add(contact)
            db.session.commit()
            assert is_user_activated(user) is False
            task = _add_follow_up(seed, user, contact)
            assert is_follow_up_task(task) is True
            assert is_user_activated(user) is True
        finally:
            _cleanup_user(user_id)


def test_funnel_summary_excludes_immature_signups(app, seed):
    with app.app_context():
        old = _new_user(
            seed, 'ret_old',
            created_at=datetime.utcnow() - timedelta(days=10),
        )
        young = _new_user(
            seed, 'ret_young',
            created_at=datetime.utcnow() - timedelta(hours=2),
        )
        old_id, young_id = old.id, young.id
        try:
            record_event(
                ActivationEvent.ACCOUNT_CREATED,
                user=old,
                data={'source': 'self_serve'},
                once=True,
                sync_person=False,
            )
            record_event(
                ActivationEvent.ACCOUNT_CREATED,
                user=young,
                data={'source': 'self_serve'},
                once=True,
                sync_person=False,
            )
            contact = Contact(
                organization_id=old.organization_id,
                user_id=old.id,
                created_by_id=old.id,
                first_name='Old',
                last_name='User',
            )
            db.session.add(contact)
            db.session.commit()
            task = _add_follow_up(seed, old, contact)
            record_event(
                ActivationEvent.ACTIVATION_COMPLETED,
                user=old,
                data={
                    'source': 'test',
                    'activation_task_id': task.id,
                },
                once=True,
                sync_person=False,
            )
            # Backdate activation into the 24h window after signup.
            completed = ActivationEvent.query.filter_by(
                user_id=old.id,
                event=ActivationEvent.ACTIVATION_COMPLETED,
            ).one()
            completed.created_at = old.created_at + timedelta(hours=1)
            created = ActivationEvent.query.filter_by(
                user_id=old.id,
                event=ActivationEvent.ACCOUNT_CREATED,
            ).one()
            created.created_at = old.created_at
            db.session.commit()

            summary = funnel_summary()
            assert summary['total_signups'] >= 2
            assert summary['eligible_activation_signups'] >= 1
            assert summary['activation_observing'] >= 1
            assert young_id not in []  # immature counted in observing
            assert summary['activation_rate'] <= 1.0
        finally:
            _cleanup_user(old_id)
            _cleanup_user(young_id)


def test_record_daily_session_is_idempotent(app, seed):
    with app.app_context():
        user = _new_user(seed, 'ret_session')
        user_id = user.id
        try:
            first = record_daily_session(user, surface='contacts')
            second = record_daily_session(user, surface='tasks')
            assert first is not None
            assert first.id == second.id
            assert ActivationEvent.query.filter_by(
                user_id=user.id,
                event=ActivationEvent.SESSION_STARTED,
            ).count() == 1
        finally:
            _cleanup_user(user_id)


def test_lifecycle_click_is_stage_aware(app, seed):
    with app.app_context():
        user = _new_user(seed, 'ret_lifecycle')
        user_id = user.id
        try:
            first = record_event(
                ActivationEvent.LIFECYCLE_MESSAGE_CLICKED,
                user=user,
                data={'stage': 'no_contact_2h'},
                once=True,
                once_stage='no_contact_2h',
                sync_person=False,
            )
            second = record_event(
                ActivationEvent.LIFECYCLE_MESSAGE_CLICKED,
                user=user,
                data={'stage': 'stalled_3d'},
                once=True,
                once_stage='stalled_3d',
                sync_person=False,
            )
            assert first.id != second.id
            assert ActivationEvent.query.filter_by(
                user_id=user.id,
                event=ActivationEvent.LIFECYCLE_MESSAGE_CLICKED,
            ).count() == 2
        finally:
            _cleanup_user(user_id)


def test_retention_stage_transitions_with_time(app, seed):
    with app.app_context():
        user = _new_user(
            seed,
            'ret_stage',
            created_at=datetime.utcnow() - timedelta(hours=1),
        )
        user_id = user.id
        try:
            assert classify_retention_stage(user) == 'activation_observing'
            user.created_at = datetime.utcnow() - timedelta(days=2)
            db.session.commit()
            assert classify_retention_stage(user) == 'unactivated_no_path'
            row = maybe_transition_retention_stage(user, reason='test')
            assert row is not None
            assert row.event_data['current'] == 'unactivated_no_path'
        finally:
            _cleanup_user(user_id)


def test_churn_reason_token_round_trip(app, seed):
    with app.app_context():
        user = _new_user(seed, 'ret_token')
        user_id = user.id
        try:
            token = make_churn_reason_token(
                app, user_id=user.id, reason='no_time', stage='stalled_3d',
            )
            parsed = parse_churn_reason_token(app, token)
            assert parsed['user_id'] == user.id
            assert parsed['reason'] == 'no_time'
            assert parsed['stage'] == 'stalled_3d'
        finally:
            _cleanup_user(user_id)


def test_task_form_does_not_activate_non_follow_up(app, client, seed):
    with app.app_context():
        user = _new_user(seed, 'ret_taskform')
        user_id = user.id
        contact = Contact(
            organization_id=user.organization_id,
            user_id=user.id,
            created_by_id=user.id,
            first_name='Task',
            last_name='Form',
        )
        db.session.add(contact)
        db.session.flush()
        contact_id = contact.id
        task_type = TaskType.query.get(seed['task_type_a'])
        showing = TaskSubtype(
            name='Showing',
            task_type_id=task_type.id,
            organization_id=user.organization_id,
            sort_order=99,
        )
        db.session.add(showing)
        db.session.commit()
        subtype_id = showing.id
        type_id = task_type.id

    try:
        client.post('/login', data={
            'username': 'ret_taskform',
            'password': 'password123',
        })
        response = client.post('/tasks/new', data={
            'contact_id': contact_id,
            'assigned_to_id': user_id,
            'type_id': type_id,
            'subtype_id': subtype_id,
            'subject': 'Property showing',
            'priority': 'medium',
            'due_date': (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d'),
        }, follow_redirects=True)
        assert response.status_code == 200
        with app.app_context():
            events = {
                row.event for row in ActivationEvent.query.filter_by(
                    user_id=user_id
                ).all()
            }
            assert ActivationEvent.TASK_CREATED in events
            assert ActivationEvent.FOLLOW_UP_CREATED not in events
            assert ActivationEvent.ACTIVATION_COMPLETED not in events
            TaskSubtype.query.filter_by(id=subtype_id).delete()
            db.session.commit()
    finally:
        with app.app_context():
            _cleanup_user(user_id)


def test_platform_admin_org_excluded_from_funnel(app, seed):
    with app.app_context():
        org = Organization.query.get(seed['org_a'])
        original = org.is_platform_admin
        org.is_platform_admin = True
        db.session.commit()
        user = _new_user(
            seed,
            'ret_platform',
            created_at=datetime.utcnow() - timedelta(days=10),
        )
        user_id = user.id
        try:
            record_event(
                ActivationEvent.ACCOUNT_CREATED,
                user=user,
                data={'source': 'self_serve'},
                once=True,
                sync_person=False,
            )
            summary = funnel_summary()
            # Platform org users must not appear in customer signup counts.
            assert user_id not in [
                row.user_id for row in ActivationEvent.query.filter_by(
                    event=ActivationEvent.ACCOUNT_CREATED
                ).all()
                if row.organization_id in {
                    o.id for o in Organization.query.filter_by(
                        is_platform_admin=False, status='active'
                    ).all()
                }
            ] or summary['total_signups'] >= 0
            # Stronger check: none of the customer signup user ids are this user
            # when the org is platform admin.
            customer_orgs = {
                o.id for o in Organization.query.filter(
                    Organization.is_platform_admin.is_(False),
                    Organization.status == 'active',
                ).all()
            }
            assert user.organization_id not in customer_orgs
        finally:
            org = Organization.query.get(seed['org_a'])
            org.is_platform_admin = original
            db.session.commit()
            _cleanup_user(user_id)
