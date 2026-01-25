"""Add Gmail signature fields and oauth_scope_version

Revision ID: add_gmail_signature_fields
Revises: fix_transaction_types_constraint
Create Date: 2026-01-25

Adds fields for CRM-based email signature (removing need for gmail.settings.basic scope)
and oauth_scope_version for tracking which scopes users have authorized.

- signature_html: HTML content of the user's email signature
- signature_images: JSON array of image metadata (filename, content_id, bytes as base64)
- oauth_scope_version: Integer to track scope version (1 = legacy with restricted scopes, 2 = new send-only)

Existing integrations are marked as version 1 with sync_status='needs_reauth' to prompt reconnection.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_gmail_signature_fields'
down_revision = 'fix_transaction_types_constraint'
branch_labels = None
depends_on = None


def upgrade():
    # Add signature_html column
    op.execute("""
        ALTER TABLE user_email_integrations
        ADD COLUMN IF NOT EXISTS signature_html TEXT;
    """)
    
    # Add signature_images column (JSON for image metadata with base64 bytes)
    op.execute("""
        ALTER TABLE user_email_integrations
        ADD COLUMN IF NOT EXISTS signature_images JSON;
    """)
    
    # Add oauth_scope_version column with default 2 for new connections
    op.execute("""
        ALTER TABLE user_email_integrations
        ADD COLUMN IF NOT EXISTS oauth_scope_version INTEGER DEFAULT 2;
    """)
    
    # Mark existing integrations as version 1 (legacy restricted scopes) and needs_reauth
    # This prompts users to reconnect with new scopes, but preserves their tokens for rollback safety
    op.execute("""
        UPDATE user_email_integrations
        SET oauth_scope_version = 1,
            sync_status = 'needs_reauth'
        WHERE oauth_scope_version IS NULL
           OR oauth_scope_version = 2;
    """)


def downgrade():
    # Remove the new columns
    op.execute("""
        ALTER TABLE user_email_integrations
        DROP COLUMN IF EXISTS signature_html;
    """)
    
    op.execute("""
        ALTER TABLE user_email_integrations
        DROP COLUMN IF EXISTS signature_images;
    """)
    
    op.execute("""
        ALTER TABLE user_email_integrations
        DROP COLUMN IF EXISTS oauth_scope_version;
    """)
