import uuid

from models import (db, ClientBrowseAccount, ClientBrowseInquiry, ClientInquiryMessage,
                    User, UserTodo, Interaction, AuditEvent, Contact)
from tests.test_client_discovery import enable_discovery, signup, auth, inquiry_body, BASE
from tests.test_client_messages import _csrf


def create(client):
    registration = signup(client)
    headers = auth(registration)
    row = client.post(BASE + '/inquiries', headers=headers, json=inquiry_body(phone='5550001234'))
    assert row.status_code == 201
    return registration.json['account']['id'], row.json['id'], headers


def test_unassigned_client_claim_moves_all_inquiries_and_future_requests(app, client, agent_a_client, owner_a_client, seed):
    aid, iid, headers = create(client)
    second = client.post(BASE + '/inquiries', headers=headers, json=inquiry_body()).json['id']
    with app.app_context():
        account = db.session.get(ClientBrowseAccount, aid)
        assert account.agent_id is None and account.contact_id is None
    assert b'Test Browser' in agent_a_client.get('/messages?view=general').data
    path = f'/messages?thread=inquiry-{iid}'
    agent_a_client.get(path)
    assert agent_a_client.post(path, data={'body': 'Cannot reply yet', 'csrf_token': _csrf(agent_a_client)}).status_code == 403
    assert agent_a_client.post(path, data={'action': 'claim', 'csrf_token': _csrf(agent_a_client)}).status_code == 302
    with app.app_context():
        account = db.session.get(ClientBrowseAccount, aid)
        assert account.agent_id == seed['agent_a']
        assert db.session.get(Contact, account.contact_id).phone == '5550001234'
        assert {r.agent_id for r in ClientBrowseInquiry.query.filter_by(account_id=aid)} == {seed['agent_a']}
    assert f'thread=inquiry-{iid}&'.encode() not in agent_a_client.get('/messages?view=general').data
    assert f'thread=inquiry-{second}&'.encode() in agent_a_client.get('/messages?view=mine').data
    assert owner_a_client.get(path).status_code == 200
    third = client.post(BASE + '/inquiries', headers=headers, json=inquiry_body()).json['id']
    with app.app_context():
        assert db.session.get(ClientBrowseInquiry, third).agent_id == seed['agent_a']


def test_claim_conflicts_reassignment_permissions_and_followup_lifecycle(app, client, agent_a_client, owner_a_client, owner_b_client, seed):
    aid, iid, headers = create(client)
    path = f'/messages?thread=inquiry-{iid}'
    for c in (agent_a_client, owner_a_client):
        c.get(path)
    assert agent_a_client.post(path, data={'action': 'claim', 'csrf_token': _csrf(agent_a_client)}).status_code == 302
    assert owner_a_client.post(path, data={'action': 'claim', 'csrf_token': _csrf(owner_a_client)}).status_code == 409
    assert agent_a_client.post(path, data={'action': 'assign', 'agent_id': seed['owner_a'], 'csrf_token': _csrf(agent_a_client)}).status_code == 403
    assert owner_b_client.get(path).status_code == 404
    with app.app_context():
        row = db.session.get(ClientBrowseInquiry, iid)
        todo_id = row.followup_todo_id
        cid = db.session.get(ClientBrowseAccount, aid).contact_id
        notes = Interaction.query.filter_by(contact_id=cid).count()
    base = {'action': 'assign', 'csrf_token': _csrf(owner_a_client), 'expected_agent': str(seed['agent_a'])}
    assert owner_a_client.post(path, data={**base, 'agent_id': seed['owner_b']}).status_code == 400
    assert owner_a_client.post(path, data={**base, 'agent_id': seed['owner_a']}).status_code == 302
    assert owner_a_client.post(path, data={**base, 'agent_id': seed['agent_a']}).status_code == 409
    assert agent_a_client.get(path).status_code == 404
    with app.app_context():
        assert db.session.get(UserTodo, todo_id).user_id == seed['owner_a']
        assert Interaction.query.filter_by(contact_id=cid).count() == notes
    release = {'action': 'assign', 'csrf_token': _csrf(owner_a_client), 'expected_agent': str(seed['owner_a']), 'agent_id': ''}
    assert owner_a_client.post(path, data=release).status_code == 302
    assert agent_a_client.post(path, data={'action': 'claim', 'csrf_token': _csrf(agent_a_client)}).status_code == 302
    with app.app_context():
        assert db.session.get(UserTodo, todo_id) is None
        assert Interaction.query.filter_by(contact_id=cid).count() == notes
        assert AuditEvent.query.filter_by(organization_id=seed['org_a'], event_type='client_inquiry_assignment').count() >= 4


