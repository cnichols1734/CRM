"""Add optional lead source fields to contacts."""
from alembic import op
import sqlalchemy as sa

revision = 'add_contact_lead_source'
down_revision = 'add_client_calendar_controls'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE contact ADD COLUMN IF NOT EXISTS lead_source VARCHAR(128)')
        op.execute('ALTER TABLE contact ADD COLUMN IF NOT EXISTS lead_source_other VARCHAR(255)')
        return
    columns = {column['name'] for column in sa.inspect(conn).get_columns('contact')}
    for column in (
        sa.Column('lead_source', sa.String(128), nullable=True),
        sa.Column('lead_source_other', sa.String(255), nullable=True),
    ):
        if column.name not in columns:
            op.add_column('contact', column)


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE contact DROP COLUMN IF EXISTS lead_source_other')
        op.execute('ALTER TABLE contact DROP COLUMN IF EXISTS lead_source')
        return
    columns = {column['name'] for column in sa.inspect(conn).get_columns('contact')}
    with op.batch_alter_table('contact') as batch:
        for name in ('lead_source_other', 'lead_source'):
            if name in columns:
                batch.drop_column(name)
