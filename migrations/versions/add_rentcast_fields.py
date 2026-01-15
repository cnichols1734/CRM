"""Add RentCast property intelligence fields to transactions

Revision ID: add_rentcast_fields
Revises: add_contact_files
Create Date: 2026-01-14

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'add_rentcast_fields'
down_revision = 'add_contact_files'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if transactions table exists
    tables = inspector.get_table_names()
    if 'transactions' not in tables:
        return
    
    # Get existing columns
    columns = [c['name'] for c in inspector.get_columns('transactions')]
    
    # Add rentcast_data column if it doesn't exist
    if 'rentcast_data' not in columns:
        op.add_column('transactions',
            sa.Column('rentcast_data', sa.JSON(), nullable=True)
        )
    
    # Add rentcast_fetched_at column if it doesn't exist
    if 'rentcast_fetched_at' not in columns:
        op.add_column('transactions',
            sa.Column('rentcast_fetched_at', sa.DateTime(), nullable=True)
        )


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    
    if 'transactions' in tables:
        columns = [c['name'] for c in inspector.get_columns('transactions')]
        
        if 'rentcast_fetched_at' in columns:
            op.drop_column('transactions', 'rentcast_fetched_at')
        
        if 'rentcast_data' in columns:
            op.drop_column('transactions', 'rentcast_data')
