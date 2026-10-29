"""Exercise the new migration against an isolated SQLite schema."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_personal_resource_upgrade_preserves_rows_and_can_rerun():
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/add_personal_resources.py'
    spec = importlib.util.spec_from_file_location('personal_resource_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        connection.exec_driver_sql('CREATE TABLE organizations (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE user (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE agent_resources (id INTEGER PRIMARY KEY, label TEXT)')
        connection.exec_driver_sql("INSERT INTO agent_resources VALUES (1, 'Shared link')")
        connection.exec_driver_sql('INSERT INTO organizations VALUES (1)')
        connection.exec_driver_sql('INSERT INTO user VALUES (1)')
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            connection.exec_driver_sql("INSERT INTO personal_resources VALUES (1, 1, 1, 'Personal link', 'https://example.com')")
            migration.upgrade()
            migration.upgrade()
            assert connection.exec_driver_sql('SELECT label FROM agent_resources').scalar_one() == 'Shared link'
            assert connection.exec_driver_sql('SELECT label FROM personal_resources').scalar_one() == 'Personal link'
            assert 'ix_personal_resources_org_user' in {i['name'] for i in sa.inspect(connection).get_indexes('personal_resources')}
            connection.exec_driver_sql('DELETE FROM user WHERE id=1')
            assert connection.exec_driver_sql('SELECT count(*) FROM personal_resources').scalar_one() == 0
            assert connection.exec_driver_sql('SELECT count(*) FROM agent_resources').scalar_one() == 1
            migration.downgrade()
            assert not sa.inspect(connection).has_table('personal_resources')
            assert sa.inspect(connection).has_table('agent_resources')
