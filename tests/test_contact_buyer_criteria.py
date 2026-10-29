"""Buyer criteria persistence, display, search, and access boundaries."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from models import Contact, db
from services.bob_tools import BobContext, dispatch

CRITERIA = 'At least 4 bedrooms with a pool\nUnder $500,000 in BHISD'


@pytest.fixture(autouse=True)
def cleanup(app, seed):
    with app.app_context():
        original = {c.id for c in Contact.query.all()}
    yield
    with app.app_context():
        db.session.rollback()
        for c in Contact.query.filter(~Contact.id.in_(original)).all():
            db.session.delete(c)
        db.session.commit()
    from services.cache_helpers import _cache
    _cache.clear()


def contact(app, seed, owner='owner_a', org='org_a', criteria=CRITERIA):
    with app.app_context():
        c = Contact(organization_id=seed[org], user_id=seed[owner],
                    first_name='Buyer', last_name='Criteria', buyer_criteria=criteria)
        db.session.add(c)
        db.session.commit()
        return c.id


def edit(client, cid, **fields):
    return client.post(f'/contacts/{cid}/edit', data={
        'first_name': 'Buyer', 'last_name': 'Criteria', **fields,
    })


def test_create_edit_clear_and_omitted_value(owner_a_client, app):
    response = owner_a_client.post('/contacts/create', data={
        'first_name': 'New', 'last_name': 'Buyer', 'potential_commission': '5000',
        'buyer_criteria': f'  {CRITERIA}  ',
    }, headers={'X-Requested-With': 'XMLHttpRequest'})
    assert response.status_code == 200
    cid = response.json['contact']['id']
    with app.app_context():
        assert db.session.get(Contact, cid).buyer_criteria == CRITERIA
    assert edit(owner_a_client, cid).status_code == 200
    with app.app_context():
        assert db.session.get(Contact, cid).buyer_criteria == CRITERIA
    assert edit(owner_a_client, cid, buyer_criteria='  Acreage  ').status_code == 200
    with app.app_context():
        assert db.session.get(Contact, cid).buyer_criteria == 'Acreage'
    assert edit(owner_a_client, cid, buyer_criteria='   ').status_code == 200
    with app.app_context():
        assert db.session.get(Contact, cid).buyer_criteria is None


def test_view_escapes_criteria(owner_a_client, app, seed):
    cid = contact(app, seed, criteria='<script>alert(1)</script>\nBHISD')
    response = owner_a_client.get(f'/contact/{cid}')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Buyer criteria' in html
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html


def test_unauthorized_edit_rejected(agent_a_client, owner_b_client, app, seed):
    cid = contact(app, seed)
    assert edit(agent_a_client, cid, buyer_criteria='Changed').status_code == 403
    assert edit(owner_b_client, cid, buyer_criteria='Changed').status_code == 404
    with app.app_context():
        assert db.session.get(Contact, cid).buyer_criteria == CRITERIA


def test_bob_search_count_detail_and_scope(app, seed):
    own = contact(app, seed)
    teammate = contact(app, seed, owner='agent_a')
    contact(app, seed, owner='owner_b', org='org_b')
    blank = contact(app, seed, criteria='  ')
    owner = BobContext(user_id=seed['owner_a'], organization_id=seed['org_a'],
                       org_role='owner', is_org_admin=True)
    agent = BobContext(user_id=seed['agent_a'], organization_id=seed['org_a'],
                       org_role='agent', is_org_admin=False)
    with app.app_context():
        result = dispatch('search_contacts', {'buyer_criteria': 'bhisd'}, owner)
        assert result.ok
        assert [c['contact_id'] for c in result.data['contacts']] == [own]
        assert result.data['contacts'][0]['buyer_criteria'] == CRITERIA
        result = dispatch('search_contacts', {'buyer_criteria': 'pool', 'scope': 'organization'}, owner)
        assert {c['contact_id'] for c in result.data['contacts']} == {own, teammate}
        result = dispatch('search_contacts', {'buyer_criteria': 'pool', 'scope': 'organization'}, agent)
        assert [c['contact_id'] for c in result.data['contacts']] == [teammate]
        result = dispatch('search_contacts', {'present_fields': ['buyer_criteria']}, owner)
        assert {c['contact_id'] for c in result.data['contacts']} == {own}
        result = dispatch('search_contacts', {'missing_fields': ['buyer_criteria']}, owner)
        assert blank in {c['contact_id'] for c in result.data['contacts']}
        result = dispatch('count_contacts', {'buyer_criteria': 'BHISD'}, owner)
        assert result.data['total'] == 1
        result = dispatch('get_contact', {'contact_id': own}, owner)
        assert result.data['buyer_criteria'] == CRITERIA
        result = dispatch('search_contacts', {'buyer_criteria': '%'}, owner)
        assert result.data['total_matching'] == 0


def test_migration_preserves_existing_rows_and_is_repeatable(tmp_path):
    path = Path(__file__).parents[1] / 'migrations/versions/add_contact_buyer_criteria.py'
    spec = importlib.util.spec_from_file_location('buyer_criteria_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE contact (id INTEGER PRIMARY KEY, first_name TEXT)'))
        conn.execute(text("INSERT INTO contact VALUES (1, 'Existing')"))
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        migration.upgrade()
        assert conn.execute(text('SELECT buyer_criteria FROM contact')).scalar() is None
        conn.execute(text("UPDATE contact SET buyer_criteria = 'Pool'"))
        assert conn.execute(text('SELECT buyer_criteria FROM contact')).scalar() == 'Pool'
        migration.downgrade()
        migration.downgrade()
        assert 'buyer_criteria' not in {c['name'] for c in inspect(conn).get_columns('contact')}
        assert conn.execute(text('SELECT first_name FROM contact')).scalar() == 'Existing'


def test_bob_marks_long_criteria_and_expands_detail(app, seed):
    criteria = 'Area preference. ' * 50 + 'Must have a pool.'
    cid = contact(app, seed, criteria=criteria)
    ctx = BobContext(user_id=seed['owner_a'], organization_id=seed['org_a'],
                     org_role='owner', is_org_admin=True)
    with app.app_context():
        result = dispatch('search_contacts', {'buyer_criteria': 'pool'}, ctx)
        assert result.data['contacts'][0]['buyer_criteria_truncated'] is True
        result = dispatch('get_contact', {'contact_id': cid}, ctx)
        assert result.data['buyer_criteria'] == criteria
        assert result.data['buyer_criteria_truncated'] is False
        db.session.get(Contact, cid).buyer_criteria = 'x' * 8001
        db.session.commit()
        result = dispatch('get_contact', {'contact_id': cid}, ctx)
        assert result.data['buyer_criteria_truncated'] is True
