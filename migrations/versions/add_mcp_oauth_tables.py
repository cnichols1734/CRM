"""Add MCP OAuth tables and org admin-only flag.

Revision ID: add_mcp_oauth_tables
Revises: add_chat_conv_tx
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_mcp_oauth_tables'
down_revision = 'add_chat_conv_tx'
branch_labels = None
depends_on = None


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def _column_exists(conn, table_name, column_name):
    if not _table_exists(conn, table_name):
        return False
    return column_name in {c['name'] for c in inspect(conn).get_columns(table_name)}


def upgrade():
    conn = op.get_bind()
    if not _column_exists(conn, 'organizations', 'mcp_admin_only'):
        with op.batch_alter_table('organizations') as batch:
            batch.add_column(sa.Column(
                'mcp_admin_only', sa.Boolean(), nullable=False,
                server_default=sa.false(),
            ))

    if not _table_exists(conn, 'mcp_oauth_clients'):
        op.create_table(
            'mcp_oauth_clients',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('client_name', sa.String(200), nullable=False),
            sa.Column('redirect_uris', sa.JSON(), nullable=False),
            sa.Column('token_endpoint_auth_method', sa.String(50), nullable=False),
            sa.Column('grant_types', sa.JSON(), nullable=False),
            sa.Column('response_types', sa.JSON(), nullable=False),
            sa.Column('dedupe_hash', sa.String(64), nullable=False),
            sa.Column('authorized_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_oauth_clients_client_id', 'mcp_oauth_clients', ['client_id'], unique=True)
        op.create_index('ix_mcp_oauth_clients_dedupe_hash', 'mcp_oauth_clients', ['dedupe_hash'])

    if not _table_exists(conn, 'mcp_user_grants'):
        op.create_table(
            'mcp_user_grants',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('scopes', sa.JSON(), nullable=False),
            sa.Column('resource', sa.String(500), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.Column('approved_at', sa.DateTime(), nullable=False),
            sa.Column('last_used_at', sa.DateTime(), nullable=True),
            sa.Column('selected_transaction_id', sa.Integer(), nullable=True),
            sa.Column('calls_on', sa.Date(), nullable=True),
            sa.Column('calls_today', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('read_calls_today', sa.Integer(), nullable=False, server_default='0'),
        )
        op.create_index('ix_mcp_user_grants_organization_id', 'mcp_user_grants', ['organization_id'])
        op.create_index('ix_mcp_user_grants_user_id', 'mcp_user_grants', ['user_id'])
        op.create_index('ix_mcp_user_grants_client_id', 'mcp_user_grants', ['client_id'])
        op.create_index('ix_mcp_user_grants_revoked_at', 'mcp_user_grants', ['revoked_at'])
        op.create_index('ix_mcp_user_grants_user_client', 'mcp_user_grants', ['user_id', 'client_id'])

    if not _table_exists(conn, 'mcp_authorization_codes'):
        op.create_table(
            'mcp_authorization_codes',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('code_hash', sa.String(64), nullable=False),
            sa.Column('grant_id', sa.Integer(), sa.ForeignKey('mcp_user_grants.id', ondelete='CASCADE'), nullable=False),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('redirect_uri', sa.String(500), nullable=False),
            sa.Column('scopes', sa.JSON(), nullable=False),
            sa.Column('resource', sa.String(500), nullable=False),
            sa.Column('code_challenge', sa.String(128), nullable=False),
            sa.Column('code_challenge_method', sa.String(16), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('consumed_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_authorization_codes_code_hash', 'mcp_authorization_codes', ['code_hash'], unique=True)
        op.create_index('ix_mcp_authorization_codes_grant_id', 'mcp_authorization_codes', ['grant_id'])

    if not _table_exists(conn, 'mcp_access_tokens'):
        op.create_table(
            'mcp_access_tokens',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('token_hash', sa.String(64), nullable=False),
            sa.Column('grant_id', sa.Integer(), sa.ForeignKey('mcp_user_grants.id', ondelete='CASCADE'), nullable=False),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('scopes', sa.JSON(), nullable=False),
            sa.Column('resource', sa.String(500), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_access_tokens_token_hash', 'mcp_access_tokens', ['token_hash'], unique=True)
        op.create_index('ix_mcp_access_tokens_grant_id', 'mcp_access_tokens', ['grant_id'])
        op.create_index('ix_mcp_access_tokens_user_id', 'mcp_access_tokens', ['user_id'])
        op.create_index('ix_mcp_access_tokens_organization_id', 'mcp_access_tokens', ['organization_id'])
        op.create_index('ix_mcp_access_tokens_expires_at', 'mcp_access_tokens', ['expires_at'])

    if not _table_exists(conn, 'mcp_refresh_tokens'):
        op.create_table(
            'mcp_refresh_tokens',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('token_hash', sa.String(64), nullable=False),
            sa.Column('grant_id', sa.Integer(), sa.ForeignKey('mcp_user_grants.id', ondelete='CASCADE'), nullable=False),
            sa.Column('client_id', sa.String(64), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('absolute_expires_at', sa.DateTime(), nullable=False),
            sa.Column('revoked_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_mcp_refresh_tokens_token_hash', 'mcp_refresh_tokens', ['token_hash'], unique=True)
        op.create_index('ix_mcp_refresh_tokens_grant_id', 'mcp_refresh_tokens', ['grant_id'])

    if conn.dialect.name == 'postgresql':
        # PostgREST lockout. Do not FORCE RLS: /oauth/token and DCR run without
        # a login, so app.current_org_id is not set.
        for table in (
            'mcp_oauth_clients',
            'mcp_user_grants',
            'mcp_authorization_codes',
            'mcp_access_tokens',
            'mcp_refresh_tokens',
        ):
            conn.execute(sa.text(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY'))
            conn.execute(sa.text(
                f'REVOKE ALL ON TABLE public.{table} FROM anon, authenticated'
            ))


def downgrade():
    conn = op.get_bind()
    for table in (
        'mcp_refresh_tokens',
        'mcp_access_tokens',
        'mcp_authorization_codes',
        'mcp_user_grants',
        'mcp_oauth_clients',
    ):
        if _table_exists(conn, table):
            op.drop_table(table)
    if _column_exists(conn, 'organizations', 'mcp_admin_only'):
        with op.batch_alter_table('organizations') as batch:
            batch.drop_column('mcp_admin_only')
