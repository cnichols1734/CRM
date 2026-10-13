"""Lead source persistence, validation, display, and tenant access."""
import importlib.util
from io import StringIO
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from models import Contact, db
from forms import ContactLeadSourceForm
from werkzeug.datastructures import MultiDict
from services.contact_lead_sources import LEAD_SOURCES


@pytest.fixture
def _clean_up_contacts(app, seed):
    with app.app_context():
        existing_ids = {row.id for row in Contact.query.with_entities(Contact.id)}
    yield
    with app.app_context():
        db.session.rollback()
        for contact in Contact.query.filter(~Contact.id.in_(existing_ids)).all():
            db.session.delete(contact)
        db.session.commit()


def _contact(app, seed, source=None, other=None, *, owner='owner_a', org='org_a'):
    with app.app_context():
        contact = Contact(
            organization_id=seed[org], user_id=seed[owner],
            first_name='Lead', last_name='Source',
            lead_source=source, lead_source_other=other,
        )
        db.session.add(contact)
        db.session.commit()
        return contact.id


def _edit(client, contact_id, **fields):
    return client.post(f'/contacts/{contact_id}/edit', data={
        'first_name': 'Lead', 'last_name': 'Source', **fields,
    })


@pytest.mark.usefixtures('_clean_up_contacts')
@pytest.mark.parametrize('source', ['Zillow', 'Other'])
def test_create_source(owner_a_client, app, source):
    response = owner_a_client.post('/contacts/create', data={
        'first_name': 'New', 'last_name': 'Source',
        'potential_commission': '5000',
        'lead_source': source, 'lead_source_other': '  Local podcast  ',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert response.status_code == 200, response.get_data(as_text=True)
    with app.app_context():
        contact = db.session.get(Contact, response.json['contact']['id'])
        assert contact.lead_source == source
        assert contact.lead_source_other == ('Local podcast' if source == 'Other' else None)


@pytest.mark.usefixtures('_clean_up_contacts')
@pytest.mark.parametrize('source', ['Zillow', 'Other'])
def test_edit_source(owner_a_client, app, seed, source):
    contact_id = _contact(app, seed)
    response = _edit(owner_a_client, contact_id, lead_source=source,
                     lead_source_other='  Local podcast  ')
    assert response.status_code == 200
    with app.app_context():
        contact = db.session.get(Contact, contact_id)
        assert contact.lead_source == source
        assert contact.lead_source_other == ('Local podcast' if source == 'Other' else None)


@pytest.mark.usefixtures('_clean_up_contacts')
@pytest.mark.parametrize('fields, error', [
    ({'lead_source': 'Not a valid source'}, 'lead_source'),
    ({'lead_source': 'Other', 'lead_source_other': '   '}, 'lead_source_other'),
    ({'lead_source': 'Other', 'lead_source_other': 'x' * 256}, 'lead_source_other'),
])
def test_invalid_edits_do_not_change_contact(owner_a_client, app, seed, fields, error):
    contact_id = _contact(app, seed, 'Zillow')
    response = _edit(owner_a_client, contact_id, first_name='Changed', **fields)
    assert response.status_code == 400
    assert error in response.json['errors']
    with app.app_context():
        contact = db.session.get(Contact, contact_id)
        assert contact.lead_source == 'Zillow'
        assert contact.first_name == 'Lead'


@pytest.mark.usefixtures('_clean_up_contacts')
@pytest.mark.parametrize('fields', [
    {'lead_source': 'Invalid'},
    {'lead_source': 'Other', 'lead_source_other': ''},
    {'lead_source': 'Other', 'lead_source_other': 'x' * 256},
])
def test_invalid_create(owner_a_client, app, fields):
    with app.app_context():
        before = Contact.query.count()
    response = owner_a_client.post('/contacts/create', data={
        'first_name': 'Invalid', 'last_name': 'Source',
        'potential_commission': '5000', **fields,
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert response.status_code == 400
    with app.app_context():
        assert Contact.query.count() == before


@pytest.mark.usefixtures('_clean_up_contacts')
def test_optional_create_and_edit_legacy_contact(owner_a_client, app, seed):
    response = owner_a_client.post('/contacts/create', data={
        'first_name': 'No', 'last_name': 'Source', 'potential_commission': '5000',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert response.status_code == 200
    contact_id = response.json['contact']['id']
    with app.app_context():
        contact = db.session.get(Contact, contact_id)
        assert contact.lead_source is None
        assert contact.lead_source_other is None
    assert _edit(owner_a_client, contact_id, lead_source='Open House').status_code == 200
    # Older forms may omit these fields entirely.
    assert _edit(owner_a_client, contact_id).status_code == 200
    with app.app_context():
        assert db.session.get(Contact, contact_id).lead_source == 'Open House'


@pytest.mark.usefixtures('_clean_up_contacts')
@pytest.mark.parametrize('source', ['', 'Instagram Ad'])
def test_changing_other_clears_text(owner_a_client, app, seed, source):
    contact_id = _contact(app, seed, 'Other', 'Old source')
    assert _edit(owner_a_client, contact_id, lead_source=source,
                 lead_source_other='Old source').status_code == 200
    with app.app_context():
        contact = db.session.get(Contact, contact_id)
        assert contact.lead_source == (source or None)
        assert contact.lead_source_other is None


@pytest.mark.usefixtures('_clean_up_contacts')
def test_display_and_edit_escape_other_text(owner_a_client, app, seed):
    contact_id = _contact(app, seed, 'Other', '<script>alert(1)</script>')
    for path in (f'/contact/{contact_id}', f'/contact/{contact_id}/preview'):
        response = owner_a_client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Other: &lt;script&gt;alert(1)&lt;/script&gt;' in html
        assert '<script>alert(1)</script>' not in html
    assert '<option selected value="Other">Other</option>' in owner_a_client.get(
        f'/contact/{contact_id}').get_data(as_text=True)


@pytest.mark.usefixtures('_clean_up_contacts')
def test_null_display_and_create_options(owner_a_client, app, seed):
    contact_id = _contact(app, seed)
    assert b'Not set' in owner_a_client.get(f'/contact/{contact_id}').data
    html = owner_a_client.get('/contacts/create').get_data(as_text=True)
    for source in LEAD_SOURCES:
        assert f'value="{source}"' in html
    assert 'data-lead-source-other hidden' in html


@pytest.mark.usefixtures('_clean_up_contacts')
def test_other_validation_preserves_create_input(owner_a_client):
    response = owner_a_client.post('/contacts/create', data={
        'first_name': 'Draft', 'last_name': 'Contact',
        'potential_commission': '5000', 'lead_source': 'Other',
    })
    html = response.get_data(as_text=True)
    assert 'Enter the other lead source.' in html
    assert '<option selected value="Other">Other</option>' in html
    assert 'value="Draft"' in html
    assert 'data-lead-source-other hidden' not in html


@pytest.mark.usefixtures('_clean_up_contacts')
def test_other_maximum_length_and_json_readback(owner_a_client, app, seed):
    contact_id = _contact(app, seed)
    other = 'x' * 255
    assert _edit(owner_a_client, contact_id, lead_source='Other',
                 lead_source_other=other).status_code == 200
    response = owner_a_client.get(f'/contact/{contact_id}', headers={
        'X-Requested-With': 'XMLHttpRequest',
    })
    assert response.json['lead_source'] == 'Other'
    assert response.json['lead_source_other'] == other


@pytest.mark.usefixtures('_clean_up_contacts')
def test_all_tiers_and_tenant_access(owner_b_client, agent_a_client, owner_a_client, app, seed):
    contact_id = _contact(app, seed)
    contact_b_id = _contact(app, seed, owner='owner_b', org='org_b')
    agent_contact_id = _contact(app, seed, owner='agent_a')
    assert _edit(owner_b_client, contact_id, lead_source='Zillow').status_code == 404
    assert _edit(agent_a_client, contact_id, lead_source='Zillow').status_code == 403
    assert _edit(owner_a_client, contact_b_id, lead_source='Zillow').status_code == 404
    assert _edit(owner_b_client, contact_b_id, lead_source='Zillow').status_code == 200
    assert _edit(agent_a_client, agent_contact_id, lead_source='TikTok').status_code == 200
    with app.app_context():
        assert db.session.get(Contact, contact_id).lead_source is None
        assert db.session.get(Contact, contact_b_id).lead_source == 'Zillow'


def _migration():
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/add_contact_lead_source.py'
    spec = importlib.util.spec_from_file_location('lead_source_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_preserves_existing_contacts_and_can_repeat(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    migration = _migration()
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE contact (id INTEGER PRIMARY KEY, organization_id INTEGER, notes TEXT)'))
        conn.execute(text("INSERT INTO contact VALUES (1, 10, 'Keep this'), (2, 20, 'Keep that')"))
        original = conn.execute(text('SELECT * FROM contact ORDER BY id')).all()
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
            assert conn.execute(text('SELECT id, organization_id, notes FROM contact ORDER BY id')).all() == original
            assert conn.execute(text('SELECT lead_source, lead_source_other FROM contact')).all() == [(None, None), (None, None)]
            migration.downgrade()
            assert {col['name'] for col in inspect(conn).get_columns('contact')} == {'id', 'organization_id', 'notes'}
            migration.upgrade()


def test_postgres_migration_is_additive_and_idempotent():
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={
        'as_sql': True, 'output_buffer': output,
    })
    with Operations.context(context):
        _migration().upgrade()
    assert output.getvalue().strip().splitlines() == [
        'ALTER TABLE contact ADD COLUMN IF NOT EXISTS lead_source VARCHAR(128);',
        '',
        'ALTER TABLE contact ADD COLUMN IF NOT EXISTS lead_source_other VARCHAR(255);',
    ]


@pytest.mark.parametrize('source', LEAD_SOURCES)
def test_source_catalog_validates_and_normalizes(app, source):
    with app.test_request_context():
        form = ContactLeadSourceForm(MultiDict({
            'lead_source': source, 'lead_source_other': '  Local podcast  ',
        }), meta={'csrf': False})
        assert form.validate(), form.errors
        assert form.lead_source.data == source
        assert form.lead_source_other.data == ('Local podcast' if source == 'Other' else None)
