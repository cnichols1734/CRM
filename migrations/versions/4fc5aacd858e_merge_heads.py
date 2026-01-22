"""Merge heads

Revision ID: 4fc5aacd858e
Revises: add_contact_voice_memos, add_placeholder_support
Create Date: 2026-01-21 19:39:48.411516

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4fc5aacd858e'
down_revision = ('add_contact_voice_memos', 'add_placeholder_support')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
