"""Fix password_hash column length to accommodate scrypt hashes

Revision ID: fix_pwd_hash_len
Revises: add_multi_tenancy
Create Date: 2026-01-16

"""
from alembic import op
import sqlalchemy as sa

revision = 'fix_pwd_hash_len'
down_revision = 'add_multi_tenancy'
branch_labels = None
depends_on = None


def upgrade():
    # Scrypt hashes can be 160+ characters, increase from 128 to 256
    op.alter_column('user', 'password_hash',
                    existing_type=sa.String(128),
                    type_=sa.String(256),
                    existing_nullable=True)


def downgrade():
    op.alter_column('user', 'password_hash',
                    existing_type=sa.String(256),
                    type_=sa.String(128),
                    existing_nullable=True)
