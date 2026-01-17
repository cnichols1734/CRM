"""Enable RLS on agent_resources table

Revision ID: add_rls_agent_resources
Revises: add_org_id_remaining
Create Date: 2026-01-17

This enables Row Level Security on the agent_resources table
to ensure organization data isolation at the database level.
"""
from alembic import op
from sqlalchemy import text

revision = 'add_rls_agent_resources'
down_revision = 'add_org_id_remaining'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    
    table = 'agent_resources'
    policy_name = 'tenant_isolation_agent_resources'
    
    print(f'Enabling RLS on {table}...')
    
    # Enable RLS
    conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    
    # Force RLS for table owner too (important for superuser connections)
    conn.execute(text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    
    # Create policy matching other tenant tables
    conn.execute(text(f'''
        CREATE POLICY {policy_name} ON "{table}"
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id', true)::int)
        WITH CHECK (organization_id = current_setting('app.current_org_id', true)::int)
    '''))
    
    print(f'✓ Enabled RLS on {table} with policy {policy_name}')


def downgrade():
    conn = op.get_bind()
    
    table = 'agent_resources'
    policy_name = 'tenant_isolation_agent_resources'
    
    # Drop policy
    try:
        conn.execute(text(f'DROP POLICY IF EXISTS {policy_name} ON "{table}"'))
    except Exception as e:
        print(f'Warning dropping policy: {e}')
    
    # Disable RLS
    try:
        conn.execute(text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    except Exception as e:
        print(f'Warning disabling RLS: {e}')
    
    print(f'✓ Disabled RLS on {table}')
