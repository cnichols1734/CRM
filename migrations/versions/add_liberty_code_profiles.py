"""Add Liberty code profiles table for strategy classification.

Revision ID: add_liberty_code_profiles
Revises: add_liberty_tax_tables
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_liberty_code_profiles'
down_revision = 'add_liberty_tax_tables'
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

    if not inspector.has_table('liberty_code_profiles'):
        op.create_table(
            'liberty_code_profiles',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('abs_subdv_cd', sa.String(10), nullable=False),
            sa.Column('abs_subdv_desc', sa.String(200)),
            sa.Column('property_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('avg_acreage', sa.Numeric(14, 4)),
            sa.Column('median_acreage', sa.Numeric(14, 4)),
            sa.Column('pct_with_situs_num', sa.Numeric(8, 4)),
            sa.Column('pct_with_sq_ft', sa.Numeric(8, 4)),
            sa.Column('distinct_street_count', sa.Integer()),
            sa.Column('distinct_zip_count', sa.Integer()),
            sa.Column('sample_addresses', sa.JSON()),
            sa.Column('sample_legal_descriptions', sa.JSON()),
            sa.Column('bucket', sa.String(30)),
            sa.Column('strategy', sa.String(20)),
            sa.Column('confidence', sa.Numeric(5, 4)),
            sa.Column('rationale', sa.Text()),
            sa.Column('model_name', sa.String(50)),
            sa.Column('prompt_version', sa.String(30)),
            sa.Column('classified_at', sa.DateTime()),
            sa.Column('updated_at', sa.DateTime()),
            sa.UniqueConstraint('abs_subdv_cd', name='uq_liberty_code_profile_abs_subdv_cd'),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, 'liberty_code_profiles', ['abs_subdv_cd']):
        op.create_index('ix_liberty_code_profile_abs_subdv_cd', 'liberty_code_profiles', ['abs_subdv_cd'], unique=True)
    if not _has_index(inspector, 'liberty_code_profiles', ['abs_subdv_desc']):
        op.create_index('ix_liberty_code_profile_abs_subdv_desc', 'liberty_code_profiles', ['abs_subdv_desc'])
    if not _has_index(inspector, 'liberty_code_profiles', ['bucket']):
        op.create_index('ix_liberty_code_profile_bucket', 'liberty_code_profiles', ['bucket'])
    if not _has_index(inspector, 'liberty_code_profiles', ['strategy']):
        op.create_index('ix_liberty_code_profile_strategy', 'liberty_code_profiles', ['strategy'])


def downgrade():
    op.drop_table('liberty_code_profiles')
