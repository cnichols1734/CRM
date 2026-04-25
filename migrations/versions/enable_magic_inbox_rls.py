"""Enable RLS on Magic Inbox and notification tables

Revision ID: enable_magic_inbox_rls
Revises: add_inbox_and_inbound_messages
Create Date: 2026-04-25 15:50:00.000000

"""
from alembic import op


revision = 'enable_magic_inbox_rls'
down_revision = 'add_inbox_and_inbound_messages'
branch_labels = None
depends_on = None


TENANT_TABLES = (
    'notifications',
    'user_notification_preferences',
    'inbound_messages',
)


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name != 'postgresql':
        return

    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}'
        )
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
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


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name != 'postgresql':
        return

    for table in TENANT_TABLES:
        op.execute(
            f'DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}'
        )
        op.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY')
