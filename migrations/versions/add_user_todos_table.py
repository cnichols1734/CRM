"""add user todos table

Revision ID: add_user_todos_table
Revises: 38f343baa6cd
Create Date: 2024-02-14 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_user_todos_table'
down_revision = '38f343baa6cd'
branch_labels = None
depends_on = None


def upgrade():
    # Create user_todos table
    op.create_table('user_todos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('text', sa.String(length=500), nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=False, default=False),
        sa.Column('order', sa.Integer(), nullable=False, default=0),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create an index on user_id for faster lookups
    op.create_index(op.f('ix_user_todos_user_id'), 'user_todos', ['user_id'], unique=False)


def downgrade():
    # Drop the index first
    op.drop_index(op.f('ix_user_todos_user_id'), table_name='user_todos')
    
    # Then drop the table
    op.drop_table('user_todos') 