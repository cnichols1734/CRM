"""add task window preference to user

Revision ID: b2c3d4e5f7g8
Revises: add_task_reminder_fields
Create Date: 2026-01-19

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f7g8'
down_revision = 'add_task_reminder_fields'
branch_labels = None
depends_on = None


def upgrade():
    # Add task_window_days column with default 30
    op.execute("""
        ALTER TABLE "user"
        ADD COLUMN IF NOT EXISTS task_window_days INTEGER NOT NULL DEFAULT 30;
    """)


def downgrade():
    # Remove the column
    op.execute("""
        ALTER TABLE "user"
        DROP COLUMN IF EXISTS task_window_days;
    """)
