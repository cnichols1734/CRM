"""Add contact_files table

Revision ID: add_contact_files
Revises: b2c3d4e5f6g7
Create Date: 2026-01-13

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_contact_files'
down_revision = 'b2c3d4e5f6g7'
branch_labels = None
depends_on = None


def upgrade():
    # Create contact_files table if it doesn't exist
    op.execute("""
        CREATE TABLE IF NOT EXISTS contact_files (
            id SERIAL PRIMARY KEY,
            contact_id INTEGER NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
            filename VARCHAR(255) NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            file_type VARCHAR(100),
            file_size INTEGER,
            storage_path VARCHAR(500) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # Create index on contact_id for faster lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_contact_files_contact_id 
        ON contact_files(contact_id);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS contact_files;")
