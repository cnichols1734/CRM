"""Attach browsing accounts and APNs environments to registered devices."""
from alembic import op
import sqlalchemy as sa

revision = 'add_browse_push_devices'
down_revision = 'add_personal_todo_details'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        op.execute('ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS browse_account_id INTEGER REFERENCES client_browse_accounts(id) ON DELETE CASCADE')
        op.execute('ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS environment VARCHAR(20)')
        op.execute('ALTER TABLE device_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP')
        op.execute('CREATE INDEX IF NOT EXISTS ix_device_tokens_browse_account_id ON device_tokens (browse_account_id)')
    else:
        columns = {c['name'] for c in sa.inspect(conn).get_columns('device_tokens')}
        with op.batch_alter_table('device_tokens') as batch:
            if 'browse_account_id' not in columns:
                batch.add_column(sa.Column('browse_account_id', sa.Integer(), nullable=True))
                batch.create_foreign_key('fk_devices_browse_account', 'client_browse_accounts', ['browse_account_id'], ['id'], ondelete='CASCADE')
                batch.create_index('ix_device_tokens_browse_account_id', ['browse_account_id'])
            if 'expires_at' not in columns:
                batch.add_column(sa.Column('expires_at', sa.DateTime(), nullable=True))
            if 'environment' not in columns:
                batch.add_column(sa.Column('environment', sa.String(20), nullable=True))


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE device_tokens DROP COLUMN IF EXISTS browse_account_id')
        op.execute('ALTER TABLE device_tokens DROP COLUMN IF EXISTS environment')
        op.execute('ALTER TABLE device_tokens DROP COLUMN IF EXISTS expires_at')
    else:
        with op.batch_alter_table('device_tokens') as batch:
            batch.drop_index('ix_device_tokens_browse_account_id')
            batch.drop_constraint('fk_devices_browse_account', type_='foreignkey')
            batch.drop_column('browse_account_id')
            batch.drop_column('environment')
            batch.drop_column('expires_at')