def test_append_only_history_idempotency_and_account_isolation(app, client, agent_a_client):
    aid, iid, headers = create(client)
    path = f'/messages?thread=inquiry-{iid}'
    agent_a_client.get(path)
    agent_a_client.post(path, data={'action': 'claim', 'csrf_token': _csrf(agent_a_client)})
    with app.app_context():
        db.session.get(ClientBrowseInquiry, iid).reply = 'Legacy reply stays visible'
        db.session.commit()
    reply = {'body': 'Yes, Saturday works.', 'csrf_token': _csrf(agent_a_client), 'request_id': str(uuid.uuid4())}
    assert agent_a_client.post(path, data=reply).status_code == 302
    assert agent_a_client.post(path, data=reply).status_code == 302
    message = {'body': 'How about 10 AM?', 'request_id': str(uuid.uuid4())}
    url = BASE + f'/inquiries/{iid}/messages'
    assert client.post(url, headers=headers, json=message).status_code == 200
    assert client.post(url, headers=headers, json=message).status_code == 200
    other = auth(signup(client))
    assert client.post(url, headers=other, json=message).status_code == 404
    assert client.post(url, headers=headers, json={**message, 'body': 'x'*4001}).status_code == 400
    row = client.get(BASE+'/inquiries', headers=headers).json['inquiries'][0]
    assert [m['body'] for m in row['messages']] == ['Test showing inquiry', 'Legacy reply stays visible', 'Yes, Saturday works.', 'How about 10 AM?']
    assert row['reply'] == 'Yes, Saturday works.' and row['assigned']
    assert b'How about 10 AM?' in agent_a_client.get('/messages?view=attention').data
    assert client.delete(BASE+'/account', headers=headers, json={'password': 'test-password-123'}).status_code == 200
    with app.app_context():
        assert ClientInquiryMessage.query.filter_by(inquiry_id=iid).count() == 0


def test_verification_assigns_existing_inquiries(app, client, seed, monkeypatch):
    aid, iid, headers = create(client)
    with app.app_context():
        email = db.session.get(ClientBrowseAccount, aid).email
        db.session.add(Contact(organization_id=seed['org_a'], user_id=seed['agent_a'], first_name='Existing', last_name='Client', email=email))
        db.session.commit()
    codes = []
    monkeypatch.setattr('routes.client_discovery.send_verification_email', lambda account, code, org: codes.append(code) or True)
    assert client.post(BASE+'/email/verification', headers=headers).status_code == 200
    assert client.post(BASE+'/email/verify', headers=headers, json={'code': codes[-1]}).status_code == 200
    with app.app_context():
        assert db.session.get(ClientBrowseInquiry, iid).agent_id == seed['agent_a']
        assert db.session.get(ClientBrowseAccount, aid).agent_id == seed['agent_a']


def test_super_admin_retains_org_scoped_inquiry_access(app, client, agent_a_client, owner_a_client, seed):
    aid, iid, headers = create(client)
    path = f'/messages?thread=inquiry-{iid}'
    owner_a_client.get(path)
    owner_a_client.post(path, data={'action': 'claim', 'csrf_token': _csrf(owner_a_client)})
    with app.app_context():
        user = db.session.get(User, seed['agent_a']); user.is_super_admin = True
        db.session.commit()
    try:
        assert agent_a_client.get(path).status_code == 200
    finally:
        with app.app_context():
            db.session.get(User, seed['agent_a']).is_super_admin = False
            db.session.commit()


def test_migration_preserves_legacy_history_and_enforces_cascades():
    import importlib
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration = importlib.import_module('migrations.versions.add_client_inquiry_messages')
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        for name in ('organizations', 'user', 'user_todos', 'client_browse_accounts'):
            conn.execute(sa.text(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)'))
            conn.execute(sa.text(f'INSERT INTO "{name}" (id) VALUES (1)'))
        metadata = sa.MetaData()
        for name in ('organizations', 'user', 'user_todos', 'client_browse_accounts'):
            sa.Table(name, metadata, autoload_with=conn)
        sa.Table('client_browse_inquiries', metadata,
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('account_id', sa.Integer, sa.ForeignKey('client_browse_accounts.id', ondelete='CASCADE'), nullable=False),
            sa.Column('agent_id', sa.Integer, sa.ForeignKey('user.id', ondelete='SET NULL')),
            sa.Column('body', sa.Text, nullable=False), sa.Column('reply', sa.Text),
            sa.Column('created_at', sa.DateTime, nullable=False)).create(conn)
        conn.execute(sa.text("INSERT INTO client_browse_inquiries VALUES (1,1,1,1,'Original question','Original reply',CURRENT_TIMESTAMP)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
        assert conn.execute(sa.text('SELECT body, reply, crm_recorded_at IS NOT NULL FROM client_browse_inquiries')).one() == ('Original question', 'Original reply', 1)
        conn.execute(sa.text("INSERT INTO client_inquiry_messages (id,organization_id,inquiry_id,sender,body,request_id,author_user_id,created_at) VALUES (1,1,1,'agent','Followup','request',1,CURRENT_TIMESTAMP)"))
    with engine.begin() as conn:
        conn.execute(sa.text('PRAGMA foreign_keys=ON'))
        conn.execute(sa.text('DELETE FROM "user" WHERE id=1'))
        assert conn.execute(sa.text('SELECT author_user_id FROM client_inquiry_messages')).scalar() is None
        conn.execute(sa.text('DELETE FROM client_browse_accounts WHERE id=1'))
        assert conn.execute(sa.text('SELECT count(*) FROM client_inquiry_messages')).scalar() == 0
