"""Enable RLS on all tenant-scoped tables

Revision ID: fix_rls_all_tables
Revises: fix_pwd_hash_len
Create Date: 2026-01-16

This enables RLS on the user table which was missed in the original migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect

revision = 'fix_rls_all_tables'
down_revision = 'fix_pwd_hash_len'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Only enable RLS on tables that have organization_id column
    # Check each table first
    tables_to_check = [
        'user',
        'task_type',
        'task_subtype', 
        'contact_files',
        'daily_todo_list',
    ]
    
    for table in tables_to_check:
        try:
            columns = [c['name'] for c in inspector.get_columns(table)]
            if 'organization_id' not in columns:
                print(f'⚠ Skipping {table} - no organization_id column')
                continue
                
            # Enable RLS
            conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
            
            # Force RLS for table owner too
            conn.execute(text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
            
            # Create policy
            policy_name = f'tenant_isolation_{table}'
            conn.execute(text(f'''
                CREATE POLICY {policy_name} ON "{table}"
                FOR ALL
                USING (organization_id = current_setting('app.current_org_id', true)::int)
                WITH CHECK (organization_id = current_setting('app.current_org_id', true)::int)
            '''))
            
            print(f'✓ Enabled RLS on {table}')
        except Exception as e:
            print(f'⚠ Error on {table}: {e}')
            # Rollback the partial transaction
            conn.execute(text('ROLLBACK'))
            conn.execute(text('BEGIN'))
    
    # Force RLS on existing tables
    existing_rls_tables = [
        'contact', 'contact_group', 'task', 'transactions',
        'action_plan', 'user_todos', 'company_updates', 'sendgrid_template'
    ]
    
    for table in existing_rls_tables:
        try:
            conn.execute(text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        except Exception as e:
            print(f'⚠ Error forcing RLS on {table}: {e}')


def downgrade():
    conn = op.get_bind()
    
    tables = ['user', 'task_type', 'task_subtype', 'contact_files', 'daily_todo_list']
    
    for table in tables:
        try:
            policy_name = f'tenant_isolation_{table}'
            conn.execute(text(f'DROP POLICY IF EXISTS {policy_name} ON "{table}"'))
            conn.execute(text(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY'))
            conn.execute(text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
        except Exception:
            pass
