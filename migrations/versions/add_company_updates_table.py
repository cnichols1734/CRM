"""add company_updates table

Revision ID: add_company_updates_table
Revises: add_user_todos_table
Create Date: 2025-12-31 13:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'add_company_updates_table'
down_revision = 'add_user_todos_table'
branch_labels = None
depends_on = None


def upgrade():
    # Check if table already exists to make migration idempotent
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    
    if 'company_updates' not in tables:
        op.create_table(
            'company_updates',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('excerpt', sa.String(length=500), nullable=True),
            sa.Column('cover_image_url', sa.String(length=500), nullable=True),
            sa.Column('author_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['author_id'], ['user.id'], name='fk_company_updates_author_id', ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id', name='pk_company_updates')
        )
        
        # Create index for reverse chronological listing
        op.create_index('ix_company_updates_created_at', 'company_updates', ['created_at'], unique=False)


def downgrade():
    # Check if table exists before trying to drop
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    
    if 'company_updates' in tables:
        op.drop_index('ix_company_updates_created_at', table_name='company_updates')
        op.drop_table('company_updates')

