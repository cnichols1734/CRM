"""add licensed supervisor fields to user

Revision ID: a1b2c3d4e5f6
Revises: 57f483948875
Create Date: 2026-01-08

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '57f483948875'
branch_labels = None
depends_on = None


def upgrade():
    # Add new nullable columns for licensed supervisor details
    op.add_column('user', sa.Column('licensed_supervisor_license', sa.String(16), nullable=True))
    op.add_column('user', sa.Column('licensed_supervisor_email', sa.String(120), nullable=True))
    op.add_column('user', sa.Column('licensed_supervisor_phone', sa.String(20), nullable=True))


def downgrade():
    # Remove the columns
    op.drop_column('user', 'licensed_supervisor_phone')
    op.drop_column('user', 'licensed_supervisor_email')
    op.drop_column('user', 'licensed_supervisor_license')

