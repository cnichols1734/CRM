"""Task persistence, filtering, and assignment boundaries.

Cross-organization task CRUD denial is covered by test_idor_protection.py.
"""
from datetime import datetime

import pytest
from models import Task


@pytest.fixture
def task_records(app, db, seed):
    with app.app_context():
        rows = []
        for key, user, contact, status, priority in [
            ('pending', 'owner_a', 'contact_a', 'pending', 'low'),
            ('completed', 'owner_a', 'contact_a', 'completed', 'high'),
            ('teammate', 'agent_a', 'contact_a2', 'pending', 'high'),
        ]:
            task = Task(organization_id=seed['org_a'], contact_id=seed[contact],
                        assigned_to_id=seed[user], created_by_id=seed[user],
                        type_id=seed['task_type_a'], subtype_id=seed['subtype_a'],
                        subject=f'Prune regression {key}', status=status,
                        priority=priority, due_date=datetime(2026, 12, 1))
            db.session.add(task)
            rows.append((key, task))
        db.session.commit()
        ids = {key: task.id for key, task in rows}
    yield ids
    with app.app_context():
        Task.query.filter(Task.id.in_(ids.values())).delete(synchronize_session=False)
        db.session.commit()


@pytest.mark.parametrize('status,visible,hidden', [
    ('pending', 'pending', 'completed'),
    ('completed', 'completed', 'pending'),
])
def test_status_filter_returns_only_matching_assigned_tasks(owner_a_client, task_records, status, visible, hidden):
    response = owner_a_client.get('/tasks', query_string={'status': status})
    assert response.status_code == 200
    assert f'Prune regression {visible}'.encode() in response.data
    assert f'Prune regression {hidden}'.encode() not in response.data
    assert b'Prune regression teammate' not in response.data
    assert b'Task B Only' not in response.data


def test_agent_list_and_detail_show_assigned_task(agent_a_client, task_records):
    create_page = agent_a_client.get('/tasks/new')
    assert create_page.status_code == 200
    assert b'role="combobox"' in create_page.data
    response = agent_a_client.get('/tasks')
    assert response.status_code == 200
    assert b'Prune regression teammate' in response.data
    assert b'Prune regression pending' not in response.data
    response = agent_a_client.get(f'/tasks/{task_records["teammate"]}')
    assert response.status_code == 200
    assert b'Prune regression teammate' in response.data


def test_task_detail_ajax_and_missing_task(owner_a_client, task_records):
    response = owner_a_client.get(f'/tasks/{task_records["pending"]}',
                                  headers={'X-Requested-With': 'XMLHttpRequest'})
    assert response.status_code == 200
    assert response.json['subject'] == 'Prune regression pending'
    assert owner_a_client.get('/tasks/99999').status_code == 404


def test_create_persists_contact_assignment_and_due_time(owner_a_client, seed, app, db):
    response = owner_a_client.post('/tasks/new', data={
        'contact_id': seed['contact_a'], 'type_id': seed['task_type_a'],
        'subtype_id': seed['subtype_a'], 'subject': 'Persisted task regression',
        'priority': 'low', 'due_date': '2026-03-15', 'scheduled_time': '09:30',
    })
    assert response.status_code == 302
    with app.app_context():
        task = Task.query.filter_by(subject='Persisted task regression').one()
        try:
            assert (task.contact_id, task.assigned_to_id, task.organization_id) == (
                seed['contact_a'], seed['owner_a'], seed['org_a'])
            assert task.priority == 'low'
            assert task.due_date == datetime(2026, 3, 15, 14, 30)
            assert task.scheduled_time == task.due_date
        finally:
            db.session.delete(task)
            db.session.commit()


def test_create_without_contact_does_not_save_task(owner_a_client, app):
    with app.app_context():
        before = Task.query.count()
    response = owner_a_client.post('/tasks/new', data={'subject': 'Missing contact'})
    assert response.status_code == 302
    with app.app_context():
        assert Task.query.count() == before


@pytest.mark.parametrize('actor,key', [('owner_a_client', 'pending'), ('agent_a_client', 'teammate')])
def test_edit_persists_changes_for_assignee(request, actor, key, task_records, seed, app):
    client = request.getfixturevalue(actor)
    response = client.post(f'/tasks/{task_records[key]}/edit', data={
        'subject': 'Edited regression task', 'status': 'pending', 'priority': 'high',
        'due_date': '2026-04-01', 'contact_id': seed['contact_a2'],
        'type_id': seed['task_type_a'], 'subtype_id': seed['subtype_a'],
    })
    assert response.status_code == 200
    with app.app_context():
        task = Task.query.get(task_records[key])
        assert (task.subject, task.priority, task.contact_id) == (
            'Edited regression task', 'high', seed['contact_a2'])
        assert task.due_date == datetime(2026, 4, 2, 4, 59, 59)


def test_edit_another_agents_task_leaves_it_unchanged(agent_a_client, task_records, app):
    response = agent_a_client.post(f'/tasks/{task_records["pending"]}/edit', data={
        'subject': 'Hijacked', 'status': 'completed',
    })
    assert response.status_code == 403
    with app.app_context():
        task = Task.query.get(task_records['pending'])
        assert (task.subject, task.status) == ('Prune regression pending', 'pending')


