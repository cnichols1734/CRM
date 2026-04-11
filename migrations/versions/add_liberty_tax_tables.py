"""Add Liberty County tax protest reference tables.

Revision ID: add_liberty_tax_tables
Revises: add_chambers_sq_ft
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_liberty_tax_tables'
down_revision = 'add_chambers_sq_ft'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    op.create_table(
        'liberty_properties',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('prop_id', sa.String(20), nullable=False),
        sa.Column('geo_id', sa.String(50)),
        sa.Column('prop_type_cd', sa.String(10)),
        sa.Column('situs_num', sa.String(20)),
        sa.Column('situs_street_prefx', sa.String(20)),
        sa.Column('situs_street', sa.String(100)),
        sa.Column('situs_street_suffix', sa.String(20)),
        sa.Column('situs_unit', sa.String(20)),
        sa.Column('situs_city', sa.String(100)),
        sa.Column('situs_zip', sa.String(10)),
        sa.Column('site_addr_1', sa.String(200)),
        sa.Column('normalized_site_addr', sa.String(200)),
        sa.Column('legal_desc', sa.String(500)),
        sa.Column('legal_desc2', sa.String(500)),
        sa.Column('legal_acreage', sa.Numeric(16, 4)),
        sa.Column('abs_subdv_cd', sa.String(10)),
        sa.Column('abs_subdv_desc', sa.String(200)),
        sa.Column('appraised_val', sa.Integer()),
        sa.Column('assessed_val', sa.Integer()),
        sa.Column('market_value', sa.Integer()),
        sa.Column('imprv_hstd_val', sa.Integer()),
        sa.Column('imprv_non_hstd_val', sa.Integer()),
        sa.Column('sq_ft', sa.Integer()),
        sa.Column('is_residential_home', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint('prop_id', name='uq_liberty_prop_id'),
    )

    op.create_index('ix_liberty_prop_id', 'liberty_properties', ['prop_id'])
    op.create_index('ix_liberty_geo_id', 'liberty_properties', ['geo_id'])
    op.create_index('ix_liberty_prop_type_cd', 'liberty_properties', ['prop_type_cd'])
    op.create_index('ix_liberty_situs_num', 'liberty_properties', ['situs_num'])
    op.create_index('ix_liberty_situs_street', 'liberty_properties', ['situs_street'])
    op.create_index('ix_liberty_situs_zip', 'liberty_properties', ['situs_zip'])
    op.create_index('ix_liberty_normalized_site_addr', 'liberty_properties', ['normalized_site_addr'])
    op.create_index('ix_liberty_abs_subdv_cd', 'liberty_properties', ['abs_subdv_cd'])
    op.create_index('ix_liberty_abs_subdv_desc', 'liberty_properties', ['abs_subdv_desc'])
    op.create_index('ix_liberty_is_residential_home', 'liberty_properties', ['is_residential_home'])
    op.create_index(
        'ix_liberty_comp_filters',
        'liberty_properties',
        ['is_residential_home', 'abs_subdv_cd', 'situs_zip', 'market_value'],
    )

    op.create_table(
        'liberty_improvements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('prop_id', sa.String(20), sa.ForeignKey('liberty_properties.prop_id'), nullable=False),
        sa.Column('imprv_id', sa.String(20), nullable=False),
        sa.Column('imprv_type_cd', sa.String(10)),
        sa.Column('imprv_type_desc', sa.String(50)),
        sa.Column('imprv_homesite', sa.String(1)),
        sa.Column('imprv_val', sa.Integer()),
        sa.Column('residential_sq_ft', sa.Integer()),
        sa.Column('is_residential', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint('prop_id', 'imprv_id', name='uq_liberty_improvement_prop_imprv'),
    )

    op.create_index('ix_liberty_impr_prop_id', 'liberty_improvements', ['prop_id'])
    op.create_index('ix_liberty_impr_imprv_id', 'liberty_improvements', ['imprv_id'])
    op.create_index('ix_liberty_impr_is_residential', 'liberty_improvements', ['is_residential'])

    if is_postgres:
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_liberty_site_addr_trgm '
            'ON liberty_properties USING gin (site_addr_1 gin_trgm_ops)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_liberty_situs_street_trgm '
            'ON liberty_properties USING gin (situs_street gin_trgm_ops)'
        )


def downgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    if is_postgres:
        op.execute('DROP INDEX IF EXISTS ix_liberty_situs_street_trgm')
        op.execute('DROP INDEX IF EXISTS ix_liberty_site_addr_trgm')

    op.drop_table('liberty_improvements')
    op.drop_table('liberty_properties')
