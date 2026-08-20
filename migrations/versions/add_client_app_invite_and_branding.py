"""Client app invite codes and org brand accent.

Revision ID: add_client_app_invite_brand
Revises: add_marketing_tables
Create Date: 2026-08-20

Adds:
- organizations.brand_accent (optional hex color for the client iPhone app)
- client_portal_access.invite_code (human grant for the app)
- client_portal_access.invite_expires_at
- client_portal_access.session_version (invalidates issued JWTs)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_client_app_invite_brand'
down_revision = 'add_marketing_tables'
branch_labels = None
depends_on = None


def _column_exists(conn, table_name, column_name):
    tables = inspect(conn).get_table_names()
    if table_name not in tables:
        return False
    return column_name in {
        col['name'] for col in inspect(conn).get_columns(table_name)
    }


def _index_exists(conn, table_name, index_name):
    tables = inspect(conn).get_table_names()
    if table_name not in tables:
        return False
    return index_name in {
        idx['name'] for idx in inspect(conn).get_indexes(table_name)
    }


def upgrade():
    conn = op.get_bind()

    if not _column_exists(conn, 'organizations', 'brand_accent'):
        op.add_column(
            'organizations',
            sa.Column('brand_accent', sa.String(length=7), nullable=True),
        )

    if not _column_exists(conn, 'client_portal_access', 'invite_code'):
        op.add_column(
            'client_portal_access',
            sa.Column('invite_code', sa.String(length=16), nullable=True),
        )
    if not _column_exists(conn, 'client_portal_access', 'invite_expires_at'):
        op.add_column(
            'client_portal_access',
            sa.Column('invite_expires_at', sa.DateTime(), nullable=True),
        )
    if not _column_exists(conn, 'client_portal_access', 'session_version'):
        op.add_column(
            'client_portal_access',
            sa.Column(
                'session_version',
                sa.Integer(),
                nullable=False,
                server_default=sa.text('1'),
            ),
        )

    if not _index_exists(conn, 'client_portal_access', 'uq_client_portal_access_invite_code'):
        op.create_index(
            'uq_client_portal_access_invite_code',
            'client_portal_access',
            ['invite_code'],
            unique=True,
        )


def downgrade():
    conn = op.get_bind()

    if _index_exists(conn, 'client_portal_access', 'uq_client_portal_access_invite_code'):
        op.drop_index(
            'uq_client_portal_access_invite_code',
            table_name='client_portal_access',
        )
    if _column_exists(conn, 'client_portal_access', 'session_version'):
        op.drop_column('client_portal_access', 'session_version')
    if _column_exists(conn, 'client_portal_access', 'invite_expires_at'):
        op.drop_column('client_portal_access', 'invite_expires_at')
    if _column_exists(conn, 'client_portal_access', 'invite_code'):
        op.drop_column('client_portal_access', 'invite_code')
    if _column_exists(conn, 'organizations', 'brand_accent'):
        op.drop_column('organizations', 'brand_accent')
