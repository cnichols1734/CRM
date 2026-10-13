"""Task contact search permissions, matching, and form preselection."""
import pytest


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
