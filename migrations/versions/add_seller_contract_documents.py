"""Add seller contract documents

Revision ID: add_seller_contract_documents
Revises: add_transaction_document_split_lineage
Create Date: 2026-04-26

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_seller_contract_documents'
down_revision = 'add_transaction_document_split_lineage'
branch_labels = None
depends_on = None


TABLE_NAME = 'seller_contract_documents'


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _index_exists(conn, table_name, index_name):
    if not _table_exists(conn, table_name):
        return False
    return index_name in {idx['name'] for idx in inspect(conn).get_indexes(table_name)}


def _create_index_if_missing(conn, index_name, table_name, columns, unique=False):
    if not _index_exists(conn, table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _enable_rls(conn):
    if conn.dialect.name != 'postgresql':
        return

    op.execute(f'ALTER TABLE {TABLE_NAME} ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE {TABLE_NAME} FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{TABLE_NAME} ON {TABLE_NAME}')
    op.execute(f"""
        CREATE POLICY tenant_isolation_{TABLE_NAME} ON {TABLE_NAME}
        FOR ALL
        USING (
            organization_id = current_setting(
                'app.current_org_id', true
            )::integer
        )
        WITH CHECK (
            organization_id = current_setting(
                'app.current_org_id', true
            )::integer
        )
    """)


def _disable_rls(conn):
    if conn.dialect.name != 'postgresql':
        return

    if _table_exists(conn, TABLE_NAME):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{TABLE_NAME} ON {TABLE_NAME}')
        op.execute(f'ALTER TABLE {TABLE_NAME} DISABLE ROW LEVEL SECURITY')


def upgrade():
    conn = op.get_bind()

    if not _table_exists(conn, TABLE_NAME):
        op.create_table(
            TABLE_NAME,
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('accepted_contract_id', sa.Integer(), sa.ForeignKey('seller_accepted_contracts.id', ondelete='CASCADE'), nullable=False),
            sa.Column('transaction_document_id', sa.Integer(), sa.ForeignKey('transaction_documents.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('document_type', sa.String(length=100), nullable=False),
            sa.Column('display_name', sa.String(length=200), nullable=False),
            sa.Column('is_primary_contract_document', sa.Boolean(), server_default=sa.text('false')),
            sa.Column('extraction_summary', sa.JSON()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )

    index_specs = (
        ('ix_seller_contract_documents_org_id', TABLE_NAME, ['organization_id']),
        ('ix_seller_contract_documents_transaction_id', TABLE_NAME, ['transaction_id']),
        ('ix_seller_contract_documents_contract_id', TABLE_NAME, ['accepted_contract_id']),
        ('ix_seller_contract_documents_document_id', TABLE_NAME, ['transaction_document_id']),
        ('ix_seller_contract_documents_type', TABLE_NAME, ['document_type']),
    )
    for index_name, table_name, columns in index_specs:
        _create_index_if_missing(conn, index_name, table_name, columns)

    _enable_rls(conn)


def downgrade():
    conn = op.get_bind()
    _disable_rls(conn)

    if _table_exists(conn, TABLE_NAME):
        op.drop_table(TABLE_NAME)
