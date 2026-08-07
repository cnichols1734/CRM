"""Bind chat conversations to transactions for sticky deal briefings.

Revision ID: add_chat_conv_tx
Revises: add_req_manual_due
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa


revision = 'add_chat_conv_tx'
down_revision = 'add_req_manual_due'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c['name'] for c in inspector.get_columns('chat_conversations')}
    if 'transaction_id' not in cols:
        with op.batch_alter_table('chat_conversations') as batch:
            batch.add_column(
                sa.Column('transaction_id', sa.Integer(), nullable=True)
            )
        op.create_index(
            'ix_chat_conversations_transaction_id',
            'chat_conversations',
            ['transaction_id'],
            unique=False,
        )
        # FK best-effort: SQLite batch mode may already have limited FK support.
        try:
            with op.batch_alter_table('chat_conversations') as batch:
                batch.create_foreign_key(
                    'fk_chat_conversations_transaction_id',
                    'transactions',
                    ['transaction_id'],
                    ['id'],
                    ondelete='SET NULL',
                )
        except Exception:
            pass
    cols = {c['name'] for c in inspector.get_columns('chat_conversations')}
    if 'setup_briefing_sent_at' not in cols:
        with op.batch_alter_table('chat_conversations') as batch:
            batch.add_column(
                sa.Column('setup_briefing_sent_at', sa.DateTime(), nullable=True)
            )


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = {c['name'] for c in inspector.get_columns('chat_conversations')}
    with op.batch_alter_table('chat_conversations') as batch:
        if 'setup_briefing_sent_at' in cols:
            batch.drop_column('setup_briefing_sent_at')
        if 'transaction_id' in cols:
            try:
                batch.drop_constraint(
                    'fk_chat_conversations_transaction_id',
                    type_='foreignkey',
                )
            except Exception:
                pass
            batch.drop_column('transaction_id')
    try:
        op.drop_index(
            'ix_chat_conversations_transaction_id',
            table_name='chat_conversations',
        )
    except Exception:
        pass
