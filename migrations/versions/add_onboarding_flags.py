"""Add onboarding flags to users

Revision ID: add_onboarding_flags
Revises: add_org_broker_fields
Create Date: 2026-01-27

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_onboarding_flags'
down_revision = 'add_org_broker_fields'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE "user"
        ADD COLUMN IF NOT EXISTS has_seen_contacts_onboarding BOOLEAN DEFAULT FALSE;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE "user"
        DROP COLUMN IF EXISTS has_seen_contacts_onboarding;
    """)
