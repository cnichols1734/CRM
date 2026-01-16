"""Add signing_method column to transaction_documents

Revision ID: add_signing_method
Revises: add_signed_file_cols
Create Date: 2026-01-16

Adds a signing_method field to distinguish between e-signed and
physically signed (wet signed) documents.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_signing_method'
down_revision = 'add_signed_file_cols'
branch_labels = None
depends_on = None


def upgrade():
    # Add signing_method column: 'esign', 'physical', or null
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS signing_method VARCHAR(20);
    """)
    
    # Optionally backfill: set existing signed docs to 'esign'
    # (since they came from DocuSeal webhooks)
    op.execute("""
        UPDATE transaction_documents
        SET signing_method = 'esign'
        WHERE status = 'signed' AND signing_method IS NULL;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE transaction_documents
        DROP COLUMN IF EXISTS signing_method;
    """)
