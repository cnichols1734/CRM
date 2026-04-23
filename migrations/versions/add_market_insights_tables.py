"""add service_areas, market_data_cache, rentcast_api_log tables

Revision ID: add_market_insights_tables
Revises: add_tax_protest_indexes
Create Date: 2026-04-22 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'add_market_insights_tables'
down_revision = 'add_tax_protest_indexes'
branch_labels = None
depends_on = None


SEED_SERVICE_AREAS = [
    {'slug': 'mont-belvieu', 'display_name': 'Mont Belvieu', 'zip_codes': ['77523']},
    {'slug': 'baytown',      'display_name': 'Baytown',      'zip_codes': ['77521', '77520']},
    {'slug': 'dayton',       'display_name': 'Dayton',       'zip_codes': ['77535']},
    {'slug': 'anahuac',      'display_name': 'Anahuac',      'zip_codes': ['77514']},
]


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if 'service_areas' not in tables:
        op.create_table(
            'service_areas',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('slug', sa.String(length=100), nullable=False),
            sa.Column('display_name', sa.String(length=200), nullable=False),
            sa.Column('zip_codes', sa.JSON(), nullable=False),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id', name='pk_service_areas'),
            sa.UniqueConstraint('slug', name='uq_service_areas_slug'),
        )
        op.create_index('ix_service_areas_sort_order', 'service_areas', ['sort_order'], unique=False)

        service_areas = sa.table(
            'service_areas',
            sa.column('slug', sa.String),
            sa.column('display_name', sa.String),
            sa.column('zip_codes', sa.JSON),
            sa.column('sort_order', sa.Integer),
        )
        op.bulk_insert(
            service_areas,
            [
                {**area, 'sort_order': idx}
                for idx, area in enumerate(SEED_SERVICE_AREAS)
            ],
        )

    if 'market_data_cache' not in tables:
        op.create_table(
            'market_data_cache',
            sa.Column('zip_code', sa.String(length=10), nullable=False),
            sa.Column('payload', sa.JSON(), nullable=True),
            sa.Column('refreshed_at', sa.DateTime(), nullable=True),
            sa.Column('refresh_started_at', sa.DateTime(), nullable=True),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('zip_code', name='pk_market_data_cache'),
        )
        op.create_index('ix_market_data_cache_refreshed_at', 'market_data_cache', ['refreshed_at'], unique=False)

    if 'rentcast_api_log' not in tables:
        op.create_table(
            'rentcast_api_log',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('zip_code', sa.String(length=10), nullable=True),
            sa.Column('endpoint', sa.String(length=100), nullable=False),
            sa.Column('status_code', sa.Integer(), nullable=True),
            sa.Column('latency_ms', sa.Integer(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('called_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id', name='pk_rentcast_api_log'),
        )
        op.create_index('ix_rentcast_api_log_called_at', 'rentcast_api_log', ['called_at'], unique=False)
        op.create_index('ix_rentcast_api_log_zip_code', 'rentcast_api_log', ['zip_code'], unique=False)


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if 'rentcast_api_log' in tables:
        op.drop_index('ix_rentcast_api_log_zip_code', table_name='rentcast_api_log')
        op.drop_index('ix_rentcast_api_log_called_at', table_name='rentcast_api_log')
        op.drop_table('rentcast_api_log')

    if 'market_data_cache' in tables:
        op.drop_index('ix_market_data_cache_refreshed_at', table_name='market_data_cache')
        op.drop_table('market_data_cache')

    if 'service_areas' in tables:
        op.drop_index('ix_service_areas_sort_order', table_name='service_areas')
        op.drop_table('service_areas')
