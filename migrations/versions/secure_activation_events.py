"""Secure the internal activation event stream.

Revision ID: secure_activation_events
Revises: add_daily_briefing_fields
Create Date: 2026-07-23
"""
from alembic import op


revision = 'secure_activation_events'
down_revision = 'add_daily_briefing_fields'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('ALTER TABLE public.activation_events ENABLE ROW LEVEL SECURITY')
    op.execute(
        'REVOKE ALL ON TABLE public.activation_events FROM anon, authenticated'
    )


def downgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE '
        'public.activation_events TO anon, authenticated'
    )
    op.execute('ALTER TABLE public.activation_events DISABLE ROW LEVEL SECURITY')

