"""Add Google Calendar sync columns

Revision ID: add_google_calendar_sync
Revises: add_task_reminder_fields
Create Date: 2026-01-24

Adds columns for Google Calendar integration:
- Task.google_calendar_event_id: Stores the Google Calendar event ID for synced tasks
- Task.calendar_sync_error: Stores any sync error message
- UserEmailIntegration.calendar_sync_enabled: Toggle for calendar sync per user
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_google_calendar_sync'
down_revision = '4fc5aacd858e'  # Current head (merge of add_contact_voice_memos + add_placeholder_support)
branch_labels = None
depends_on = None


def upgrade():
    # Add calendar sync columns to task table
    op.execute("""
        ALTER TABLE task
        ADD COLUMN IF NOT EXISTS google_calendar_event_id VARCHAR(255);
    """)
    
    op.execute("""
        ALTER TABLE task
        ADD COLUMN IF NOT EXISTS calendar_sync_error TEXT;
    """)
    
    # Add calendar sync enabled flag to user_email_integrations table
    op.execute("""
        ALTER TABLE user_email_integrations
        ADD COLUMN IF NOT EXISTS calendar_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE user_email_integrations
        DROP COLUMN IF EXISTS calendar_sync_enabled;
    """)
    
    op.execute("""
        ALTER TABLE task
        DROP COLUMN IF EXISTS calendar_sync_error;
    """)
    
    op.execute("""
        ALTER TABLE task
        DROP COLUMN IF EXISTS google_calendar_event_id;
    """)
