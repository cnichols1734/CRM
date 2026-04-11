"""Add Fort Bend County tax protest reference tables.

Revision ID: add_fort_bend_tax_tables
Revises: add_liberty_code_profiles
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_fort_bend_tax_tables'
down_revision = 'add_liberty_code_profiles'
branch_labels = None
depends_on = None


def _has_index(inspector, table_name, columns):
    target = tuple(columns)
    for idx in inspector.get_indexes(table_name):
        if tuple(idx.get('column_names') or []) == target:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    is_postgres = bind.dialect.name == 'postgresql'

    if not inspector.has_table('fort_bend_properties'):
        op.create_table(
            'fort_bend_properties',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('property_id', sa.String(20), nullable=False),
            sa.Column('quick_ref_id', sa.String(20)),
            sa.Column('property_number', sa.String(40)),
            sa.Column('legal_desc', sa.String(1000)),
            sa.Column('legal_location_code', sa.String(50)),
            sa.Column('legal_location_desc', sa.String(500)),
            sa.Column('legal_acres', sa.Numeric(16, 4)),
            sa.Column('market_value', sa.Integer()),
            sa.Column('assessed_value', sa.Integer()),
            sa.Column('land_value', sa.Integer()),
            sa.Column('improvement_value', sa.Integer()),
            sa.Column('sq_ft', sa.Integer()),
            sa.Column('nbhd_code', sa.String(20)),
            sa.Column('nbhd_desc', sa.String(500)),
            sa.Column('situs', sa.String(255)),
            sa.Column('site_addr_1', sa.String(200)),
            sa.Column('normalized_site_addr', sa.String(200)),
            sa.Column('situs_pre_directional', sa.String(20)),
            sa.Column('situs_street_number', sa.String(20)),
            sa.Column('situs_street_name', sa.String(100)),
            sa.Column('situs_street_suffix', sa.String(20)),
            sa.Column('situs_post_directional', sa.String(20)),
            sa.Column('situs_city', sa.String(100)),
            sa.Column('situs_state', sa.String(10)),
            sa.Column('situs_zip', sa.String(10)),
            sa.Column('acreage', sa.Numeric(16, 4)),
            sa.Column('is_residential_home', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.UniqueConstraint('property_id', name='uq_fort_bend_property_id'),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, 'fort_bend_properties', ['property_id']):
        op.create_index('ix_fort_bend_property_id', 'fort_bend_properties', ['property_id'], unique=True)
    if not _has_index(inspector, 'fort_bend_properties', ['quick_ref_id']):
        op.create_index('ix_fort_bend_quick_ref_id', 'fort_bend_properties', ['quick_ref_id'])
    if not _has_index(inspector, 'fort_bend_properties', ['property_number']):
        op.create_index('ix_fort_bend_property_number', 'fort_bend_properties', ['property_number'])
    if not _has_index(inspector, 'fort_bend_properties', ['normalized_site_addr']):
        op.create_index('ix_fort_bend_normalized_site_addr', 'fort_bend_properties', ['normalized_site_addr'])
    if not _has_index(inspector, 'fort_bend_properties', ['situs_street_number']):
        op.create_index('ix_fort_bend_situs_street_number', 'fort_bend_properties', ['situs_street_number'])
    if not _has_index(inspector, 'fort_bend_properties', ['situs_street_name']):
        op.create_index('ix_fort_bend_situs_street_name', 'fort_bend_properties', ['situs_street_name'])
    if not _has_index(inspector, 'fort_bend_properties', ['situs_zip']):
        op.create_index('ix_fort_bend_situs_zip', 'fort_bend_properties', ['situs_zip'])
    if not _has_index(inspector, 'fort_bend_properties', ['nbhd_code']):
        op.create_index('ix_fort_bend_nbhd_code', 'fort_bend_properties', ['nbhd_code'])
    if not _has_index(inspector, 'fort_bend_properties', ['nbhd_desc']):
        op.create_index('ix_fort_bend_nbhd_desc', 'fort_bend_properties', ['nbhd_desc'])
    if not _has_index(inspector, 'fort_bend_properties', ['is_residential_home']):
        op.create_index('ix_fort_bend_is_residential_home', 'fort_bend_properties', ['is_residential_home'])
    if not _has_index(inspector, 'fort_bend_properties', ['is_residential_home', 'nbhd_code', 'situs_zip', 'market_value']):
        op.create_index(
            'ix_fort_bend_comp_filters',
            'fort_bend_properties',
            ['is_residential_home', 'nbhd_code', 'situs_zip', 'market_value'],
        )

    if is_postgres:
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_fort_bend_site_addr_trgm '
            'ON fort_bend_properties USING gin (site_addr_1 gin_trgm_ops)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_fort_bend_situs_street_trgm '
            'ON fort_bend_properties USING gin (situs_street_name gin_trgm_ops)'
        )


def downgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    if is_postgres:
        op.execute('DROP INDEX IF EXISTS ix_fort_bend_situs_street_trgm')
        op.execute('DROP INDEX IF EXISTS ix_fort_bend_site_addr_trgm')

    op.drop_table('fort_bend_properties')
