"""Add non_realty_items to seller offers

Revision ID: add_offer_non_realty_items
Revises: add_agent_api_tokens
Create Date: 2026-09-10

The Non-Realty Items Addendum (TREC 51) lists the personal property a buyer
wants conveyed. Stored one item per line, matching the other Text-typed
contract detail columns on the offer.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_offer_non_realty_items'
down_revision = 'add_agent_api_tokens'
branch_labels = None
depends_on = None


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    return column_name in {column['name'] for column in inspect(conn).get_columns(table_name)}


def upgrade():
    conn = op.get_bind()
    if _table_exists(conn, 'seller_offers') and not _column_exists(conn, 'seller_offers', 'non_realty_items'):
        op.add_column('seller_offers', sa.Column('non_realty_items', sa.Text()))


def downgrade():
    conn = op.get_bind()
    if _column_exists(conn, 'seller_offers', 'non_realty_items'):
        op.drop_column('seller_offers', 'non_realty_items')
