"""Add dashboard onboarding flag to users

Revision ID: add_dashboard_onboarding_flag
Revises: add_onboarding_flags
Create Date: 2026-01-27

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_dashboard_onboarding_flag'
down_revision = 'add_onboarding_flags'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE "user"
        ADD COLUMN IF NOT EXISTS has_seen_dashboard_onboarding BOOLEAN DEFAULT FALSE;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE "user"
        DROP COLUMN IF EXISTS has_seen_dashboard_onboarding;
    """)
