"""Add broker fields to organizations table

Revision ID: add_org_broker_fields
Revises: add_gmail_signature_fields
Create Date: 2026-01-25

Adds broker/brokerage information fields to organizations for document generation:
- broker_name: Brokerage company name (e.g., "Origen Realty")
- broker_license_number: Broker license number
- broker_address: Full broker address string

These fields are used to pre-fill document fields like "Broker's Printed Name"
in seller transaction documents (e.g., Wire Fraud Warning).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_org_broker_fields'
down_revision = 'add_gmail_signature_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Add broker_name column
    op.execute("""
        ALTER TABLE organizations
        ADD COLUMN IF NOT EXISTS broker_name VARCHAR(200);
    """)
    
    # Add broker_license_number column
    op.execute("""
        ALTER TABLE organizations
        ADD COLUMN IF NOT EXISTS broker_license_number VARCHAR(50);
    """)
    
    # Add broker_address column
    op.execute("""
        ALTER TABLE organizations
        ADD COLUMN IF NOT EXISTS broker_address VARCHAR(500);
    """)


def downgrade():
    # Remove the broker columns
    op.execute("""
        ALTER TABLE organizations
        DROP COLUMN IF EXISTS broker_name;
    """)
    
    op.execute("""
        ALTER TABLE organizations
        DROP COLUMN IF EXISTS broker_license_number;
    """)
    
    op.execute("""
        ALTER TABLE organizations
        DROP COLUMN IF EXISTS broker_address;
    """)
