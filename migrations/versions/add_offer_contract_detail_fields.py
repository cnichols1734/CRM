"""Add seller offer and contract detail fields

Revision ID: add_offer_contract_detail_fields
Revises: widen_seller_contract_survey_choice
Create Date: 2026-04-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_offer_contract_detail_fields'
down_revision = 'widen_seller_contract_survey_choice'
branch_labels = None
depends_on = None


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    return column_name in {column['name'] for column in inspect(conn).get_columns(table_name)}


def _add_column_if_missing(conn, table_name, column):
    if _table_exists(conn, table_name) and not _column_exists(conn, table_name, column.name):
        op.add_column(table_name, column)


def _drop_column_if_exists(conn, table_name, column_name):
    if _column_exists(conn, table_name, column_name):
        op.drop_column(table_name, column_name)


def upgrade():
    conn = op.get_bind()

    offer_columns = [
        sa.Column('financing_amount', sa.Numeric(12, 2)),
        sa.Column('survey_furnished_by', sa.Text()),
        sa.Column('residential_service_contract', sa.Text()),
        sa.Column('buyer_agent_commission_percent', sa.Numeric(6, 3)),
        sa.Column('buyer_agent_commission_flat', sa.Numeric(12, 2)),
    ]
    for column in offer_columns:
        _add_column_if_missing(conn, 'seller_offers', column)

    contract_columns = [
        sa.Column('financing_type', sa.String(length=100)),
        sa.Column('cash_down_payment', sa.Numeric(12, 2)),
        sa.Column('financing_amount', sa.Numeric(12, 2)),
        sa.Column('seller_concessions_amount', sa.Numeric(12, 2)),
        sa.Column('survey_furnished_by', sa.Text()),
        sa.Column('residential_service_contract', sa.Text()),
        sa.Column('buyer_agent_commission_percent', sa.Numeric(6, 3)),
        sa.Column('buyer_agent_commission_flat', sa.Numeric(12, 2)),
    ]
    for column in contract_columns:
        _add_column_if_missing(conn, 'seller_accepted_contracts', column)


def downgrade():
    conn = op.get_bind()

    for column_name in (
        'financing_amount',
        'survey_furnished_by',
        'residential_service_contract',
        'buyer_agent_commission_percent',
        'buyer_agent_commission_flat',
    ):
        _drop_column_if_exists(conn, 'seller_offers', column_name)

    for column_name in (
        'financing_type',
        'cash_down_payment',
        'financing_amount',
        'seller_concessions_amount',
        'survey_furnished_by',
        'residential_service_contract',
        'buyer_agent_commission_percent',
        'buyer_agent_commission_flat',
    ):
        _drop_column_if_exists(conn, 'seller_accepted_contracts', column_name)
