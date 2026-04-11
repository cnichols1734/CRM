"""Add missing indexes for tax protest search performance.

HCAD (1.6M rows):
  - expression index on upper(site_addr_1) for address lookup
  - btree index on lgl_2 for comparable subdivision filtering
  - partial composite index for comparable queries
  - trgm index on site_addr_1 for ILIKE fallback searches

Fort Bend (291k rows):
  - expression index on upper(situs_city) for city fallback searches

Revision ID: add_tax_protest_indexes
Revises: add_fort_bend_tax_tables
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa

revision = "add_tax_protest_indexes"
down_revision = "add_fort_bend_tax_tables"
branch_labels = None
depends_on = None


def _has_index(inspector, table_name, index_name):
    for idx in inspector.get_indexes(table_name):
        if idx["name"] == index_name:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    is_postgres = bind.dialect.name == "postgresql"

    # --- HCAD indexes ---
    if is_postgres:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_hcad_site_addr_1_upper "
            "ON hcad_properties (upper(site_addr_1))"
        )

    if not _has_index(inspector, "hcad_properties", "ix_hcad_lgl_2"):
        op.create_index("ix_hcad_lgl_2", "hcad_properties", ["lgl_2"])

    if is_postgres:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_hcad_comp_filters "
            "ON hcad_properties (lgl_2, site_addr_3, tot_mkt_val) "
            "WHERE site_addr_1 IS NOT NULL "
            "AND site_addr_1 != '' "
            "AND str_num IS NOT NULL "
            "AND str_num != '0' "
            "AND tot_mkt_val IS NOT NULL "
            "AND tot_mkt_val > 0"
        )

    if is_postgres:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_hcad_site_addr_1_trgm "
            "ON hcad_properties USING gin (site_addr_1 gin_trgm_ops)"
        )

    # --- Fort Bend indexes ---
    if is_postgres:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_fort_bend_situs_city_upper "
            "ON fort_bend_properties (upper(situs_city))"
        )


def downgrade():
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("DROP INDEX IF EXISTS ix_fort_bend_situs_city_upper")
        op.execute("DROP INDEX IF EXISTS ix_hcad_site_addr_1_trgm")
        op.execute("DROP INDEX IF EXISTS ix_hcad_comp_filters")
        op.execute("DROP INDEX IF EXISTS ix_hcad_site_addr_1_upper")

    op.drop_index("ix_hcad_lgl_2", table_name="hcad_properties", if_exists=True)
