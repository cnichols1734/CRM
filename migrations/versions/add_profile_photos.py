"""Store private profile photos and deduplicate mobile deal messages."""
from alembic import op
import sqlalchemy as sa

revision = 'add_profile_photos'
down_revision = 'add_browse_push_devices'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    for table in ('user', 'client_browse_accounts', 'transaction_participants'):
        if conn.dialect.name == 'postgresql':
            op.execute(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS avatar_path VARCHAR(500)')
        elif 'avatar_path' not in {c['name'] for c in sa.inspect(conn).get_columns(table)}:
            op.add_column(table, sa.Column('avatar_path', sa.String(500), nullable=True))
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE portal_messages ADD COLUMN IF NOT EXISTS request_id VARCHAR(36)')
        op.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_portal_message_request ON portal_messages (participant_id, sender, request_id)')
    else:
        if 'request_id' not in {c['name'] for c in sa.inspect(conn).get_columns('portal_messages')}:
            op.add_column('portal_messages', sa.Column('request_id', sa.String(36), nullable=True))
        op.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_portal_message_request ON portal_messages (participant_id, sender, request_id)')


def downgrade():
    op.execute('DROP INDEX IF EXISTS uq_portal_message_request')
    for table, column in [('portal_messages', 'request_id')] + [(t, 'avatar_path') for t in ('user', 'client_browse_accounts', 'transaction_participants')]:
        if op.get_bind().dialect.name == 'postgresql':
            op.execute(f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS {column}')
        else:
            op.drop_column(table, column)
