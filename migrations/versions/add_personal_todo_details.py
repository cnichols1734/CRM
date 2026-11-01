"""Add private descriptions, sub-to-dos and comments to personal to-dos."""
from alembic import op
import sqlalchemy as sa

revision = 'add_personal_todo_details'
down_revision = 'add_account_deletion_requests'
branch_labels = None
depends_on = None


def upgrade():
    if 'details' not in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('user_todos')}:
        op.add_column('user_todos', sa.Column('details', sa.JSON(), nullable=True))
    if 'client_id' not in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('user_todos')}:
        op.add_column('user_todos', sa.Column('client_id', sa.String(36), nullable=True))


def downgrade():
    if 'details' in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('user_todos')}:
        op.drop_column('user_todos', 'details')
    if 'client_id' in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('user_todos')}:
        op.drop_column('user_todos', 'client_id')
