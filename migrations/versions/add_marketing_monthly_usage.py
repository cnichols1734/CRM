"""Persist free marketing send attempts independently of campaign history."""
from alembic import op
import sqlalchemy as sa

revision = 'add_marketing_monthly_usage'
down_revision = 'add_contact_buyer_criteria'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text('''CREATE TABLE IF NOT EXISTS marketing_monthly_usage (
        organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        period_start TIMESTAMP NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        PRIMARY KEY (organization_id, period_start)
    )'''))
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE marketing_monthly_usage ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE marketing_monthly_usage FORCE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON TABLE marketing_monthly_usage FROM anon, authenticated')
        op.execute('''DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
                AND tablename='marketing_monthly_usage'
                AND policyname='tenant_isolation_marketing_monthly_usage') THEN
                CREATE POLICY tenant_isolation_marketing_monthly_usage ON marketing_monthly_usage
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::integer);
            END IF;
        END $$''')


def downgrade():
    op.execute('DROP TABLE IF EXISTS marketing_monthly_usage')
