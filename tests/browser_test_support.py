"""Shared safety helpers for browser-based integration tests."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


DEFAULT_BROWSER_TEST_DB_URL = "sqlite:////tmp/browser_integration.db"
DEFAULT_BROWSER_TEST_USERNAME = "browser-test@example.com"
DEFAULT_BROWSER_TEST_PASSWORD = "browser-test-password123"
DEFAULT_BROWSER_TEST_SECRET_KEY = "browser-test-secret-key"


def is_local_database_url(database_url: str | None) -> bool:
    """Return True only for disposable local database targets."""
    if not database_url:
        return False

    parsed = urlparse(database_url)
    if parsed.scheme.startswith("sqlite"):
        return True

    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def configure_browser_test_environment(project_root: Path) -> dict[str, str]:
    """
    Load only browser-test-specific env and refuse any remote database target.

    Browser tests mutate data by design, so they must never point at a hosted
    database such as Supabase production.
    """
    env_test_path = project_root / ".env.test"
    if env_test_path.exists():
        load_dotenv(env_test_path)

    database_url = (
        os.getenv("TEST_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or DEFAULT_BROWSER_TEST_DB_URL
    )
    if not is_local_database_url(database_url):
        parsed = urlparse(database_url)
        target = parsed.hostname or parsed.path or "unknown"
        raise RuntimeError(
            "Refusing to run browser tests against a non-local DATABASE_URL "
            f"({target}). Use TEST_DATABASE_URL with sqlite or localhost only."
        )

    os.environ["DATABASE_URL"] = database_url
    os.environ.setdefault("SECRET_KEY", DEFAULT_BROWSER_TEST_SECRET_KEY)
    os.environ.setdefault("TEST_USERNAME", DEFAULT_BROWSER_TEST_USERNAME)
    os.environ.setdefault("TEST_PASSWORD", DEFAULT_BROWSER_TEST_PASSWORD)

    return {
        "database_url": database_url,
        "test_username": os.environ["TEST_USERNAME"],
        "test_password": os.environ["TEST_PASSWORD"],
    }


def ensure_local_base_url(base_url: str) -> None:
    """Browser tests should only ever target a local app instance."""
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(
            "Refusing to run browser tests against a non-local base URL "
            f"({base_url})."
        )
