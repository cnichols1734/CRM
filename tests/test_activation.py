from datetime import datetime, timedelta

import pytz

from models import (
    ActivationEvent, Contact, Task, TaskSubtype, TaskType, User, db,
)
from services.activation_service import record_event
from services.product_analytics import _safe_properties


def _new_user(seed, username, created_at=None):
    user = User(
        organization_id=seed['org_a'],
        username=username,
        email=f'{username}@test.local',
        first_name='Activation',
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


def test_once_events_are_idempotent(app, seed):
    with app.app_context():
        user = User.query.get(seed['owner_a'])
        ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.FRICTION_RESPONSE,
        ).delete()
        db.session.commit()

        first = record_event(
            ActivationEvent.FRICTION_RESPONSE,
            user=user,
            data={'reason': 'no_time'},
            once=True,
        )
        second = record_event(
            ActivationEvent.FRICTION_RESPONSE,
            user=user,
            data={'reason': 'other'},
            once=True,
        )
        assert first.id == second.id
        assert ActivationEvent.query.filter_by(
            user_id=user.id,
            event=ActivationEvent.FRICTION_RESPONSE,
        ).count() == 1
        db.session.delete(first)
        db.session.commit()


def test_product_analytics_removes_pii_and_free_text():
    cleaned = _safe_properties({
        'path': 'manual',
        'contact_name': 'Jane Doe',
        'email': 'jane@example.com',
        'notes': 'private details',
        'contact_count': 2,
        'completed': True,
    })
    assert cleaned == {
        'path': 'manual',
        'contact_count': 2,
        'completed': True,
    }


def test_quick_activation_creates_contact_task_and_milestones(
        app, client, seed):
    with app.app_context():
        user = _new_user(seed, 'activation_quick')
        user_id = user.id

    try:
        client.post('/login', data={
            'username': 'activation_quick',
            'password': 'password123',
        })
        dashboard = client.get('/dashboard')
        assert dashboard.status_code == 200
        assert b'How do you want to start?' in dashboard.data
        assert b'welcomeOverlay' not in dashboard.data
        response = client.post('/contacts/quick-add', data={
            'name': 'Jordan Lee',
            'phone': '5125550199',
            'follow_up': 'tomorrow',
        })
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['task']['id']

        with app.app_context():
            contact = Contact.query.filter_by(user_id=user_id).one()
            task = Task.query.filter_by(contact_id=contact.id).one()
            assert task.due_date.date() == (
                datetime.now(pytz.timezone('America/Chicago')).date()
                + timedelta(days=1)
            )
            events = {
                row.event for row in ActivationEvent.query.filter_by(
                    user_id=user_id
                ).all()
            }
            assert ActivationEvent.CONTACT_CREATED in events
            assert ActivationEvent.FOLLOW_UP_CREATED in events
            assert ActivationEvent.ACTIVATION_COMPLETED in events
    finally:
        with app.app_context():
            _cleanup_user(user_id)


def test_baselined_user_does_not_see_activation_prompts(app, client, seed):
    with app.app_context():
        user = _new_user(
            seed,
            'activation_baselined',
            created_at=datetime.utcnow() - timedelta(days=30),
        )
        user.has_seen_dashboard_onboarding = True
        contact = Contact(
            organization_id=user.organization_id,
            user_id=user.id,
            created_by_id=user.id,
            first_name='Legacy',
            last_name='Contact',
        )
        db.session.add(contact)
        db.session.commit()
        user_id = user.id

    try:
        client.post('/login', data={
            'username': 'activation_baselined',
            'password': 'password123',
        })
        dashboard = client.get('/dashboard?friction=1')
        assert dashboard.status_code == 200
        assert b'What stopped you from finishing setup?' not in dashboard.data
        assert b'Give this relationship a next date.' not in dashboard.data
        assert b'How do you want to start?' not in dashboard.data
    finally:
        with app.app_context():
            _cleanup_user(user_id)


def test_lifecycle_stops_for_activated_user(app, seed, monkeypatch):
    with app.app_context():
        user = _new_user(
            seed,
            'activation_complete',
            created_at=datetime.utcnow() - timedelta(days=4),
        )
        user_id = user.id
        contact = Contact(
            organization_id=user.organization_id,
            user_id=user.id,
            created_by_id=user.id,
            first_name='Ready',
            last_name='Agent',
        )
        db.session.add(contact)
        db.session.flush()
        task_type = TaskType.query.get(seed['task_type_a'])
        subtype = TaskSubtype.query.get(seed['subtype_a'])
        db.session.add(Task(
            organization_id=user.organization_id,
            contact_id=contact.id,
            assigned_to_id=user.id,
            created_by_id=user.id,
            type_id=task_type.id,
            subtype_id=subtype.id,
            subject='Follow up',
            due_date=datetime.utcnow() + timedelta(days=1),
        ))
        db.session.commit()

    calls = []
    monkeypatch.setattr(
        'services.sendgrid_outbound.send_activation_nudge',
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    try:
        with app.app_context():
            from jobs.activation_lifecycle import send_activation_lifecycle_messages
            send_activation_lifecycle_messages()
            assert not any(
                args and getattr(args[0], 'id', None) == user_id
                for args, _kwargs in calls
            )
            assert ActivationEvent.query.filter_by(
                user_id=user_id,
                event=ActivationEvent.LIFECYCLE_MESSAGE_SENT,
            ).count() == 0
    finally:
        with app.app_context():
            _cleanup_user(user_id)

