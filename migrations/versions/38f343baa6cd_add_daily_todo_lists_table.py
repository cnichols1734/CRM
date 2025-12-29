"""add daily todo lists table

Revision ID: 38f343baa6cd
Revises: 
Create Date: 2025-01-04 11:23:28.527527

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '38f343baa6cd'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create daily_todo_list table
    op.create_table(
        'daily_todo_list',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('generated_at', sa.DateTime(), nullable=False),
        sa.Column('todo_content', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='fk_daily_todo_list_user_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name='pk_daily_todo_list')
    )

    # Create indexes
    op.create_index('ix_daily_todo_list_user_id', 'daily_todo_list', ['user_id'], unique=False)
    op.create_index('ix_daily_todo_list_generated_at', 'daily_todo_list', ['generated_at'], unique=False)


def downgrade():
    # Drop indexes first
    op.drop_index('ix_daily_todo_list_generated_at', table_name='daily_todo_list')
    op.drop_index('ix_daily_todo_list_user_id', table_name='daily_todo_list')
    
    # Drop the table
    op.drop_table('daily_todo_list')
