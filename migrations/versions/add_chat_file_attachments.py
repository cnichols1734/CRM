"""Add file attachment columns to chat_messages table

Revision ID: add_chat_file_attachments
Revises: add_chat_history
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_chat_file_attachments'
down_revision = 'add_chat_history'
branch_labels = None
depends_on = None


def upgrade():
    # Add file attachment columns to chat_messages table
    op.execute("""
        ALTER TABLE chat_messages 
        ADD COLUMN IF NOT EXISTS file_url VARCHAR(500);
    """)
    op.execute("""
        ALTER TABLE chat_messages 
        ADD COLUMN IF NOT EXISTS file_name VARCHAR(255);
    """)
    op.execute("""
        ALTER TABLE chat_messages 
        ADD COLUMN IF NOT EXISTS file_type VARCHAR(100);
    """)
    op.execute("""
        ALTER TABLE chat_messages 
        ADD COLUMN IF NOT EXISTS file_size INTEGER;
    """)
    op.execute("""
        ALTER TABLE chat_messages 
        ADD COLUMN IF NOT EXISTS file_storage_path VARCHAR(500);
    """)


def downgrade():
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS file_storage_path;")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS file_size;")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS file_type;")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS file_name;")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS file_url;")
