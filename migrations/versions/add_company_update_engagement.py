"""Add company update engagement tables (reactions, comments, views)

Revision ID: add_company_update_engagement
Revises: add_company_updates_table
Create Date: 2025-12-31

PRODUCTION SAFE: This migration only creates new tables.
It does NOT modify any existing tables or data.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_company_update_engagement'
down_revision = 'add_company_updates_table'
branch_labels = None
depends_on = None


def upgrade():
    # Create reactions table
    op.create_table(
        'company_update_reactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('update_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('reaction_type', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['update_id'], ['company_updates.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('update_id', 'user_id', 'reaction_type', name='unique_user_reaction')
    )
    op.create_index('ix_company_update_reactions_update_id', 'company_update_reactions', ['update_id'], unique=False)
    
    # Create comments table
    op.create_table(
        'company_update_comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('update_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['update_id'], ['company_updates.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_company_update_comments_update_id', 'company_update_comments', ['update_id'], unique=False)
    
    # Create views table
    op.create_table(
        'company_update_views',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('update_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('viewed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['update_id'], ['company_updates.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('update_id', 'user_id', name='unique_user_view')
    )
    op.create_index('ix_company_update_views_update_id', 'company_update_views', ['update_id'], unique=False)


def downgrade():
    op.drop_index('ix_company_update_views_update_id', table_name='company_update_views')
    op.drop_table('company_update_views')
    op.drop_index('ix_company_update_comments_update_id', table_name='company_update_comments')
    op.drop_table('company_update_comments')
    op.drop_index('ix_company_update_reactions_update_id', table_name='company_update_reactions')
    op.drop_table('company_update_reactions')

