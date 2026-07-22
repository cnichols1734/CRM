"""Extend daily_todo_list for Daily Briefing rebuild

Revision ID: add_daily_briefing_fields
Revises: add_per_user_contact_groups
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = 'add_daily_briefing_fields'
down_revision = 'add_per_user_contact_groups'
branch_labels = None
depends_on = None


def _existing_columns(inspector, table):
    if table not in inspector.get_table_names():
        return set()
    return {col['name'] for col in inspector.get_columns(table)}


def _existing_indexes(inspector, table):
    if table not in inspector.get_table_names():
        return set()
    return {idx['name'] for idx in inspector.get_indexes(table)}


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    cols = _existing_columns(inspector, 'daily_todo_list')
    indexes = _existing_indexes(inspector, 'daily_todo_list')

    if 'plan_date' not in cols:
        op.add_column('daily_todo_list', sa.Column('plan_date', sa.Date(), nullable=True))
    if 'status' not in cols:
        op.add_column(
            'daily_todo_list',
            sa.Column('status', sa.String(length=20), nullable=False, server_default='ready'),
        )
    if 'viewed_at' not in cols:
        op.add_column('daily_todo_list', sa.Column('viewed_at', sa.DateTime(), nullable=True))
    if 'item_states' not in cols:
        op.add_column(
            'daily_todo_list',
            sa.Column('item_states', sa.JSON(), nullable=False, server_default=text("'{}'")),
        )
    if 'model_used' not in cols:
        op.add_column('daily_todo_list', sa.Column('model_used', sa.String(length=64), nullable=True))
    if 'error' not in cols:
        op.add_column('daily_todo_list', sa.Column('error', sa.Text(), nullable=True))

    # Refresh inspector after adds
    inspector = inspect(conn)
    indexes = _existing_indexes(inspector, 'daily_todo_list')
    if 'ix_daily_todo_list_plan_date' not in indexes:
        op.create_index('ix_daily_todo_list_plan_date', 'daily_todo_list', ['plan_date'])
    if 'ix_daily_todo_list_user_plan_date' not in indexes:
        op.create_index(
            'ix_daily_todo_list_user_plan_date',
            'daily_todo_list',
            ['user_id', 'plan_date'],
        )

    # Backfill plan_date from generated_at
    conn.execute(text("""
        UPDATE daily_todo_list
        SET plan_date = DATE(generated_at)
        WHERE plan_date IS NULL AND generated_at IS NOT NULL
    """))


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    indexes = _existing_indexes(inspector, 'daily_todo_list')
    cols = _existing_columns(inspector, 'daily_todo_list')

    if 'ix_daily_todo_list_user_plan_date' in indexes:
        op.drop_index('ix_daily_todo_list_user_plan_date', table_name='daily_todo_list')
    if 'ix_daily_todo_list_plan_date' in indexes:
        op.drop_index('ix_daily_todo_list_plan_date', table_name='daily_todo_list')

    for col in ('error', 'model_used', 'item_states', 'viewed_at', 'status', 'plan_date'):
        if col in cols:
            op.drop_column('daily_todo_list', col)
