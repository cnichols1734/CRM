"""Link B.O.B. actions to the notification that announced them.

Lets an undo retract the bell entry instead of leaving the agent with a record
of a change that no longer exists.

Revision ID: add_bob_action_notif
Revises: add_bob_telegram
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_bob_action_notif'
down_revision = 'add_bob_telegram'
branch_labels = None
depends_on = None

TABLE = 'bob_actions'
COLUMN = 'notification_id'


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    if TABLE not in set(inspector.get_table_names()):
        return
    if COLUMN in {c['name'] for c in inspector.get_columns(TABLE)}:
        return

    op.add_column(TABLE, sa.Column(COLUMN, sa.Integer(), nullable=True))
    op.create_index(
        f'ix_{TABLE}_{COLUMN}', TABLE, [COLUMN], unique=False,
    )

    # SQLite cannot add a foreign key to an existing table, and local dev does
    # not need one for correctness here.
    if conn.dialect.name == 'postgresql':
        op.create_foreign_key(
            f'fk_{TABLE}_{COLUMN}', TABLE, 'notifications',
            [COLUMN], ['id'], ondelete='SET NULL',
        )


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    if TABLE not in set(inspector.get_table_names()):
        return
    if COLUMN not in {c['name'] for c in inspector.get_columns(TABLE)}:
        return

    if conn.dialect.name == 'postgresql':
        op.drop_constraint(f'fk_{TABLE}_{COLUMN}', TABLE, type_='foreignkey')
    op.drop_index(f'ix_{TABLE}_{COLUMN}', table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
