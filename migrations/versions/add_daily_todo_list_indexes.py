"""add daily todo list indexes

Revision ID: add_daily_todo_list_indexes
Revises: 38f343baa6cd
Create Date: 2024-02-20 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_daily_todo_list_indexes'
down_revision = '38f343baa6cd'
branch_labels = None
depends_on = None

def upgrade():
    # Create indexes
    op.create_index('ix_daily_todo_list_user_id', 'daily_todo_list', ['user_id'], unique=False)
    op.create_index('ix_daily_todo_list_generated_at', 'daily_todo_list', ['generated_at'], unique=False)

def downgrade():
    # Drop indexes
    op.drop_index('ix_daily_todo_list_generated_at', table_name='daily_todo_list')
    op.drop_index('ix_daily_todo_list_user_id', table_name='daily_todo_list') 