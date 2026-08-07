"""Add due_at_manual_override to transaction_requirements

Revision ID: add_req_manual_due
Revises: add_org_req_templates
Create Date: 2026-08-06

Agents can set checklist due dates by hand; automated deadline recompute
must not overwrite a manually chosen date.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'add_req_manual_due'
down_revision = 'add_org_req_templates'
branch_labels = None
depends_on = None

TABLE = 'transaction_requirements'
COLUMN = 'due_at_manual_override'


def _column_exists(conn, table_name, column_name):
    columns = [c['name'] for c in inspect(conn).get_columns(table_name)]
    return column_name in columns


def upgrade():
    conn = op.get_bind()
    if TABLE in inspect(conn).get_table_names() and not _column_exists(conn, TABLE, COLUMN):
        op.add_column(
            TABLE,
            sa.Column(COLUMN, sa.Boolean(), nullable=True, server_default=sa.false()),
        )


def downgrade():
    conn = op.get_bind()
    if TABLE in inspect(conn).get_table_names() and _column_exists(conn, TABLE, COLUMN):
        op.drop_column(TABLE, COLUMN)
