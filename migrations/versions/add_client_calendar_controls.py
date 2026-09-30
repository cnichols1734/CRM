"""Track calendar link issuance and independent revocation for client grants."""
from alembic import op
import sqlalchemy as sa

revision = 'add_client_calendar_controls'
down_revision = 'add_client_inquiry_messages'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE client_portal_access ADD COLUMN IF NOT EXISTS calendar_version INTEGER NOT NULL DEFAULT 1')
        op.execute('ALTER TABLE client_portal_access ADD COLUMN IF NOT EXISTS calendar_issued_at TIMESTAMP WITHOUT TIME ZONE')
        op.execute('ALTER TABLE client_portal_access ADD COLUMN IF NOT EXISTS calendar_disabled_at TIMESTAMP WITHOUT TIME ZONE')
        return
    columns = {c['name'] for c in sa.inspect(conn).get_columns('client_portal_access')}
    for column in (
        sa.Column('calendar_version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('calendar_issued_at', sa.DateTime, nullable=True),
        sa.Column('calendar_disabled_at', sa.DateTime, nullable=True),
    ):
        if column.name not in columns:
            op.add_column('client_portal_access', column)


def downgrade():
    conn = op.get_bind()
    columns = {c['name'] for c in sa.inspect(conn).get_columns('client_portal_access')}
    with op.batch_alter_table('client_portal_access') as batch:
        for name in ('calendar_disabled_at', 'calendar_issued_at', 'calendar_version'):
            if name in columns:
                batch.drop_column(name)
