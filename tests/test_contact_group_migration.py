"""Migration backfill test for per-user contact groups.

Builds a minimal legacy schema in a throwaway SQLite DB, inserts org-scoped
groups + memberships, runs the migration upgrade function, and verifies remap.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    PROJECT_ROOT / 'migrations' / 'versions' / 'add_per_user_contact_groups.py'
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        'add_per_user_contact_groups', MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_per_user_contact_groups_migration_backfill():
    assert MIGRATION_PATH.exists()

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / 'migrate_groups.db'
        db_url = f'sqlite:///{db_path}'

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE organizations (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    slug VARCHAR(100) NOT NULL UNIQUE
                )
            """))
            conn.execute(text("""
                CREATE TABLE "user" (
                    id INTEGER PRIMARY KEY,
                    organization_id INTEGER,
                    username VARCHAR(80),
                    email VARCHAR(120)
                )
            """))
            conn.execute(text("""
                CREATE TABLE contact (
                    id INTEGER PRIMARY KEY,
                    organization_id INTEGER,
                    user_id INTEGER NOT NULL,
                    first_name VARCHAR(80) NOT NULL,
                    last_name VARCHAR(80) NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE contact_group (
                    id INTEGER PRIMARY KEY,
                    organization_id INTEGER,
                    name VARCHAR(100) NOT NULL,
                    category VARCHAR(50) NOT NULL,
                    sort_order INTEGER NOT NULL,
                    created_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE UNIQUE INDEX uq_contact_group_org_name
                ON contact_group (organization_id, name)
            """))
            conn.execute(text("""
                CREATE TABLE contact_groups (
                    contact_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    PRIMARY KEY (contact_id, group_id)
                )
            """))

            conn.execute(text(
                "INSERT INTO organizations (id, name, slug) "
                "VALUES (1, 'Migrate Org', 'migrate-org')"
            ))
            conn.execute(text(
                "INSERT INTO \"user\" (id, organization_id, username, email) "
                "VALUES (10, 1, 'owner', 'owner@migrate.test'), "
                "(11, 1, 'agent', 'agent@migrate.test')"
            ))
            conn.execute(text(
                "INSERT INTO contact_group "
                "(id, organization_id, name, category, sort_order, created_at) "
                "VALUES "
                "(100, 1, 'Buyers', 'Status', 1, CURRENT_TIMESTAMP), "
                "(101, 1, 'Sellers', 'Status', 2, CURRENT_TIMESTAMP)"
            ))
            conn.execute(text(
                "INSERT INTO contact "
                "(id, organization_id, user_id, first_name, last_name) "
                "VALUES (1000, 1, 10, 'Owned', 'ByOwner'), "
                "(1001, 1, 11, 'Owned', 'ByAgent')"
            ))
            conn.execute(text(
                "INSERT INTO contact_groups (contact_id, group_id) "
                "VALUES (1000, 100), (1001, 101)"
            ))

        mig = _load_migration_module()

        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            op_proxy = Operations(ctx)

            import alembic.op as alembic_op
            originals = {
                'get_bind': alembic_op.get_bind,
                'add_column': alembic_op.add_column,
                'create_index': alembic_op.create_index,
                'drop_index': alembic_op.drop_index,
                'batch_alter_table': alembic_op.batch_alter_table,
            }
            alembic_op.get_bind = lambda: conn
            alembic_op.add_column = op_proxy.add_column
            alembic_op.create_index = op_proxy.create_index
            alembic_op.drop_index = op_proxy.drop_index
            alembic_op.batch_alter_table = op_proxy.batch_alter_table

            try:
                mig.upgrade()
            finally:
                for name, value in originals.items():
                    setattr(alembic_op, name, value)

        with engine.connect() as conn:
            null_owners = conn.execute(text(
                "SELECT COUNT(*) FROM contact_group WHERE user_id IS NULL"
            )).scalar()
            assert null_owners == 0

            owner_count = conn.execute(text(
                "SELECT COUNT(*) FROM contact_group WHERE user_id = 10"
            )).scalar()
            agent_count = conn.execute(text(
                "SELECT COUNT(*) FROM contact_group WHERE user_id = 11"
            )).scalar()
            assert owner_count == 2
            assert agent_count == 2

            owner_membership = conn.execute(text("""
                SELECT g.name, g.user_id
                FROM contact_groups cg
                JOIN contact_group g ON g.id = cg.group_id
                WHERE cg.contact_id = 1000
            """)).fetchall()
            assert len(owner_membership) == 1
            assert owner_membership[0][0] == 'Buyers'
            assert owner_membership[0][1] == 10

            agent_membership = conn.execute(text("""
                SELECT g.name, g.user_id
                FROM contact_groups cg
                JOIN contact_group g ON g.id = cg.group_id
                WHERE cg.contact_id = 1001
            """)).fetchall()
            assert len(agent_membership) == 1
            assert agent_membership[0][0] == 'Sellers'
            assert agent_membership[0][1] == 11

            same_name = conn.execute(text("""
                SELECT COUNT(*) FROM contact_group
                WHERE name = 'Buyers'
            """)).scalar()
            assert same_name == 2
