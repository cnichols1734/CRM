"""Browsing accounts and brokerage app settings."""
from alembic import op
import sqlalchemy as sa

revision = 'add_client_discovery'
down_revision = 'add_marketing_tracking'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'client_app_settings' not in {c['name'] for c in inspector.get_columns('organizations')}:
        op.add_column('organizations', sa.Column('client_app_settings', sa.JSON(), nullable=True))
    metadata = sa.MetaData()
    # Referenced tables are reflected so this migration does not import app models.
    for table in ('organizations', 'user', 'contact'):
        sa.Table(table, metadata, autoload_with=conn)
    sa.Table('client_browse_accounts', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('email', sa.String(120), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('password_hash', sa.String(256), nullable=False),
        sa.Column('session_version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('saved_ids', sa.JSON, nullable=False),
        sa.Column('owns_contact', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('email_verified_at', sa.DateTime),
        sa.Column('verification_hash', sa.String(64)),
        sa.Column('verification_expires_at', sa.Integer),
        sa.Column('verification_attempts', sa.Integer, nullable=False, server_default='0'),
        sa.Column('linked_access_ids', sa.JSON, nullable=False),
        sa.Column('agent_id', sa.Integer, sa.ForeignKey('user.id', ondelete='SET NULL')),
        sa.Column('contact_id', sa.Integer, sa.ForeignKey('contact.id', ondelete='SET NULL')),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('organization_id', 'email'))
    sa.Table('client_browse_inquiries', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('account_id', sa.Integer, sa.ForeignKey('client_browse_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_id', sa.Integer, sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('listing_id', sa.String(80)),
        sa.Column('body', sa.Text, nullable=False),
        sa.Column('reply', sa.Text),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('account_id', 'request_id'))
    sa.Table('client_browse_rate_limits', metadata,
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('hits', sa.Integer, nullable=False),
        sa.Column('expires_at', sa.Integer, nullable=False, index=True))
    for name in ('client_browse_accounts', 'client_browse_inquiries', 'client_browse_rate_limits'):
        metadata.tables[name].create(conn, checkfirst=True)
    if conn.dialect.name == 'postgresql':
        for name in ('client_browse_accounts', 'client_browse_inquiries', 'client_browse_rate_limits'):
            op.execute(f'ALTER TABLE {name} ENABLE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE {name} FROM PUBLIC')
            for role in ('anon', 'authenticated'):
                op.execute(f"""DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                        REVOKE ALL ON TABLE {name} FROM {role};
                    END IF;
                END $$""")
            if name != 'client_browse_rate_limits':
                for role in ('anon', 'authenticated'):
                    op.execute(f"""DO $$ BEGIN
                        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                            REVOKE ALL ON SEQUENCE {name}_id_seq FROM {role};
                        END IF;
                    END $$""")
        # The runtime still needs explicit table privileges. API roles are denied
        # by both ACLs and this policy; the shared counters have no tenant key.
        op.execute('DROP POLICY IF EXISTS runtime_counters ON client_browse_rate_limits')
        op.execute("""CREATE POLICY runtime_counters ON client_browse_rate_limits FOR ALL
            USING (current_user NOT IN ('anon', 'authenticated', 'authenticator'))
            WITH CHECK (current_user NOT IN ('anon', 'authenticated', 'authenticator'))""")
        for name in ('client_browse_accounts', 'client_browse_inquiries'):
            op.execute(f'ALTER TABLE {name} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE {name} FORCE ROW LEVEL SECURITY')
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON {name}')
            op.execute(f"""CREATE POLICY tenant_isolation ON {name} FOR ALL
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)""")


def downgrade():
    for name in ('client_browse_inquiries', 'client_browse_accounts', 'client_browse_rate_limits'):
        op.execute(sa.text(f'DROP TABLE IF EXISTS {name}'))
    if 'client_app_settings' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('organizations')}:
        op.drop_column('organizations', 'client_app_settings')
