"""Add magic inbox columns and inbound_messages table

Revision ID: add_inbox_and_inbound_messages
Revises: add_notification_tables
Create Date: 2026-04-25 11:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_inbox_and_inbound_messages'
down_revision = 'add_notification_tables'
branch_labels = None
depends_on = None


def _has_column(inspector, table, column):
    return any(c['name'] == column for c in inspector.get_columns(table))


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    # --- User columns -------------------------------------------------------
    if 'user' in tables:
        with op.batch_alter_table('user') as batch_op:
            if not _has_column(inspector, 'user', 'inbox_address'):
                batch_op.add_column(sa.Column('inbox_address', sa.String(length=200),
                                              nullable=True))
            if not _has_column(inspector, 'user', 'inbox_token'):
                batch_op.add_column(sa.Column('inbox_token', sa.String(length=16),
                                              nullable=True))
            if not _has_column(inspector, 'user', 'has_seen_inbox_onboarding'):
                batch_op.add_column(sa.Column('has_seen_inbox_onboarding',
                                              sa.Boolean(), nullable=True,
                                              server_default=sa.text('0')))

        # Re-inspect after the column adds so the unique index check is accurate.
        inspector = inspect(conn)
        existing_indexes = {ix['name'] for ix in inspector.get_indexes('user')}
        if 'ix_user_inbox_address' not in existing_indexes:
            op.create_index('ix_user_inbox_address', 'user',
                            ['inbox_address'], unique=True)
        if 'ix_user_inbox_token' not in existing_indexes:
            op.create_index('ix_user_inbox_token', 'user',
                            ['inbox_token'], unique=True)

    # --- inbound_messages table --------------------------------------------
    if 'inbound_messages' not in tables:
        op.create_table(
            'inbound_messages',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('recipient_address', sa.String(length=200), nullable=False),
            sa.Column('sender_email', sa.String(length=200), nullable=True),
            sa.Column('subject', sa.String(length=500), nullable=True),
            sa.Column('plus_alias', sa.String(length=100), nullable=True),
            sa.Column('raw_storage_path', sa.String(length=500), nullable=True),
            sa.Column('source_kind', sa.String(length=20), nullable=False,
                      server_default='text'),
            sa.Column('ai_model', sa.String(length=60), nullable=True),
            sa.Column('ai_tokens_in', sa.Integer(), nullable=True),
            sa.Column('ai_tokens_out', sa.Integer(), nullable=True),
            sa.Column('ai_cost_cents', sa.Numeric(10, 4), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False,
                      server_default='received'),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_contact_ids', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('processed_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id', name='pk_inbound_messages'),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    name='fk_inbound_messages_org',
                                    ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'],
                                    name='fk_inbound_messages_user',
                                    ondelete='CASCADE'),
        )
        op.create_index('ix_inbound_messages_org_id', 'inbound_messages',
                        ['organization_id'], unique=False)
        op.create_index('ix_inbound_messages_user_id', 'inbound_messages',
                        ['user_id'], unique=False)
        op.create_index('ix_inbound_messages_recipient_address',
                        'inbound_messages', ['recipient_address'], unique=False)
        op.create_index('ix_inbound_messages_sender_email',
                        'inbound_messages', ['sender_email'], unique=False)
        op.create_index('ix_inbound_messages_source_kind',
                        'inbound_messages', ['source_kind'], unique=False)
        op.create_index('ix_inbound_messages_status', 'inbound_messages',
                        ['status'], unique=False)
        op.create_index('ix_inbound_messages_created_at',
                        'inbound_messages', ['created_at'], unique=False)


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if 'inbound_messages' in tables:
        op.drop_index('ix_inbound_messages_created_at',
                      table_name='inbound_messages')
        op.drop_index('ix_inbound_messages_status', table_name='inbound_messages')
        op.drop_index('ix_inbound_messages_source_kind',
                      table_name='inbound_messages')
        op.drop_index('ix_inbound_messages_sender_email',
                      table_name='inbound_messages')
        op.drop_index('ix_inbound_messages_recipient_address',
                      table_name='inbound_messages')
        op.drop_index('ix_inbound_messages_user_id', table_name='inbound_messages')
        op.drop_index('ix_inbound_messages_org_id', table_name='inbound_messages')
        op.drop_table('inbound_messages')

    if 'user' in tables:
        existing_indexes = {ix['name'] for ix in inspector.get_indexes('user')}
        if 'ix_user_inbox_token' in existing_indexes:
            op.drop_index('ix_user_inbox_token', table_name='user')
        if 'ix_user_inbox_address' in existing_indexes:
            op.drop_index('ix_user_inbox_address', table_name='user')

        with op.batch_alter_table('user') as batch_op:
            if _has_column(inspector, 'user', 'has_seen_inbox_onboarding'):
                batch_op.drop_column('has_seen_inbox_onboarding')
            if _has_column(inspector, 'user', 'inbox_token'):
                batch_op.drop_column('inbox_token')
            if _has_column(inspector, 'user', 'inbox_address'):
                batch_op.drop_column('inbox_address')
