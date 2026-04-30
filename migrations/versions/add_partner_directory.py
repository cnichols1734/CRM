"""Add org-wide partner directory tables

Revision ID: add_partner_directory
Revises: add_task_transaction_link
Create Date: 2026-04-30

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_partner_directory'
down_revision = 'add_task_transaction_link'
branch_labels = None
depends_on = None


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

    if not _table_exists(conn, 'partner_organizations'):
        op.create_table(
            'partner_organizations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('normalized_name', sa.String(length=200), nullable=False),
            sa.Column('partner_type', sa.String(length=50), nullable=False, server_default='other'),
            sa.Column('phone', sa.String(length=30), nullable=True),
            sa.Column('normalized_phone', sa.String(length=30), nullable=True),
            sa.Column('email', sa.String(length=200), nullable=True),
            sa.Column('website', sa.String(length=300), nullable=True),
            sa.Column('street_address', sa.String(length=200), nullable=True),
            sa.Column('city', sa.String(length=100), nullable=True),
            sa.Column('state', sa.String(length=50), nullable=True),
            sa.Column('zip_code', sa.String(length=20), nullable=True),
            sa.Column('normalized_address', sa.String(length=500), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.UniqueConstraint('organization_id', 'normalized_name', name='uq_partner_org_normalized_name'),
        )

    if not _index_exists(conn, 'partner_organizations', 'ix_partner_organizations_organization_id'):
        op.create_index('ix_partner_organizations_organization_id', 'partner_organizations', ['organization_id'])
    if not _index_exists(conn, 'partner_organizations', 'ix_partner_organizations_partner_type'):
        op.create_index('ix_partner_organizations_partner_type', 'partner_organizations', ['partner_type'])
    if not _index_exists(conn, 'partner_organizations', 'ix_partner_organizations_normalized_address'):
        op.create_index('ix_partner_organizations_normalized_address', 'partner_organizations', ['normalized_address'])

    if not _table_exists(conn, 'partner_contacts'):
        op.create_table(
            'partner_contacts',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('partner_organization_id', sa.Integer(), sa.ForeignKey('partner_organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
            sa.Column('first_name', sa.String(length=80), nullable=False),
            sa.Column('last_name', sa.String(length=80), nullable=False),
            sa.Column('normalized_full_name', sa.String(length=180), nullable=False),
            sa.Column('title', sa.String(length=120), nullable=True),
            sa.Column('email', sa.String(length=200), nullable=True),
            sa.Column('normalized_email', sa.String(length=200), nullable=True),
            sa.Column('phone', sa.String(length=30), nullable=True),
            sa.Column('normalized_phone', sa.String(length=30), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('is_primary_contact', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.UniqueConstraint('partner_organization_id', 'normalized_full_name', name='uq_partner_contact_org_full_name'),
        )

    if not _index_exists(conn, 'partner_contacts', 'ix_partner_contacts_organization_id'):
        op.create_index('ix_partner_contacts_organization_id', 'partner_contacts', ['organization_id'])
    if not _index_exists(conn, 'partner_contacts', 'ix_partner_contacts_partner_organization_id'):
        op.create_index('ix_partner_contacts_partner_organization_id', 'partner_contacts', ['partner_organization_id'])

    if _table_exists(conn, 'transaction_participants'):
        if not _column_exists(conn, 'transaction_participants', 'partner_organization_id'):
            op.add_column('transaction_participants', sa.Column('partner_organization_id', sa.Integer(), nullable=True))
        if not _column_exists(conn, 'transaction_participants', 'partner_contact_id'):
            op.add_column('transaction_participants', sa.Column('partner_contact_id', sa.Integer(), nullable=True))

        if not _foreign_key_exists(conn, 'transaction_participants', 'fk_transaction_participants_partner_organization_id'):
            try:
                op.create_foreign_key(
                    'fk_transaction_participants_partner_organization_id',
                    'transaction_participants',
                    'partner_organizations',
                    ['partner_organization_id'],
                    ['id'],
                    ondelete='SET NULL',
                )
            except Exception:
                pass
        if not _foreign_key_exists(conn, 'transaction_participants', 'fk_transaction_participants_partner_contact_id'):
            try:
                op.create_foreign_key(
                    'fk_transaction_participants_partner_contact_id',
                    'transaction_participants',
                    'partner_contacts',
                    ['partner_contact_id'],
                    ['id'],
                    ondelete='SET NULL',
                )
            except Exception:
                pass

        if not _index_exists(conn, 'transaction_participants', 'ix_transaction_participants_partner_organization_id'):
            op.create_index('ix_transaction_participants_partner_organization_id', 'transaction_participants', ['partner_organization_id'])
        if not _index_exists(conn, 'transaction_participants', 'ix_transaction_participants_partner_contact_id'):
            op.create_index('ix_transaction_participants_partner_contact_id', 'transaction_participants', ['partner_contact_id'])


def downgrade():
    conn = op.get_bind()

    if _table_exists(conn, 'transaction_participants'):
        if _index_exists(conn, 'transaction_participants', 'ix_transaction_participants_partner_contact_id'):
            op.drop_index('ix_transaction_participants_partner_contact_id', table_name='transaction_participants')
        if _index_exists(conn, 'transaction_participants', 'ix_transaction_participants_partner_organization_id'):
            op.drop_index('ix_transaction_participants_partner_organization_id', table_name='transaction_participants')

        for fk_name in (
            'fk_transaction_participants_partner_contact_id',
            'fk_transaction_participants_partner_organization_id',
        ):
            if _foreign_key_exists(conn, 'transaction_participants', fk_name):
                try:
                    op.drop_constraint(fk_name, 'transaction_participants', type_='foreignkey')
                except Exception:
                    pass

        if _column_exists(conn, 'transaction_participants', 'partner_contact_id'):
            op.drop_column('transaction_participants', 'partner_contact_id')
        if _column_exists(conn, 'transaction_participants', 'partner_organization_id'):
            op.drop_column('transaction_participants', 'partner_organization_id')

    if _table_exists(conn, 'partner_contacts'):
        op.drop_table('partner_contacts')
    if _table_exists(conn, 'partner_organizations'):
        op.drop_table('partner_organizations')
