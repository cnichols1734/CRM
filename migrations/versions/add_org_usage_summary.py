"""Store content-free organization usage counts for platform reporting."""
from alembic import op
import sqlalchemy as sa

revision = 'add_org_usage_summary'
down_revision = 'add_home_facts'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE organization_metrics ADD COLUMN IF NOT EXISTS usage_summary JSON')
    elif 'usage_summary' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('organization_metrics')}:
        op.add_column('organization_metrics', sa.Column('usage_summary', sa.JSON(), nullable=True))


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE organization_metrics DROP COLUMN IF EXISTS usage_summary')
    elif 'usage_summary' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('organization_metrics')}:
        op.drop_column('organization_metrics', 'usage_summary')
