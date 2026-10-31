"""Track agent account deletion requests independently of deleted identities."""
from alembic import op
import sqlalchemy as sa
revision = 'add_account_deletion_requests'
down_revision = 'add_marketing_monthly_usage'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if 'deletion_identity' not in {c['name'] for c in sa.inspect(conn).get_columns('user')}:
        op.add_column('user', sa.Column('deletion_identity', sa.String(32), nullable=True))
    op.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_user_deletion_identity ON "user" (deletion_identity)')
    metadata = sa.MetaData()
    table = sa.Table('account_deletion_requests', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, nullable=False, index=True),
        sa.Column('user_id', sa.Integer, nullable=False),
        sa.Column('account_identity', sa.String(32), nullable=False, unique=True),
        sa.Column('requested_at', sa.DateTime, nullable=False),
        sa.Column('due_at', sa.DateTime, nullable=False),
        sa.Column('completed_at', sa.DateTime),
        sa.Column('completion_evidence', sa.Text),
        sa.Column('notified_at', sa.DateTime))
    table.create(conn, checkfirst=True)
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE account_deletion_requests ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE account_deletion_requests FORCE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON account_deletion_requests FROM PUBLIC')
        for role in ('anon', 'authenticated'):
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL ON account_deletion_requests FROM {role};
                    REVOKE ALL ON SEQUENCE account_deletion_requests_id_seq FROM {role};
                END IF;
            END $$""")
        op.execute('DROP POLICY IF EXISTS tenant_isolation ON account_deletion_requests')
        op.execute("""CREATE POLICY tenant_isolation ON account_deletion_requests FOR ALL
            USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
            WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)""")


def downgrade():
    op.execute('DROP TABLE IF EXISTS account_deletion_requests')
    op.execute('DROP INDEX IF EXISTS uq_user_deletion_identity')
    if 'deletion_identity' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('user')}:
        op.drop_column('user', 'deletion_identity')
