"""Widen seller accepted contract survey choice

Revision ID: widen_seller_contract_survey_choice
Revises: add_seller_transaction_workflow
Create Date: 2026-04-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'widen_seller_contract_survey_choice'
down_revision = 'add_seller_transaction_workflow'
branch_labels = None
depends_on = None


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def upgrade():
    conn = op.get_bind()
    if not _table_exists(conn, 'seller_accepted_contracts'):
        return

    with op.batch_alter_table('seller_accepted_contracts') as batch_op:
        batch_op.alter_column(
            'survey_choice',
            existing_type=sa.String(length=100),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade():
    conn = op.get_bind()
    if not _table_exists(conn, 'seller_accepted_contracts'):
        return

    with op.batch_alter_table('seller_accepted_contracts') as batch_op:
        batch_op.alter_column(
            'survey_choice',
            existing_type=sa.Text(),
            type_=sa.String(length=100),
            existing_nullable=True,
        )
