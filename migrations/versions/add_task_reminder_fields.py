"""Add task reminder tracking fields

Revision ID: add_task_reminder_fields
Revises: add_signing_method
Create Date: 2026-01-19

Adds boolean flags to track which reminder types have been sent for each task:
- two_day_reminder_sent: Reminder sent 48-72 hours before due date
- one_day_reminder_sent: Reminder sent 24-48 hours before due date  
- overdue_reminder_sent: Reminder sent after task became overdue
- last_reminder_sent_at: Timestamp of most recent reminder (for debugging)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_task_reminder_fields'
down_revision = 'add_rls_agent_resources'
branch_labels = None
depends_on = None


def upgrade():
    # Add reminder tracking columns to task table
    op.execute("""
        ALTER TABLE task
        ADD COLUMN IF NOT EXISTS two_day_reminder_sent BOOLEAN NOT NULL DEFAULT FALSE;
    """)
    
    op.execute("""
        ALTER TABLE task
        ADD COLUMN IF NOT EXISTS one_day_reminder_sent BOOLEAN NOT NULL DEFAULT FALSE;
    """)
    
    op.execute("""
        ALTER TABLE task
        ADD COLUMN IF NOT EXISTS today_reminder_sent BOOLEAN NOT NULL DEFAULT FALSE;
    """)
    
    op.execute("""
        ALTER TABLE task
        ADD COLUMN IF NOT EXISTS overdue_reminder_sent BOOLEAN NOT NULL DEFAULT FALSE;
    """)
    
    op.execute("""
        ALTER TABLE task
        ADD COLUMN IF NOT EXISTS last_reminder_sent_at TIMESTAMP;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE task
        DROP COLUMN IF EXISTS last_reminder_sent_at;
    """)
    
    op.execute("""
        ALTER TABLE task
        DROP COLUMN IF EXISTS overdue_reminder_sent;
    """)
    
    op.execute("""
        ALTER TABLE task
        DROP COLUMN IF EXISTS today_reminder_sent;
    """)
    
    op.execute("""
        ALTER TABLE task
        DROP COLUMN IF EXISTS one_day_reminder_sent;
    """)
    
    op.execute("""
        ALTER TABLE task
        DROP COLUMN IF EXISTS two_day_reminder_sent;
    """)
