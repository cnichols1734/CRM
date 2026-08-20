"""Agent API session version and device tokens.

Revision ID: add_agent_api_tokens
Revises: add_tx_mls_listing_url
Create Date: 2026-08-20

Adds:
- user.session_version (invalidates issued agent JWTs)
- device_tokens (APNs tokens for agent and client apps)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_agent_api_tokens'
down_revision = 'add_tx_mls_listing_url'
branch_labels = None
depends_on = None


def _column_exists(conn, table_name, column_name):
    tables = inspect(conn).get_table_names()
    if table_name not in tables:
        return False
    return column_name in {
        col['name'] for col in inspect(conn).get_columns(table_name)
    }


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def upgrade():
    conn = op.get_bind()
    if not _column_exists(conn, 'user', 'session_version'):
        op.add_column(
            'user',
            sa.Column(
                'session_version',
                sa.Integer(),
                nullable=False,
                server_default=sa.text('1'),
            ),
        )

    if not _table_exists(conn, 'device_tokens'):
        op.create_table(
            'device_tokens',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('audience', sa.String(length=20), nullable=False),
            sa.Column('token', sa.String(length=255), nullable=False),
            sa.Column('platform', sa.String(length=20), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('participant_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('last_seen_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name='pk_device_tokens'),
            sa.ForeignKeyConstraint(
                ['organization_id'], ['organizations.id'],
                name='fk_device_tokens_org', ondelete='RESTRICT',
            ),
            sa.ForeignKeyConstraint(
                ['user_id'], ['user.id'],
                name='fk_device_tokens_user', ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['participant_id'], ['transaction_participants.id'],
                name='fk_device_tokens_participant', ondelete='CASCADE',
            ),
            sa.UniqueConstraint(
                'audience', 'token', name='uq_device_tokens_audience_token',
            ),
        )
        op.create_index(
            'ix_device_tokens_organization_id',
            'device_tokens',
            ['organization_id'],
        )
        op.create_index('ix_device_tokens_audience', 'device_tokens', ['audience'])
        op.create_index('ix_device_tokens_user_id', 'device_tokens', ['user_id'])
        op.create_index(
            'ix_device_tokens_participant_id',
            'device_tokens',
            ['participant_id'],
        )
        op.create_index(
            'ix_device_tokens_org_audience',
            'device_tokens',
            ['organization_id', 'audience'],
        )

    if conn.dialect.name == 'postgresql' and _table_exists(conn, 'device_tokens'):
        op.execute('ALTER TABLE device_tokens ENABLE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS tenant_isolation_device_tokens ON device_tokens')
        op.execute("""
            CREATE POLICY tenant_isolation_device_tokens ON device_tokens
            FOR ALL
            USING (
                organization_id = current_setting(
                    'app.current_org_id', true
                )::integer
            )
            WITH CHECK (
                organization_id = current_setting(
                    'app.current_org_id', true
                )::integer
            )
        """)


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql' and _table_exists(conn, 'device_tokens'):
        op.execute('DROP POLICY IF EXISTS tenant_isolation_device_tokens ON device_tokens')
    if _table_exists(conn, 'device_tokens'):
        op.drop_table('device_tokens')
    if _column_exists(conn, 'user', 'session_version'):
        op.drop_column('user', 'session_version')
