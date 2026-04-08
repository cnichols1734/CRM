"""Add extraction_status and extraction_error to transaction_documents

Revision ID: add_extraction_status
Revises: add_signed_original_filename
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa


revision = 'add_extraction_status'
down_revision = 'add_signed_original_filename'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS extraction_status VARCHAR(20)
    """)
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS extraction_error TEXT
    """)


def downgrade():
    op.execute("ALTER TABLE transaction_documents DROP COLUMN IF EXISTS extraction_status")
    op.execute("ALTER TABLE transaction_documents DROP COLUMN IF EXISTS extraction_error")
