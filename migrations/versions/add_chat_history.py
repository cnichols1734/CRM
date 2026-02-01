"""Add chat_conversations and chat_messages tables for BOB chat history

Revision ID: add_chat_history
Revises: 4fc5aacd858e
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_chat_history'
down_revision = 'add_dashboard_onboarding_flag'
branch_labels = None
depends_on = None


def upgrade():
    # Create chat_conversations table (using raw SQL for IF NOT EXISTS)
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            title VARCHAR(100),
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_conversations_user_id ON chat_conversations(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_conversations_organization_id ON chat_conversations(organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_conversations_updated_at ON chat_conversations(updated_at)")
    
    # Create chat_messages table
    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            image_data TEXT,
            mentioned_contact_ids JSONB,
            created_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_id ON chat_messages(conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_messages_created_at ON chat_messages(created_at)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_created_at")
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_conversation_id")
    op.execute("DROP TABLE IF EXISTS chat_messages")
    
    op.execute("DROP INDEX IF EXISTS ix_chat_conversations_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_chat_conversations_organization_id")
    op.execute("DROP INDEX IF EXISTS ix_chat_conversations_user_id")
    op.execute("DROP TABLE IF EXISTS chat_conversations")
