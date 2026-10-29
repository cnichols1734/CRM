"""Add personal resource links without changing shared resources."""
from alembic import op
import sqlalchemy as sa

revision = 'add_personal_resources'
down_revision = 'add_contact_lead_source'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if not sa.inspect(conn).has_table('personal_resources'):
        op.create_table(
            'personal_resources',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer, sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
            sa.Column('label', sa.String(100), nullable=False),
            sa.Column('url', sa.String(2000), nullable=False),
        )
    op.execute('CREATE INDEX IF NOT EXISTS ix_personal_resources_org_user ON personal_resources (organization_id, user_id)')
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE personal_resources ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE personal_resources FORCE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON personal_resources FROM anon, authenticated')
        op.execute('DROP POLICY IF EXISTS tenant_isolation_personal_resources ON personal_resources')
        op.execute("""
            CREATE POLICY tenant_isolation_personal_resources ON personal_resources
            FOR ALL
            USING (organization_id = current_setting('app.current_org_id', true)::int)
            WITH CHECK (organization_id = current_setting('app.current_org_id', true)::int)
        """)


def downgrade():
    op.execute('DROP TABLE IF EXISTS personal_resources')
