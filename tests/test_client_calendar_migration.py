"""Preserve legacy grants while adding independent calendar revocation."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_calendar_migration_preserves_grants_and_is_repeatable(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/add_client_calendar_controls.py'
    spec = importlib.util.spec_from_file_location('calendar_controls_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f'sqlite:///{tmp_path / "calendar-migration.db"}')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE client_portal_access (id INTEGER PRIMARY KEY, session_version INTEGER NOT NULL, token VARCHAR(64))'))
        connection.execute(text("INSERT INTO client_portal_access VALUES (1, 7, 'legacy-grant')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()
        row = connection.execute(text('SELECT * FROM client_portal_access')).mappings().one()
        assert dict(row) == {'id': 1, 'session_version': 7, 'token': 'legacy-grant',
            'calendar_version': 1, 'calendar_issued_at': None, 'calendar_disabled_at': None}
        connection.execute(text("INSERT INTO client_portal_access (id, session_version, token) VALUES (2, 1, 'new-grant')"))
        assert connection.execute(text('SELECT calendar_version FROM client_portal_access WHERE id=2')).scalar_one() == 1
        migration.downgrade()
        migration.downgrade()
        assert {column['name'] for column in inspect(connection).get_columns('client_portal_access')} == {
            'id', 'session_version', 'token',
        }
        assert connection.execute(text('SELECT count(*) FROM client_portal_access')).scalar_one() == 2
