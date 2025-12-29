"""merge chat tables and daily todo indexes

Revision ID: dfa31e2d5cb1
Revises: add_chat_tables, add_daily_todo_list_indexes
Create Date: 2025-01-05 19:40:59.520325

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dfa31e2d5cb1'
down_revision = ('add_chat_tables', 'add_daily_todo_list_indexes')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
