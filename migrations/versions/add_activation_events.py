"""add activation_events table

Tracks new-user activation milestones (signup, first contact, etc.) so we can
measure the activation funnel that is currently invisible. Internal product
analytics only -- no RLS, app-level scoped.

Revision ID: add_activation_events
Revises: add_core_table_indexes
Create Date: 2026-05-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'add_activation_events'
down_revision = 'add_core_table_indexes'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if 'activation_events' not in tables:
        op.create_table(
            'activation_events',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=True),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('event', sa.String(length=50), nullable=False),
            sa.Column('event_data', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    name='fk_activation_events_organization_id',
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'],
                                    name='fk_activation_events_user_id',
                                    ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id', name='pk_activation_events'),
        )
        op.create_index('ix_activation_events_organization_id',
                        'activation_events', ['organization_id'], unique=False)
        op.create_index('ix_activation_events_user_id',
                        'activation_events', ['user_id'], unique=False)
        op.create_index('ix_activation_events_event',
                        'activation_events', ['event'], unique=False)
        op.create_index('ix_activation_events_created_at',
                        'activation_events', ['created_at'], unique=False)


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if 'activation_events' in tables:
        op.drop_index('ix_activation_events_created_at', table_name='activation_events')
        op.drop_index('ix_activation_events_event', table_name='activation_events')
        op.drop_index('ix_activation_events_user_id', table_name='activation_events')
        op.drop_index('ix_activation_events_organization_id', table_name='activation_events')
        op.drop_table('activation_events')
