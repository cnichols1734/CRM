"""Add is_placeholder column to transaction_documents

Revision ID: add_placeholder_support
Revises: add_adhoc_doc_cols
Create Date: 2026-01-20

Adds is_placeholder field to mark documents that are created as reminders
for agents to upload content later (e.g., Special Tax District Notice,
Referral Agreement when agent-provided).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_placeholder_support'
down_revision = 'b2c3d4e5f7g8'
branch_labels = None
depends_on = None


def upgrade():
    # Add is_placeholder column with default False for existing docs
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS is_placeholder BOOLEAN DEFAULT FALSE;
    """)
    
    # Backfill: ensure all existing docs have is_placeholder = false
    op.execute("""
        UPDATE transaction_documents
        SET is_placeholder = FALSE
        WHERE is_placeholder IS NULL;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE transaction_documents
        DROP COLUMN IF EXISTS is_placeholder;
    """)
