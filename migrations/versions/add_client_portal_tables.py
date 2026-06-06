"""Add client portal tables (client_portal_access, portal_messages)

Revision ID: add_client_portal_tables
Revises: add_activation_events
Create Date: 2026-06-05 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_client_portal_tables'
down_revision = 'add_activation_events'
branch_labels = None
depends_on = None


# Only portal_messages carries tenant RLS. client_portal_access is an
# auth/bootstrap table (looked up by its globally-unique token BEFORE an org
# context exists), exactly like the user table in Magic Inbox — so it is
# intentionally excluded from RLS to avoid a chicken-and-egg lookup.
TENANT_TABLES = (
    'portal_messages',
)


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if 'client_portal_access' not in tables:
        op.create_table(
            'client_portal_access',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('transaction_id', sa.Integer(), nullable=False),
            sa.Column('participant_id', sa.Integer(), nullable=False),
            sa.Column('token', sa.String(length=64), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False,
                      server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.Column('last_viewed_at', sa.DateTime(), nullable=True),
            sa.Column('view_count', sa.Integer(), nullable=False,
                      server_default=sa.text('0')),
            sa.PrimaryKeyConstraint('id', name='pk_client_portal_access'),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    name='fk_client_portal_access_org',
                                    ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'],
                                    name='fk_client_portal_access_tx',
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['participant_id'],
                                    ['transaction_participants.id'],
                                    name='fk_client_portal_access_participant',
                                    ondelete='CASCADE'),
            sa.UniqueConstraint('token', name='uq_client_portal_access_token'),
        )
        op.create_index('ix_client_portal_access_org_id',
                        'client_portal_access', ['organization_id'], unique=False)
        op.create_index('ix_client_portal_access_tx_id',
                        'client_portal_access', ['transaction_id'], unique=False)
        op.create_index('ix_client_portal_access_participant_id',
                        'client_portal_access', ['participant_id'], unique=False)
        op.create_index('ix_client_portal_access_token',
                        'client_portal_access', ['token'], unique=True)

    if 'portal_messages' not in tables:
        op.create_table(
            'portal_messages',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('transaction_id', sa.Integer(), nullable=False),
            sa.Column('participant_id', sa.Integer(), nullable=False),
            sa.Column('sender', sa.String(length=20), nullable=False,
                      server_default='agent'),
            sa.Column('kind', sa.String(length=20), nullable=False,
                      server_default='message'),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('attachment_path', sa.String(length=500), nullable=True),
            sa.Column('attachment_name', sa.String(length=255), nullable=True),
            sa.Column('author_user_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('read_by_agent_at', sa.DateTime(), nullable=True),
            sa.Column('read_by_client_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id', name='pk_portal_messages'),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    name='fk_portal_messages_org',
                                    ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'],
                                    name='fk_portal_messages_tx',
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['participant_id'],
                                    ['transaction_participants.id'],
                                    name='fk_portal_messages_participant',
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['author_user_id'], ['user.id'],
                                    name='fk_portal_messages_author',
                                    ondelete='SET NULL'),
        )
        op.create_index('ix_portal_messages_org_id', 'portal_messages',
                        ['organization_id'], unique=False)
        op.create_index('ix_portal_messages_tx_id', 'portal_messages',
                        ['transaction_id'], unique=False)
        op.create_index('ix_portal_messages_participant_id', 'portal_messages',
                        ['participant_id'], unique=False)
        op.create_index('ix_portal_messages_created_at', 'portal_messages',
                        ['created_at'], unique=False)

    # Row Level Security (PostgreSQL only) — mirrors enable_magic_inbox_rls.py
    if conn.dialect.name == 'postgresql':
        for table in TENANT_TABLES:
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}')
            op.execute(f"""
                CREATE POLICY tenant_isolation_{table} ON {table}
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
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if conn.dialect.name == 'postgresql':
        for table in TENANT_TABLES:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}')

    if 'portal_messages' in tables:
        op.drop_index('ix_portal_messages_created_at', table_name='portal_messages')
        op.drop_index('ix_portal_messages_participant_id', table_name='portal_messages')
        op.drop_index('ix_portal_messages_tx_id', table_name='portal_messages')
        op.drop_index('ix_portal_messages_org_id', table_name='portal_messages')
        op.drop_table('portal_messages')

    if 'client_portal_access' in tables:
        op.drop_index('ix_client_portal_access_token', table_name='client_portal_access')
        op.drop_index('ix_client_portal_access_participant_id', table_name='client_portal_access')
        op.drop_index('ix_client_portal_access_tx_id', table_name='client_portal_access')
        op.drop_index('ix_client_portal_access_org_id', table_name='client_portal_access')
        op.drop_table('client_portal_access')
