"""merge chat tables migrations

Revision ID: 9118d7bfdcb2
Revises: create_bob_chat_tables, create_chat_tables_fix
Create Date: 2025-01-05 19:48:46.267716

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9118d7bfdcb2'
down_revision = ('create_bob_chat_tables', 'create_chat_tables_fix')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
