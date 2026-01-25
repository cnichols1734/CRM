"""Fix transaction_types unique constraint to be org-scoped

Revision ID: fix_transaction_types_constraint
Revises: (run manage_db.py status to get current head)
Create Date: 2026-01-25

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fix_transaction_types_constraint'
down_revision = 'add_google_calendar_sync'  # Current head
branch_labels = None
depends_on = None


def upgrade():
    """
    Remove the global unique constraint on transaction_types.name
    and add a composite unique constraint on (organization_id, name)
    """
    op.execute("""
        -- Drop the old unique constraint on name (if it exists)
        ALTER TABLE transaction_types 
        DROP CONSTRAINT IF EXISTS transaction_types_name_key;
        
        -- Add composite unique constraint on (organization_id, name)
        -- This allows same name across different orgs but prevents duplicates within an org
        ALTER TABLE transaction_types 
        DROP CONSTRAINT IF EXISTS uq_transaction_types_org_name;
        
        ALTER TABLE transaction_types 
        ADD CONSTRAINT uq_transaction_types_org_name 
        UNIQUE (organization_id, name);
    """)


def downgrade():
    """
    Restore the global unique constraint (not recommended)
    """
    op.execute("""
        -- Remove composite constraint
        ALTER TABLE transaction_types 
        DROP CONSTRAINT IF EXISTS uq_transaction_types_org_name;
        
        -- This downgrade may fail if there are duplicate names across orgs
        -- In that case, manual cleanup is required
        ALTER TABLE transaction_types 
        ADD CONSTRAINT transaction_types_name_key UNIQUE (name);
    """)
