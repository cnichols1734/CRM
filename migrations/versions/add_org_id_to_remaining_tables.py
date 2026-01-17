"""Add organization_id to all remaining tenant tables

Revision ID: add_org_id_remaining
Revises: fix_rls_all_tables
Create Date: 2026-01-16

Adds organization_id to tables that were missed in the original multi-tenancy migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect

revision = 'add_org_id_remaining'
down_revision = 'fix_rls_all_tables'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Get Origen Realty org ID
    result = conn.execute(text("SELECT id FROM organizations WHERE slug = 'origen-realty' LIMIT 1"))
    row = result.fetchone()
    origen_org_id = row[0] if row else 1
    print(f"Using Origen org_id: {origen_org_id}")
    
    # Tables that need organization_id added
    # Format: (table_name, parent_table_for_backfill, parent_fk_column)
    tables_to_fix = [
        ('task_type', None, None),  # Standalone, backfill to origen
        ('task_subtype', 'task_type', 'task_type_id'),  # Get org from task_type
        ('contact_files', 'contact', 'contact_id'),  # Get org from contact
        ('daily_todo_list', 'user', 'user_id'),  # Get org from user
        ('company_update_comments', 'company_updates', 'update_id'),
        ('company_update_reactions', 'company_updates', 'update_id'),
        ('company_update_views', 'company_updates', 'update_id'),
        ('transaction_documents', 'transactions', 'transaction_id'),
        ('transaction_participants', 'transactions', 'transaction_id'),
        ('document_signatures', None, None),  # Complex, backfill to origen
        ('transaction_types', None, None),  # Global types, backfill to origen
        ('interaction', 'contact', 'contact_id'),
    ]
    
    for table_name, parent_table, parent_fk in tables_to_fix:
        try:
            # Check if table exists
            if table_name not in inspector.get_table_names():
                print(f"⚠ Table {table_name} doesn't exist, skipping")
                continue
            
            # Check if organization_id already exists
            columns = [c['name'] for c in inspector.get_columns(table_name)]
            if 'organization_id' in columns:
                print(f"✓ {table_name} already has organization_id")
                continue
            
            # Add organization_id column (nullable first)
            op.add_column(table_name, sa.Column('organization_id', sa.Integer(), nullable=True))
            print(f"  Added organization_id column to {table_name}")
            
            # Backfill data
            if parent_table and parent_fk:
                # Get org_id from parent table
                conn.execute(text(f'''
                    UPDATE "{table_name}" t
                    SET organization_id = p.organization_id
                    FROM "{parent_table}" p
                    WHERE t.{parent_fk} = p.id
                '''))
                print(f"  Backfilled {table_name} from {parent_table}")
            
            # Set remaining nulls to origen
            conn.execute(text(f'''
                UPDATE "{table_name}"
                SET organization_id = {origen_org_id}
                WHERE organization_id IS NULL
            '''))
            
            # Make NOT NULL
            op.alter_column(table_name, 'organization_id', nullable=False)
            
            # Add foreign key
            op.create_foreign_key(
                f'fk_{table_name}_org',
                table_name, 'organizations',
                ['organization_id'], ['id'],
                ondelete='RESTRICT'
            )
            
            # Add index
            op.create_index(f'ix_{table_name}_org_id', table_name, ['organization_id'])
            
            # Enable RLS
            conn.execute(text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
            conn.execute(text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
            
            # Create RLS policy
            policy_name = f'tenant_isolation_{table_name}'
            conn.execute(text(f'''
                CREATE POLICY {policy_name} ON "{table_name}"
                FOR ALL
                USING (organization_id = current_setting('app.current_org_id', true)::int)
                WITH CHECK (organization_id = current_setting('app.current_org_id', true)::int)
            '''))
            
            print(f"✓ Completed {table_name}")
            
        except Exception as e:
            print(f"⚠ Error on {table_name}: {e}")
            # Continue with other tables


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    
    tables = [
        'task_type', 'task_subtype', 'contact_files', 'daily_todo_list',
        'company_update_comments', 'company_update_reactions', 'company_update_views',
        'transaction_documents', 'transaction_participants', 'document_signatures',
        'transaction_types', 'interaction'
    ]
    
    for table_name in tables:
        try:
            if table_name not in inspector.get_table_names():
                continue
                
            columns = [c['name'] for c in inspector.get_columns(table_name)]
            if 'organization_id' not in columns:
                continue
            
            # Drop RLS
            policy_name = f'tenant_isolation_{table_name}'
            conn.execute(text(f'DROP POLICY IF EXISTS {policy_name} ON "{table_name}"'))
            conn.execute(text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
            
            # Drop index
            op.drop_index(f'ix_{table_name}_org_id', table_name=table_name)
            
            # Drop FK
            op.drop_constraint(f'fk_{table_name}_org', table_name, type_='foreignkey')
            
            # Drop column
            op.drop_column(table_name, 'organization_id')
            
            print(f"✓ Reverted {table_name}")
        except Exception as e:
            print(f"⚠ Error reverting {table_name}: {e}")
