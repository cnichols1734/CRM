"""Add tax protest reference tables (chambers_properties, hcad_properties, hcad_buildings)

Revision ID: add_tax_protest_tables
Revises: add_extraction_status
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_tax_protest_tables'
down_revision = 'add_extraction_status'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    op.create_table(
        'chambers_properties',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('parcel_id', sa.String(50)),
        sa.Column('account', sa.String(50)),
        sa.Column('street', sa.String(200)),
        sa.Column('street_overflow', sa.String(200)),
        sa.Column('city', sa.String(100)),
        sa.Column('zip5', sa.String(10)),
        sa.Column('prop_street_number', sa.String(20)),
        sa.Column('prop_street', sa.String(100)),
        sa.Column('prop_street_dir', sa.String(10)),
        sa.Column('prop_city', sa.String(100)),
        sa.Column('prop_zip5', sa.String(10)),
        sa.Column('legal1', sa.String(500)),
        sa.Column('legal2', sa.String(500)),
        sa.Column('legal3', sa.String(500)),
        sa.Column('legal4', sa.String(500)),
        sa.Column('acres', sa.Numeric(14, 4)),
        sa.Column('market_value', sa.Integer()),
        sa.Column('improvement_hs_val', sa.Integer()),
        sa.Column('improvement_nhs_val', sa.Integer()),
    )

    op.create_index('ix_chambers_parcel_id', 'chambers_properties', ['parcel_id'])
    op.create_index('ix_chambers_account', 'chambers_properties', ['account'])
    op.create_index('ix_chambers_prop_street_number', 'chambers_properties', ['prop_street_number'])
    op.create_index('ix_chambers_prop_street', 'chambers_properties', ['prop_street'])
    op.create_index('ix_chambers_prop_zip5', 'chambers_properties', ['prop_zip5'])

    op.create_table(
        'hcad_properties',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('acct', sa.String(30)),
        sa.Column('str_num', sa.String(30)),
        sa.Column('str_num_sfx', sa.String(50)),
        sa.Column('str', sa.String(200)),
        sa.Column('str_sfx', sa.String(50)),
        sa.Column('str_sfx_dir', sa.String(50)),
        sa.Column('str_unit', sa.String(50)),
        sa.Column('site_addr_1', sa.String(200)),
        sa.Column('site_addr_2', sa.String(100)),
        sa.Column('site_addr_3', sa.String(30)),
        sa.Column('acreage', sa.Numeric(14, 4)),
        sa.Column('assessed_val', sa.Integer()),
        sa.Column('tot_appr_val', sa.Integer()),
        sa.Column('tot_mkt_val', sa.Integer()),
        sa.Column('lgl_1', sa.String(500)),
        sa.Column('lgl_2', sa.String(500)),
        sa.Column('lgl_3', sa.String(500)),
        sa.Column('lgl_4', sa.String(500)),
        sa.Column('neighborhood_code', sa.String(20)),
        sa.UniqueConstraint('acct', name='uq_hcad_acct'),
    )

    op.create_index('ix_hcad_acct', 'hcad_properties', ['acct'])
    op.create_index('ix_hcad_str_num', 'hcad_properties', ['str_num'])
    op.create_index('ix_hcad_str', 'hcad_properties', ['str'])
    op.create_index('ix_hcad_site_addr_3', 'hcad_properties', ['site_addr_3'])
    op.create_index('ix_hcad_neighborhood_code', 'hcad_properties', ['neighborhood_code'])

    op.create_table(
        'hcad_neighborhood_codes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('cd', sa.String(20)),
        sa.Column('grp_cd', sa.String(20)),
        sa.Column('dscr', sa.String(500)),
        sa.UniqueConstraint('cd', name='uq_hcad_nc_cd'),
    )

    op.create_index('ix_hcad_nc_cd', 'hcad_neighborhood_codes', ['cd'])

    op.create_table(
        'hcad_buildings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('acct', sa.String(30), sa.ForeignKey('hcad_properties.acct'), nullable=False),
        sa.Column('im_sq_ft', sa.Integer()),
    )

    op.create_index('ix_hcad_bld_acct', 'hcad_buildings', ['acct'])

    if is_postgres:
        op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_chambers_legal1_trgm '
            'ON chambers_properties USING gin (legal1 gin_trgm_ops)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_chambers_prop_street_trgm '
            'ON chambers_properties USING gin (prop_street gin_trgm_ops)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_hcad_lgl1_trgm '
            'ON hcad_properties USING gin (lgl_1 gin_trgm_ops)'
        )
        op.execute(
            'CREATE INDEX IF NOT EXISTS ix_hcad_str_trgm '
            'ON hcad_properties USING gin (str gin_trgm_ops)'
        )


def downgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    if is_postgres:
        op.execute('DROP INDEX IF EXISTS ix_hcad_str_trgm')
        op.execute('DROP INDEX IF EXISTS ix_hcad_lgl1_trgm')
        op.execute('DROP INDEX IF EXISTS ix_chambers_prop_street_trgm')
        op.execute('DROP INDEX IF EXISTS ix_chambers_legal1_trgm')

    op.drop_table('hcad_buildings')
    op.drop_table('hcad_neighborhood_codes')
    op.drop_table('hcad_properties')
    op.drop_table('chambers_properties')
