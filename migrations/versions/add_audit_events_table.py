"""add audit_events table and sent_by_id to transaction_documents

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-01-13

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6g7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    # Add sent_by_id to transaction_documents if it doesn't exist
    if 'transaction_documents' in tables:
        columns = [c['name'] for c in inspector.get_columns('transaction_documents')]
        if 'sent_by_id' not in columns:
            op.add_column('transaction_documents',
                sa.Column('sent_by_id', sa.Integer(), nullable=True)
            )
            op.create_foreign_key(
                'fk_transaction_documents_sent_by_id',
                'transaction_documents', 'user',
                ['sent_by_id'], ['id'],
                ondelete='SET NULL'
            )

    # Create audit_events table if it doesn't exist
    if 'audit_events' not in tables:
        op.create_table(
            'audit_events',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('transaction_id', sa.Integer(), nullable=True),
            sa.Column('document_id', sa.Integer(), nullable=True),
            sa.Column('signature_id', sa.Integer(), nullable=True),
            sa.Column('actor_id', sa.Integer(), nullable=True),
            sa.Column('event_type', sa.String(length=50), nullable=False),
            sa.Column('description', sa.String(length=500), nullable=True),
            sa.Column('event_data', sa.JSON(), nullable=True),
            sa.Column('source', sa.String(length=50), nullable=True, server_default='app'),
            sa.Column('ip_address', sa.String(length=45), nullable=True),
            sa.Column('user_agent', sa.String(length=500), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], name='fk_audit_events_transaction_id', ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['document_id'], ['transaction_documents.id'], name='fk_audit_events_document_id', ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['signature_id'], ['document_signatures.id'], name='fk_audit_events_signature_id', ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['actor_id'], ['user.id'], name='fk_audit_events_actor_id', ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id', name='pk_audit_events')
        )

        # Create indexes for common queries
        op.create_index('ix_audit_events_transaction_id', 'audit_events', ['transaction_id'], unique=False)
        op.create_index('ix_audit_events_document_id', 'audit_events', ['document_id'], unique=False)
        op.create_index('ix_audit_events_actor_id', 'audit_events', ['actor_id'], unique=False)
        op.create_index('ix_audit_events_created_at', 'audit_events', ['created_at'], unique=False)
        op.create_index('ix_audit_events_event_type', 'audit_events', ['event_type'], unique=False)


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    # Drop audit_events table
    if 'audit_events' in tables:
        op.drop_index('ix_audit_events_event_type', table_name='audit_events')
        op.drop_index('ix_audit_events_created_at', table_name='audit_events')
        op.drop_index('ix_audit_events_actor_id', table_name='audit_events')
        op.drop_index('ix_audit_events_document_id', table_name='audit_events')
        op.drop_index('ix_audit_events_transaction_id', table_name='audit_events')
        op.drop_table('audit_events')

    # Remove sent_by_id from transaction_documents
    if 'transaction_documents' in tables:
        columns = [c['name'] for c in inspector.get_columns('transaction_documents')]
        if 'sent_by_id' in columns:
            op.drop_constraint('fk_transaction_documents_sent_by_id', 'transaction_documents', type_='foreignkey')
            op.drop_column('transaction_documents', 'sent_by_id')
