"""Add org_requirement_templates for learned VTC packs (Phase 3)

Revision ID: add_org_req_templates
Revises: add_bob_vtc_foundation
Create Date: 2026-08-04

Lightweight org-scoped versioned requirement templates.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = 'add_org_req_templates'
down_revision = 'add_bob_vtc_foundation'
branch_labels = None
depends_on = None

TABLE = 'org_requirement_templates'


def _table_exists(conn, table_name):
    return table_name in inspect(conn).get_table_names()


def upgrade():
    conn = op.get_bind()
    is_postgres = conn.dialect.name == 'postgresql'

    if not _table_exists(conn, TABLE):
        op.create_table(
            TABLE,
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column(
                'organization_id',
                sa.Integer(),
                sa.ForeignKey('organizations.id', ondelete='RESTRICT'),
                nullable=False,
            ),
            sa.Column('pack_key', sa.String(100), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('name', sa.String(200), nullable=False),
            sa.Column('status', sa.String(50), nullable=False, server_default='draft'),
            sa.Column('template_json', sa.JSON(), nullable=False),
            sa.Column(
                'created_by_id',
                sa.Integer(),
                sa.ForeignKey('user.id', ondelete='SET NULL'),
                nullable=True,
            ),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                'organization_id', 'pack_key', 'version',
                name='uq_org_requirement_templates_org_pack_version',
            ),
        )
        op.create_index(
            'ix_org_requirement_templates_organization_id',
            TABLE, ['organization_id'],
        )
        op.create_index(
            'ix_org_requirement_templates_pack_key',
            TABLE, ['pack_key'],
        )
        op.create_index(
            'ix_org_requirement_templates_status',
            TABLE, ['status'],
        )

    if is_postgres and _table_exists(conn, TABLE):
        op.execute(text(f'ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY'))
        op.execute(text(f'ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY'))
        op.execute(text(f'DROP POLICY IF EXISTS tenant_isolation_{TABLE} ON {TABLE}'))
        op.execute(text(f"""
            CREATE POLICY tenant_isolation_{TABLE} ON {TABLE}
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
        """))


def downgrade():
    conn = op.get_bind()
    is_postgres = conn.dialect.name == 'postgresql'

    if is_postgres and _table_exists(conn, TABLE):
        op.execute(text(f'DROP POLICY IF EXISTS tenant_isolation_{TABLE} ON {TABLE}'))
        op.execute(text(f'ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY'))

    if _table_exists(conn, TABLE):
        op.drop_table(TABLE)
