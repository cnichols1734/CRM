"""Add transaction_id and is_auto_checkin to tasks

Revision ID: add_task_transaction_link
Revises: add_seller_contract_documents
Create Date: 2026-04-28

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_task_transaction_link'
down_revision = 'add_seller_contract_documents'
branch_labels = None
depends_on = None


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    columns = {col['name'] for col in inspect(conn).get_columns(table_name)}
    return column_name in columns


def _index_exists(conn, table_name, index_name):
    if not _table_exists(conn, table_name):
        return False
    return index_name in {idx['name'] for idx in inspect(conn).get_indexes(table_name)}


TABLE = 'task'


def upgrade():
    conn = op.get_bind()

    if not _table_exists(conn, TABLE):
        return

    if not _column_exists(conn, TABLE, 'transaction_id'):
        op.add_column(TABLE, sa.Column(
            'transaction_id', sa.Integer(),
            sa.ForeignKey('transactions.id', ondelete='SET NULL'),
            nullable=True,
        ))

    if not _column_exists(conn, TABLE, 'is_auto_checkin'):
        op.add_column(TABLE, sa.Column(
            'is_auto_checkin', sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ))

    if not _index_exists(conn, TABLE, 'ix_task_transaction_id'):
        op.create_index('ix_task_transaction_id', TABLE, ['transaction_id'])


def downgrade():
    conn = op.get_bind()

    if _index_exists(conn, TABLE, 'ix_task_transaction_id'):
        op.drop_index('ix_task_transaction_id', table_name=TABLE)

    if _column_exists(conn, TABLE, 'is_auto_checkin'):
        op.drop_column(TABLE, 'is_auto_checkin')

    if _column_exists(conn, TABLE, 'transaction_id'):
        op.drop_column(TABLE, 'transaction_id')
