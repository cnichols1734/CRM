from pathlib import Path

import pytest

from tests.browser_test_support import (
    DEFAULT_BROWSER_TEST_DB_URL,
    configure_browser_test_environment,
    ensure_local_base_url,
    is_local_database_url,
)


def test_is_local_database_url_accepts_local_targets():
    assert is_local_database_url("sqlite:////tmp/browser.db")
    assert is_local_database_url("postgresql://localhost/browser_test")
    assert is_local_database_url("postgresql://127.0.0.1/browser_test")


def test_is_local_database_url_rejects_remote_targets():
    assert not is_local_database_url("postgresql://aws-1-us-east-2.pooler.supabase.com/postgres")


def test_configure_browser_test_environment_defaults_to_safe_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_USERNAME", raising=False)
    monkeypatch.delenv("TEST_PASSWORD", raising=False)

    values = configure_browser_test_environment(Path("/tmp/nonexistent-project-root"))

    assert values["database_url"] == DEFAULT_BROWSER_TEST_DB_URL
    assert values["test_username"] == "browser-test@example.com"
    assert values["test_password"] == "browser-test-password123"


def test_configure_browser_test_environment_rejects_remote_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://aws-1-us-east-2.pooler.supabase.com/postgres")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="Refusing to run browser tests"):
        configure_browser_test_environment(Path("/tmp/nonexistent-project-root"))


def test_ensure_local_base_url_rejects_remote_hosts():
    with pytest.raises(RuntimeError, match="non-local base URL"):
        ensure_local_base_url("https://crm.example.com")
