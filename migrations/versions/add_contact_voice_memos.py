"""Add contact_voice_memos table for voice memo feature

Revision ID: add_contact_voice_memos
Revises: 
Create Date: 2026-01-21
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_contact_voice_memos'
down_revision = None  # Will be auto-filled by alembic
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('contact_voice_memos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('storage_path', sa.String(500), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('transcription', sa.Text(), nullable=True),
        sa.Column('transcription_status', sa.String(20), nullable=True, default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['contact_id'], ['contact.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_contact_voice_memos_organization_id', 'contact_voice_memos', ['organization_id'])
    op.create_index('ix_contact_voice_memos_contact_id', 'contact_voice_memos', ['contact_id'])


def downgrade():
    op.drop_index('ix_contact_voice_memos_contact_id', 'contact_voice_memos')
    op.drop_index('ix_contact_voice_memos_organization_id', 'contact_voice_memos')
    op.drop_table('contact_voice_memos')
