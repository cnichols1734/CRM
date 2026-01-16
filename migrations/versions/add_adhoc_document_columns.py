"""Add columns for ad-hoc document signing

Revision ID: add_adhoc_doc_cols
Revises: add_signing_method
Create Date: 2026-01-16

Adds columns for external/hybrid document workflows:
- document_source: 'template', 'external', or 'hybrid'
- source_file_path: path to uploaded source PDF in Supabase
- field_placements: JSON array of manually placed signature fields
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_adhoc_doc_cols'
down_revision = 'add_signing_method'
branch_labels = None
depends_on = None


def upgrade():
    # Add document_source column with default 'template' for existing docs
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS document_source VARCHAR(20) DEFAULT 'template';
    """)
    
    # Add source_file_path for external/hybrid docs
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS source_file_path VARCHAR(500);
    """)
    
    # Add field_placements JSON column for manual field positions
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS field_placements JSONB;
    """)
    
    # Backfill: set existing docs to 'template' source
    op.execute("""
        UPDATE transaction_documents
        SET document_source = 'template'
        WHERE document_source IS NULL;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE transaction_documents
        DROP COLUMN IF EXISTS document_source;
    """)
    op.execute("""
        ALTER TABLE transaction_documents
        DROP COLUMN IF EXISTS source_file_path;
    """)
    op.execute("""
        ALTER TABLE transaction_documents
        DROP COLUMN IF EXISTS field_placements;
    """)
