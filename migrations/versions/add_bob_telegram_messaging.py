"""Add Telegram messaging tables and channel columns for B.O.B.

Revision ID: add_bob_telegram
Revises: add_bob_actions
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = 'add_bob_telegram'
down_revision = 'add_bob_actions'
branch_labels = None
depends_on = None

CHANNEL_TABLE = 'agent_messaging_channels'
TOKEN_TABLE = 'messaging_link_tokens'
UPDATE_TABLE = 'messaging_inbound_updates'
RLS_TABLES = (CHANNEL_TABLE, TOKEN_TABLE, UPDATE_TABLE)


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())

    if CHANNEL_TABLE not in tables:
        op.create_table(
            CHANNEL_TABLE,
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('provider', sa.String(length=32), nullable=False),
            sa.Column('external_id', sa.String(length=64), nullable=False),
            sa.Column('chat_id', sa.String(length=64), nullable=False),
            sa.Column('linked_at', sa.DateTime(), nullable=False),
            sa.Column('disabled_at', sa.DateTime(), nullable=True),
            sa.Column('disable_reason', sa.String(length=100), nullable=True),
            sa.Column('pending_action_id', sa.Integer(), nullable=True),
            sa.Column('last_inbound_at', sa.DateTime(), nullable=True),
            sa.Column('daily_count', sa.Integer(), nullable=False,
                      server_default='0'),
            sa.Column('daily_count_date', sa.Date(), nullable=True),
            sa.Column('proactive_daily_count', sa.Integer(), nullable=False,
                      server_default='0'),
            sa.Column('proactive_daily_count_date', sa.Date(), nullable=True),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['pending_action_id'], ['bob_actions.id'],
                                    ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('provider', 'external_id',
                                name='uq_messaging_provider_external'),
            sa.UniqueConstraint('user_id', 'provider',
                                name='uq_messaging_user_provider'),
        )
        with op.batch_alter_table(CHANNEL_TABLE) as batch:
            batch.create_index('ix_agent_messaging_channels_user_id', ['user_id'])
            batch.create_index('ix_agent_messaging_channels_organization_id',
                               ['organization_id'])
            batch.create_index('ix_agent_messaging_channels_provider',
                               ['provider'])
            batch.create_index('ix_messaging_channels_org_provider',
                               ['organization_id', 'provider'])

    if TOKEN_TABLE not in tables:
        op.create_table(
            TOKEN_TABLE,
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('provider', sa.String(length=32), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('token_hash'),
        )
        with op.batch_alter_table(TOKEN_TABLE) as batch:
            batch.create_index('ix_messaging_link_tokens_user_id', ['user_id'])
            batch.create_index('ix_messaging_link_tokens_organization_id',
                               ['organization_id'])
            batch.create_index('ix_messaging_link_tokens_token_hash',
                               ['token_hash'])

    if UPDATE_TABLE not in tables:
        op.create_table(
            UPDATE_TABLE,
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=True),
            sa.Column('provider', sa.String(length=32), nullable=False),
            sa.Column('external_update_id', sa.String(length=64), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('kind', sa.String(length=32), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('provider', 'external_update_id',
                                name='uq_messaging_inbound_update'),
        )
        with op.batch_alter_table(UPDATE_TABLE) as batch:
            batch.create_index('ix_messaging_inbound_updates_organization_id',
                               ['organization_id'])

    # chat_conversations.channel
    if 'chat_conversations' in tables:
        cols = {c['name'] for c in inspector.get_columns('chat_conversations')}
        if 'channel' not in cols:
            with op.batch_alter_table('chat_conversations') as batch:
                batch.add_column(sa.Column(
                    'channel', sa.String(length=32), nullable=False,
                    server_default='web',
                ))
                batch.create_index('ix_chat_conversations_channel', ['channel'])

    # user_notification_preferences.telegram_enabled
    if 'user_notification_preferences' in tables:
        cols = {c['name'] for c in
                inspector.get_columns('user_notification_preferences')}
        if 'telegram_enabled' not in cols:
            with op.batch_alter_table('user_notification_preferences') as batch:
                batch.add_column(sa.Column(
                    'telegram_enabled', sa.Boolean(), nullable=False,
                    server_default=sa.text('true'),
                ))

    if conn.dialect.name != 'postgresql':
        return

    for table in RLS_TABLES:
        conn.execute(text(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY'))
        conn.execute(text(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY'))
        conn.execute(text(
            f'REVOKE ALL ON TABLE public.{table} FROM anon, authenticated'
        ))
        conn.execute(text(
            f'DROP POLICY IF EXISTS tenant_isolation_{table} ON public.{table}'
        ))
        # messaging_inbound_updates.organization_id is nullable (unbound
        # updates have no org yet), so the policy allows NULL through the
        # USING clause while still scoping tenant rows.
        if table == UPDATE_TABLE:
            conn.execute(text(f'''
                CREATE POLICY tenant_isolation_{table} ON public.{table}
                FOR ALL
                USING (
                    organization_id IS NULL
                    OR organization_id = current_setting(
                        'app.current_org_id', true
                    )::int
                )
                WITH CHECK (
                    organization_id IS NULL
                    OR organization_id = current_setting(
                        'app.current_org_id', true
                    )::int
                )
            '''))
        else:
            conn.execute(text(f'''
                CREATE POLICY tenant_isolation_{table} ON public.{table}
                FOR ALL
                USING (
                    organization_id = current_setting(
                        'app.current_org_id', true
                    )::int
                )
                WITH CHECK (
                    organization_id = current_setting(
                        'app.current_org_id', true
                    )::int
                )
            '''))


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())

    if conn.dialect.name == 'postgresql':
        for table in RLS_TABLES:
            if table in tables:
                conn.execute(text(
                    f'DROP POLICY IF EXISTS tenant_isolation_{table} '
                    f'ON public.{table}'
                ))

    if 'user_notification_preferences' in tables:
        cols = {c['name'] for c in
                inspector.get_columns('user_notification_preferences')}
        if 'telegram_enabled' in cols:
            with op.batch_alter_table('user_notification_preferences') as batch:
                batch.drop_column('telegram_enabled')

    if 'chat_conversations' in tables:
        cols = {c['name'] for c in inspector.get_columns('chat_conversations')}
        if 'channel' in cols:
            with op.batch_alter_table('chat_conversations') as batch:
                batch.drop_index('ix_chat_conversations_channel')
                batch.drop_column('channel')

    for table in (UPDATE_TABLE, TOKEN_TABLE, CHANNEL_TABLE):
        if table in tables:
            op.drop_table(table)
