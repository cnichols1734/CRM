"""Add multi-tenancy support with organizations, RLS, and tenant scoping

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-01-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision = 'add_multi_tenancy'
down_revision = 'add_adhoc_doc_cols'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    
    # =========================================================================
    # STEP 1: Create organizations table
    # =========================================================================
    if 'organizations' not in tables:
        op.create_table(
            'organizations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(200), nullable=False),
            sa.Column('slug', sa.String(100), unique=True, nullable=False),
            sa.Column('logo_url', sa.String(500), nullable=True),
            sa.Column('subscription_tier', sa.String(50), server_default='free'),
            sa.Column('max_users', sa.Integer(), server_default='1'),
            sa.Column('max_contacts', sa.Integer(), server_default='200'),
            sa.Column('can_invite_users', sa.Boolean(), server_default='false'),
            sa.Column('feature_flags', sa.JSON(), server_default='{}'),
            sa.Column('is_platform_admin', sa.Boolean(), server_default='false'),
            sa.Column('status', sa.String(30), server_default='active'),
            sa.Column('deletion_scheduled_at', sa.DateTime(), nullable=True),
            sa.Column('session_invalidated_at', sa.DateTime(), nullable=True),
            sa.Column('approved_at', sa.DateTime(), nullable=True),
            sa.Column('approved_by_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index('ix_organizations_slug', 'organizations', ['slug'], unique=True)
        op.create_index('ix_organizations_status', 'organizations', ['status'])
    
    # =========================================================================
    # STEP 2: Insert Origen Realty as platform admin
    # =========================================================================
    conn.execute(text("""
        INSERT INTO organizations 
            (name, slug, is_platform_admin, subscription_tier, status, max_users, can_invite_users, max_contacts)
        SELECT 
            'Origen Realty', 'origen-realty', true, 'enterprise', 'active', 1000, true, NULL
        WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE slug = 'origen-realty')
    """))
    
    # =========================================================================
    # STEP 3: Add columns to user table
    # =========================================================================
    if 'user' in tables:
        columns = [c['name'] for c in inspector.get_columns('user')]
        
        if 'organization_id' not in columns:
            op.add_column('user', sa.Column('organization_id', sa.Integer(), nullable=True))
        
        if 'org_role' not in columns:
            op.add_column('user', sa.Column('org_role', sa.String(20), server_default='agent'))
        
        if 'is_super_admin' not in columns:
            op.add_column('user', sa.Column('is_super_admin', sa.Boolean(), server_default='false'))
        
        # Backfill users to Origen (using subquery, not hardcoded ID)
        conn.execute(text("""
            UPDATE "user" SET 
                organization_id = (SELECT id FROM organizations WHERE slug = 'origen-realty'),
                org_role = CASE WHEN role = 'admin' THEN 'admin' ELSE 'agent' END,
                is_super_admin = (role = 'admin')
            WHERE organization_id IS NULL
        """))
        
        # Set first admin as owner
        conn.execute(text("""
            UPDATE "user" SET org_role = 'owner'
            WHERE id = (
                SELECT MIN(id) FROM "user" WHERE role = 'admin'
            )
            AND org_role != 'owner'
        """))
        
        # Make organization_id NOT NULL and add FK (RESTRICT, not CASCADE)
        op.alter_column('user', 'organization_id', nullable=False)
        
        # Check if FK already exists before creating
        fks = [fk['name'] for fk in inspector.get_foreign_keys('user')]
        if 'fk_user_org' not in fks:
            op.create_foreign_key('fk_user_org', 'user', 'organizations',
                                  ['organization_id'], ['id'], ondelete='RESTRICT')
        op.create_index('ix_user_organization_id', 'user', ['organization_id'])
    
    # =========================================================================
    # STEP 4: Add organization_id to tenant tables with backfill
    # =========================================================================
    tenant_tables = [
        'contact', 'contact_group', 'task', 'transactions',
        'action_plan', 'daily_todo_lists', 'user_todos',
        'company_updates', 'sendgrid_template'
    ]
    
    for table in tenant_tables:
        if table in tables:
            columns = [c['name'] for c in inspector.get_columns(table)]
            
            if 'organization_id' not in columns:
                # Add column as nullable first
                op.add_column(table, sa.Column('organization_id', sa.Integer(), nullable=True))
                
                # Backfill using subquery
                conn.execute(text(f"""
                    UPDATE "{table}" SET organization_id = 
                        (SELECT id FROM organizations WHERE slug = 'origen-realty')
                    WHERE organization_id IS NULL
                """))
                
                # Make NOT NULL
                op.alter_column(table, 'organization_id', nullable=False)
                
                # Add FK with RESTRICT
                fks = [fk['name'] for fk in inspector.get_foreign_keys(table)]
                fk_name = f'fk_{table}_org'
                if fk_name not in fks:
                    op.create_foreign_key(fk_name, table, 'organizations',
                                          ['organization_id'], ['id'], ondelete='RESTRICT')
                
                # Add index
                op.create_index(f'ix_{table}_org_id', table, ['organization_id'])
    
    # =========================================================================
    # STEP 5: Add created_by_id to contact
    # =========================================================================
    if 'contact' in tables:
        columns = [c['name'] for c in inspector.get_columns('contact')]
        if 'created_by_id' not in columns:
            op.add_column('contact', sa.Column('created_by_id', sa.Integer(), nullable=True))
            
            # Backfill with user_id
            conn.execute(text("""
                UPDATE contact SET created_by_id = user_id WHERE created_by_id IS NULL
            """))
            
            fks = [fk['name'] for fk in inspector.get_foreign_keys('contact')]
            if 'fk_contact_created_by' not in fks:
                op.create_foreign_key('fk_contact_created_by', 'contact', 'user',
                                      ['created_by_id'], ['id'], ondelete='SET NULL')
    
    # =========================================================================
    # STEP 6: Remove unique constraint on contact_group.name (now unique per org)
    # =========================================================================
    if 'contact_group' in tables:
        # Check for existing unique constraint
        try:
            op.drop_constraint('contact_group_name_key', 'contact_group', type_='unique')
        except:
            pass  # Constraint may not exist
        
        # Add org-scoped unique constraint
        try:
            op.create_unique_constraint('uq_contact_group_org_name', 'contact_group',
                                        ['organization_id', 'name'])
        except:
            pass  # May already exist
    
    # =========================================================================
    # STEP 7: Create organization_metrics table
    # =========================================================================
    if 'organization_metrics' not in tables:
        op.create_table(
            'organization_metrics',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('user_count', sa.Integer(), server_default='0'),
            sa.Column('contact_count', sa.Integer(), server_default='0'),
            sa.Column('task_count', sa.Integer(), server_default='0'),
            sa.Column('transaction_count', sa.Integer(), server_default='0'),
            sa.Column('last_user_login_at', sa.DateTime(), nullable=True),
            sa.Column('last_contact_created_at', sa.DateTime(), nullable=True),
            sa.Column('last_transaction_created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    name='fk_org_metrics_org', ondelete='CASCADE'),
            sa.UniqueConstraint('organization_id', name='uq_org_metrics_org_id'),
        )
    
    # =========================================================================
    # STEP 8: Create organization_invites table
    # =========================================================================
    if 'organization_invites' not in tables:
        op.create_table(
            'organization_invites',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(120), nullable=False),
            sa.Column('invited_by_id', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(20), server_default='agent'),
            sa.Column('token', sa.String(64), unique=True, nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    name='fk_org_invites_org', ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['invited_by_id'], ['user.id'],
                                    name='fk_org_invites_user'),
        )
        op.create_index('ix_org_invites_token', 'organization_invites', ['token'], unique=True)
        op.create_index('ix_org_invites_org_id', 'organization_invites', ['organization_id'])
    
    # =========================================================================
    # STEP 9: Create platform_audit_log table
    # =========================================================================
    if 'platform_audit_log' not in tables:
        op.create_table(
            'platform_audit_log',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('admin_user_id', sa.Integer(), nullable=False),
            sa.Column('target_org_id', sa.Integer(), nullable=True),
            sa.Column('action', sa.String(100), nullable=False),
            sa.Column('details', sa.JSON(), server_default='{}'),
            sa.Column('ip_address', sa.String(45), nullable=True),
            sa.Column('user_agent', sa.String(500), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['admin_user_id'], ['user.id'],
                                    name='fk_platform_audit_admin'),
            sa.ForeignKeyConstraint(['target_org_id'], ['organizations.id'],
                                    name='fk_platform_audit_org', ondelete='SET NULL'),
        )
        op.create_index('ix_platform_audit_log_created_at', 'platform_audit_log', ['created_at'])
        op.create_index('ix_platform_audit_log_target_org', 'platform_audit_log', ['target_org_id'])
    
    # =========================================================================
    # STEP 10: Enable Row Level Security on tenant tables
    # Note: RLS is PostgreSQL-specific. This will work on Supabase.
    # =========================================================================
    rls_tables = [
        'contact', 'contact_group', 'task', 'transactions',
        'action_plan', 'daily_todo_lists', 'user_todos',
        'company_updates', 'sendgrid_template'
    ]
    
    for table in rls_tables:
        if table in tables:
            # Enable RLS
            conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
            
            # Create tenant isolation policy
            policy_name = f'tenant_isolation_{table}'
            
            # Drop policy if exists (for idempotency)
            try:
                conn.execute(text(f'DROP POLICY IF EXISTS {policy_name} ON "{table}"'))
            except:
                pass
            
            # Create new policy
            conn.execute(text(f"""
                CREATE POLICY {policy_name} ON "{table}"
                FOR ALL
                USING (organization_id = current_setting('app.current_org_id', true)::int)
                WITH CHECK (organization_id = current_setting('app.current_org_id', true)::int)
            """))
    
    # =========================================================================
    # STEP 11: Initialize metrics for Origen
    # =========================================================================
    conn.execute(text("""
        INSERT INTO organization_metrics (organization_id, user_count, contact_count, task_count, transaction_count)
        SELECT 
            o.id,
            (SELECT COUNT(*) FROM "user" WHERE organization_id = o.id),
            (SELECT COUNT(*) FROM contact WHERE organization_id = o.id),
            (SELECT COUNT(*) FROM task WHERE organization_id = o.id),
            (SELECT COUNT(*) FROM transactions WHERE organization_id = o.id)
        FROM organizations o
        WHERE o.slug = 'origen-realty'
        AND NOT EXISTS (SELECT 1 FROM organization_metrics WHERE organization_id = o.id)
    """))


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    
    # Disable RLS and drop policies
    rls_tables = [
        'contact', 'contact_group', 'task', 'transactions',
        'action_plan', 'daily_todo_lists', 'user_todos',
        'company_updates', 'sendgrid_template'
    ]
    
    for table in rls_tables:
        if table in tables:
            try:
                conn.execute(text(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"'))
                conn.execute(text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
            except:
                pass
    
    # Drop platform_audit_log
    if 'platform_audit_log' in tables:
        op.drop_index('ix_platform_audit_log_target_org', table_name='platform_audit_log')
        op.drop_index('ix_platform_audit_log_created_at', table_name='platform_audit_log')
        op.drop_table('platform_audit_log')
    
    # Drop organization_invites
    if 'organization_invites' in tables:
        op.drop_index('ix_org_invites_org_id', table_name='organization_invites')
        op.drop_index('ix_org_invites_token', table_name='organization_invites')
        op.drop_table('organization_invites')
    
    # Drop organization_metrics
    if 'organization_metrics' in tables:
        op.drop_table('organization_metrics')
    
    # Remove organization_id from tenant tables
    tenant_tables = [
        'contact', 'contact_group', 'task', 'transactions',
        'action_plan', 'daily_todo_lists', 'user_todos',
        'company_updates', 'sendgrid_template'
    ]
    
    for table in tenant_tables:
        if table in tables:
            columns = [c['name'] for c in inspector.get_columns(table)]
            if 'organization_id' in columns:
                try:
                    op.drop_constraint(f'fk_{table}_org', table, type_='foreignkey')
                except:
                    pass
                try:
                    op.drop_index(f'ix_{table}_org_id', table_name=table)
                except:
                    pass
                op.drop_column(table, 'organization_id')
    
    # Remove created_by_id from contact
    if 'contact' in tables:
        columns = [c['name'] for c in inspector.get_columns('contact')]
        if 'created_by_id' in columns:
            try:
                op.drop_constraint('fk_contact_created_by', 'contact', type_='foreignkey')
            except:
                pass
            op.drop_column('contact', 'created_by_id')
    
    # Remove org columns from user
    if 'user' in tables:
        columns = [c['name'] for c in inspector.get_columns('user')]
        if 'organization_id' in columns:
            try:
                op.drop_constraint('fk_user_org', 'user', type_='foreignkey')
            except:
                pass
            try:
                op.drop_index('ix_user_organization_id', table_name='user')
            except:
                pass
            op.drop_column('user', 'organization_id')
        if 'org_role' in columns:
            op.drop_column('user', 'org_role')
        if 'is_super_admin' in columns:
            op.drop_column('user', 'is_super_admin')
    
    # Drop organizations table
    if 'organizations' in tables:
        op.drop_index('ix_organizations_status', table_name='organizations')
        op.drop_index('ix_organizations_slug', table_name='organizations')
        op.drop_table('organizations')
