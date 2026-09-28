"""Keep the property context on client inquiries."""
from alembic import op
import sqlalchemy as sa

revision = 'add_inquiry_listing_snapshot'
down_revision = 'add_client_discovery'
branch_labels = None
depends_on = None


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('client_browse_inquiries')}
    if 'listing_snapshot' not in columns:
        op.add_column('client_browse_inquiries', sa.Column('listing_snapshot', sa.JSON(), nullable=True))


def downgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('client_browse_inquiries')}
    if 'listing_snapshot' in columns:
        op.drop_column('client_browse_inquiries', 'listing_snapshot')
