"""Add public MLS listing URL on transactions.

Revision ID: add_tx_mls_listing_url
Revises: add_client_app_invite_brand
Create Date: 2026-08-20

Agents store the live listing link here. The client iPhone Home screen
shows it in place of Message when the URL is set.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_tx_mls_listing_url'
down_revision = 'add_client_app_invite_brand'
branch_labels = None
depends_on = None


def _column_exists(conn, table_name, column_name):
    tables = inspect(conn).get_table_names()
    if table_name not in tables:
        return False
    return column_name in {
        col['name'] for col in inspect(conn).get_columns(table_name)
    }


def upgrade():
    conn = op.get_bind()
    if not _column_exists(conn, 'transactions', 'mls_listing_url'):
        op.add_column(
            'transactions',
            sa.Column('mls_listing_url', sa.String(length=500), nullable=True),
        )


def downgrade():
    conn = op.get_bind()
    if _column_exists(conn, 'transactions', 'mls_listing_url'):
        op.drop_column('transactions', 'mls_listing_url')
