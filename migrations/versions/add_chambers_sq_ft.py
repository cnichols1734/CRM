"""Add sq_ft column to chambers_properties for scraped building square footage.

Revision ID: add_chambers_sq_ft
Revises: add_tax_protest_tables
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_chambers_sq_ft'
down_revision = 'add_tax_protest_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chambers_properties',
                  sa.Column('sq_ft', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('chambers_properties', 'sq_ft')
