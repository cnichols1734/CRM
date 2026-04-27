"""Add parent/page lineage columns for AI-split transaction documents

Revision ID: add_transaction_document_split_lineage
Revises: add_offer_contract_detail_fields
Create Date: 2026-04-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_transaction_document_split_lineage'
down_revision = 'add_offer_contract_detail_fields'
branch_labels = None
depends_on = None


TABLE_NAME = 'transaction_documents'


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    return column_name in {column['name'] for column in inspect(conn).get_columns(table_name)}


def _index_exists(conn, table_name, index_name):
    if not _table_exists(conn, table_name):
        return False
    return index_name in {idx['name'] for idx in inspect(conn).get_indexes(table_name)}


def _foreign_key_exists(conn, table_name, fk_name):
    if not _table_exists(conn, table_name):
        return False
    return fk_name in {fk.get('name') for fk in inspect(conn).get_foreign_keys(table_name) if fk.get('name')}


def upgrade():
    conn = op.get_bind()

    if _table_exists(conn, TABLE_NAME):
        if not _column_exists(conn, TABLE_NAME, 'parent_document_id'):
            op.add_column(
                TABLE_NAME,
                sa.Column('parent_document_id', sa.Integer(), nullable=True),
            )
        if not _column_exists(conn, TABLE_NAME, 'page_start'):
            op.add_column(TABLE_NAME, sa.Column('page_start', sa.Integer(), nullable=True))
        if not _column_exists(conn, TABLE_NAME, 'page_end'):
            op.add_column(TABLE_NAME, sa.Column('page_end', sa.Integer(), nullable=True))
        if not _column_exists(conn, TABLE_NAME, 'split_source'):
            op.add_column(TABLE_NAME, sa.Column('split_source', sa.String(length=50), nullable=True))

        if not _foreign_key_exists(conn, TABLE_NAME, 'fk_transaction_documents_parent_document_id'):
            try:
                op.create_foreign_key(
                    'fk_transaction_documents_parent_document_id',
                    TABLE_NAME,
                    TABLE_NAME,
                    ['parent_document_id'],
                    ['id'],
                    ondelete='SET NULL',
                )
            except Exception:
                # SQLite (used for local dev) does not support adding FKs after the fact.
                pass

        if not _index_exists(conn, TABLE_NAME, 'ix_transaction_documents_parent_document_id'):
            op.create_index(
                'ix_transaction_documents_parent_document_id',
                TABLE_NAME,
                ['parent_document_id'],
            )


def downgrade():
    conn = op.get_bind()

    if _index_exists(conn, TABLE_NAME, 'ix_transaction_documents_parent_document_id'):
        op.drop_index('ix_transaction_documents_parent_document_id', table_name=TABLE_NAME)

    if _foreign_key_exists(conn, TABLE_NAME, 'fk_transaction_documents_parent_document_id'):
        try:
            op.drop_constraint(
                'fk_transaction_documents_parent_document_id',
                TABLE_NAME,
                type_='foreignkey',
            )
        except Exception:
            pass

    for column_name in ('split_source', 'page_end', 'page_start', 'parent_document_id'):
        if _column_exists(conn, TABLE_NAME, column_name):
            op.drop_column(TABLE_NAME, column_name)
