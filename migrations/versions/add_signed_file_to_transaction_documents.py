"""Add signed file columns to transaction_documents

Revision ID: add_signed_file_cols
Revises: add_rentcast_fields
Create Date: 2026-01-16

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_signed_file_cols'
down_revision = 'add_rentcast_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Add columns for storing signed document files in Supabase
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS signed_file_path VARCHAR(500),
        ADD COLUMN IF NOT EXISTS signed_file_size INTEGER,
        ADD COLUMN IF NOT EXISTS signed_file_downloaded_at TIMESTAMP;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE transaction_documents
        DROP COLUMN IF EXISTS signed_file_path,
        DROP COLUMN IF EXISTS signed_file_size,
        DROP COLUMN IF EXISTS signed_file_downloaded_at;
    """)
