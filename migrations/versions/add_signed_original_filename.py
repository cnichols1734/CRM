"""Add signed_original_filename to transaction_documents

Revision ID: add_signed_original_filename
Revises: add_chat_file_attachments
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa


revision = 'add_signed_original_filename'
down_revision = 'add_chat_file_attachments'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE transaction_documents
        ADD COLUMN IF NOT EXISTS signed_original_filename VARCHAR(255)
    """)


def downgrade():
    op.execute("""
        ALTER TABLE transaction_documents
        DROP COLUMN IF EXISTS signed_original_filename
    """)
