"""Add free-text buyer criteria to contacts."""
from alembic import op
import sqlalchemy as sa

revision = 'add_contact_buyer_criteria'
down_revision = 'add_personal_resources'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE contact ADD COLUMN IF NOT EXISTS buyer_criteria TEXT')
    elif 'buyer_criteria' not in {c['name'] for c in sa.inspect(conn).get_columns('contact')}:
        op.add_column('contact', sa.Column('buyer_criteria', sa.Text(), nullable=True))


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE contact DROP COLUMN IF EXISTS buyer_criteria')
    elif 'buyer_criteria' in {c['name'] for c in sa.inspect(conn).get_columns('contact')}:
        with op.batch_alter_table('contact') as batch:
            batch.drop_column('buyer_criteria')