def test_quick_update_persists_priority_and_completion(owner_a_client, task_records, app):
    url = f'/tasks/{task_records["pending"]}/quick-update'
    assert owner_a_client.post(url, data={'priority': 'high'}).status_code == 200
    assert owner_a_client.post(url, data={'status': 'completed'}).status_code == 200
    with app.app_context():
        task = Task.query.get(task_records['pending'])
        assert (task.priority, task.status) == ('high', 'completed')
        assert task.completed_at is not None


def test_subtypes_are_scoped_to_org(owner_a_client, seed):
    response = owner_a_client.get(f'/tasks/types/{seed["task_type_a"]}/subtypes')
    assert response.status_code == 200
    assert seed['subtype_a'] in [item['id'] for item in response.json]
    response = owner_a_client.get(f'/tasks/types/{seed["task_type_b"]}/subtypes')
    assert response.status_code == 200
    assert response.json == []


@pytest.fixture
def search_contacts(app, db, seed):
    from models import Contact
    with app.app_context():
        contacts = []
        result = dict(seed)
        for key, first, last, email, phone in [
            ('contact_a', 'Jane', 'Doe', 'picker-jane@test.com', '5551110000'),
            ('contact_a2', 'John', 'Smith', 'picker-john@test.com', '5552220000'),
            ('contact_b', 'Bob', 'Contact', 'picker-bob@test.com', ''),
        ]:
            original = Contact.query.get(seed[key])
            contact = Contact(user_id=original.user_id, organization_id=original.organization_id,
                              first_name=first, last_name=last, email=email, phone=phone)
            contacts.append(contact)
            db.session.add(contact)
            db.session.flush()
            result[key] = contact.id
        db.session.commit()
        ids = [contact.id for contact in contacts]
    try:
        yield result
    finally:
        with app.app_context():
            Contact.query.filter(Contact.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()


@pytest.mark.parametrize('query', ['Jane Doe', 'doe jane', 'picker-jane@test.com', '(555) 111-0000'])
def test_search_matches_name_email_and_phone(owner_a_client, search_contacts, query):
    seed = search_contacts
    data = owner_a_client.get('/tasks/contact-search', query_string={'q': query}).get_json()
    assert seed['contact_a'] in [contact['id'] for contact in data]


def test_create_search_scope_and_empty_query(owner_a_client, seed):
    assert owner_a_client.get('/tasks/contact-search?q=').get_json() == []
    assert owner_a_client.get('/tasks/contact-search?q=John').get_json() == []
    assert owner_a_client.get('/tasks/contact-search?q=bc@test.com').get_json() == []
    assert owner_a_client.get('/tasks/contact-search?q=%25').get_json() == []


def test_edit_search_keeps_org_contact_choices(owner_a_client, search_contacts):
    seed = search_contacts
    response = owner_a_client.get('/tasks/contact-search', query_string={
        'q': 'John', 'task_id': seed['task_a'],
    })
    assert seed['contact_a2'] in [contact['id'] for contact in response.get_json()]
    assert owner_a_client.get('/tasks/contact-search', query_string={
        'q': 'bc@test.com', 'task_id': seed['task_a'],
    }).get_json() == []


def test_search_requires_task_edit_permission(agent_a_client, owner_a_client, seed):
    assert agent_a_client.get('/tasks/contact-search', query_string={
        'q': 'Jane', 'task_id': seed['task_a'],
    }).status_code == 403
    assert owner_a_client.get('/tasks/contact-search', query_string={
        'q': 'Bob', 'task_id': seed['task_b'],
    }).status_code == 404


def test_search_requires_login(client):
    assert client.get('/tasks/contact-search?q=Jane').status_code == 302


def test_form_preselection_and_search_control(owner_a_client, seed):
    response = owner_a_client.get(f'/tasks/new?contact_id={seed["contact_a"]}')
    assert b'role="combobox"' in response.data
    assert f'name="contact_id" value="{seed["contact_a"]}"'.encode() in response.data
    assert b'<select name="contact_id"' not in response.data
    response = owner_a_client.get(f'/tasks/new?contact_id={seed["contact_b"]}')
    assert b'name="contact_id" value=""' in response.data
    response = owner_a_client.get(f'/tasks/{seed["task_a"]}')
    assert b'role="combobox"' in response.data
    assert f'task_id={seed["task_a"]}'.encode() in response.data


def test_edit_rejects_cross_org_contact_before_changing_task(owner_a_client, seed, app):
    from models import Task
    with app.app_context():
        original = Task.query.get(seed['task_a']).subject
    response = owner_a_client.post(f'/tasks/{seed["task_a"]}/edit', data={
        'contact_id': seed['contact_b'], 'subject': 'Unauthorized change',
    })
    assert response.status_code == 404
    with app.app_context():
        task = Task.query.get(seed['task_a'])
        assert task.subject == original
        assert task.contact_id == seed['contact_a']


def test_search_limits_results(owner_a_client, seed, app, db):
    from models import Contact
    with app.app_context():
        owner = Contact.query.get(seed['contact_a'])
        contacts = [Contact(first_name='PickerLimit', last_name=str(index),
                            user_id=owner.user_id, organization_id=owner.organization_id)
                    for index in range(25)]
        db.session.add_all(contacts)
        db.session.commit()
        ids = [contact.id for contact in contacts]
    try:
        result = owner_a_client.get('/tasks/contact-search?q=PickerLimit').get_json()
        assert len(result) == 20
        assert all(contact['id'] in ids for contact in result)
    finally:
        with app.app_context():
            Contact.query.filter(Contact.id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
