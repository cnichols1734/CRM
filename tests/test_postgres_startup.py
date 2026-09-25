"""Check the production database driver without opening a database connection."""

import pytest

from config import Config
from models import db
from scripts.manage_db import create_app


@pytest.mark.parametrize("scheme", ["postgresql", "postgresql+psycopg2"])
def test_postgres_migration_app_loads_installed_driver(scheme):
    class PostgresConfig(Config):
        SQLALCHEMY_DATABASE_URI = f"{scheme}://user:password@127.0.0.1/crm_test"

    application, _ = create_app(PostgresConfig)
    with application.app_context():
        assert db.engine.dialect.driver == "psycopg2"
        assert db.engine.dialect.dbapi.__name__ == "psycopg2"
        db.engine.dispose()
