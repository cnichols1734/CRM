"""Append messages to browsing inquiries and preserve prospect phone numbers."""
from alembic import op
import sqlalchemy as sa

revision = 'add_client_inquiry_messages'
down_revision = 'add_inquiry_listing_snapshot'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    columns = {c['name'] for c in sa.inspect(conn).get_columns('client_browse_inquiries')}
    with op.batch_alter_table('client_browse_inquiries') as batch:
        if 'phone' not in columns:
            batch.add_column(sa.Column('phone', sa.String(20), nullable=True))
        if 'crm_recorded_at' not in columns:
            batch.add_column(sa.Column('crm_recorded_at', sa.DateTime, nullable=True))
        if 'followup_todo_id' not in columns:
            batch.add_column(sa.Column('followup_todo_id', sa.Integer, nullable=True))
            batch.create_foreign_key('fk_inquiry_followup_todo', 'user_todos', ['followup_todo_id'], ['id'], ondelete='SET NULL')
    if 'crm_recorded_at' not in columns:
        op.execute('UPDATE client_browse_inquiries SET crm_recorded_at = created_at WHERE agent_id IS NOT NULL')
    metadata = sa.MetaData()
    for name in ('organizations', 'user', 'client_browse_inquiries'):
        sa.Table(name, metadata, autoload_with=conn)
    table = sa.Table('client_inquiry_messages', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('inquiry_id', sa.Integer, sa.ForeignKey('client_browse_inquiries.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sender', sa.String(10), nullable=False),
        sa.Column('body', sa.Text, nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('author_user_id', sa.Integer, sa.ForeignKey('user.id', ondelete='SET NULL')),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('inquiry_id', 'sender', 'request_id'),
        sa.CheckConstraint("sender IN ('client', 'agent')", name='ck_inquiry_message_sender'),
        sa.Index('ix_inquiry_messages_thread', 'organization_id', 'inquiry_id', 'id'))
    table.create(conn, checkfirst=True)
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE client_inquiry_messages ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE client_inquiry_messages FORCE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE client_inquiry_messages FROM PUBLIC')
        op.execute('REVOKE ALL ON SEQUENCE client_inquiry_messages_id_seq FROM PUBLIC')
        for role in ('anon', 'authenticated'):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL ON TABLE client_inquiry_messages FROM {role};
                    REVOKE ALL ON SEQUENCE client_inquiry_messages_id_seq FROM {role};
                END IF;
            END $$""")
        op.execute('DROP POLICY IF EXISTS tenant_isolation ON client_inquiry_messages')
        op.execute("""CREATE POLICY tenant_isolation ON client_inquiry_messages FOR ALL
            USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
            WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)""")


def downgrade():
    op.execute('DROP TABLE IF EXISTS client_inquiry_messages')
    with op.batch_alter_table('client_browse_inquiries') as batch:
        batch.drop_constraint('fk_inquiry_followup_todo', type_='foreignkey')
        for name in ('phone', 'crm_recorded_at', 'followup_todo_id'):
            batch.drop_column(name)
