"""Add bob_actions table for B.O.B. tool confirmations, audit, and undo.

Revision ID: add_bob_actions
Revises: secure_activation_events
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = 'add_bob_actions'
down_revision = 'secure_activation_events'
branch_labels = None
depends_on = None

TABLE = 'bob_actions'


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('conversation_id', sa.Integer(), nullable=True),
            sa.Column('tool_name', sa.String(length=64), nullable=False),
            sa.Column('arguments', sa.JSON(), nullable=False),
            sa.Column('preview', sa.JSON(), nullable=True),
            sa.Column('result', sa.JSON(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False,
                      server_default='executed'),
            sa.Column('summary', sa.String(length=300), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('surface', sa.String(length=32), nullable=False,
                      server_default='bob_chat'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('executed_at', sa.DateTime(), nullable=True),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversations.id'],
                                    ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )

        with op.batch_alter_table(TABLE) as batch:
            batch.create_index('ix_bob_actions_organization_id', ['organization_id'])
            batch.create_index('ix_bob_actions_user_id', ['user_id'])
            batch.create_index('ix_bob_actions_conversation_id', ['conversation_id'])
            batch.create_index('ix_bob_actions_status', ['status'])
            batch.create_index('ix_bob_actions_created_at', ['created_at'])
            # Confirm/undo lookups always filter by owner first.
            batch.create_index('ix_bob_actions_user_status',
                               ['user_id', 'status', 'created_at'])

    if conn.dialect.name != 'postgresql':
        return

    # Same tenant isolation every other org-scoped table gets. The audit trail
    # of AI writes must not be readable across tenants.
    conn.execute(text(f'ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY'))
    conn.execute(text(f'ALTER TABLE public.{TABLE} FORCE ROW LEVEL SECURITY'))
    conn.execute(text(
        f'REVOKE ALL ON TABLE public.{TABLE} FROM anon, authenticated'
    ))
    conn.execute(text(f'DROP POLICY IF EXISTS tenant_isolation_{TABLE} ON public.{TABLE}'))
    conn.execute(text(f'''
        CREATE POLICY tenant_isolation_{TABLE} ON public.{TABLE}
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id', true)::int)
        WITH CHECK (organization_id = current_setting('app.current_org_id', true)::int)
    '''))


def downgrade():
    conn = op.get_bind()

    if conn.dialect.name == 'postgresql':
        conn.execute(text(
            f'DROP POLICY IF EXISTS tenant_isolation_{TABLE} ON public.{TABLE}'
        ))

    inspector = inspect(conn)
    if TABLE in inspector.get_table_names():
        op.drop_table(TABLE)
