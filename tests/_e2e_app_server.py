#!/usr/bin/env python3
"""Subprocess launcher used by the onboarding E2E test.

Boots the real Flask app against a throwaway local SQLite database, creates the
schema, and serves it without the reloader. The runner (run_onboarding_e2e.py)
sets DATABASE_URL/SECRET_KEY/E2E_PORT in the environment before spawning this.

Safety: refuses to start against anything other than a local SQLite/localhost
database, so a stray DATABASE_URL pointing at Supabase can never be touched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.browser_test_support import is_local_database_url


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not is_local_database_url(database_url):
        raise RuntimeError(
            "E2E server refusing to boot against a non-local DATABASE_URL "
            f"({database_url!r}). This launcher is for disposable local databases only."
        )

    from app import create_app
    from models import db

    app = create_app()
    with app.app_context():
        db.create_all()

    port = int(os.environ.get("E2E_PORT", "5099"))
    # threaded=True lets the browser pull static assets and fire XHRs in
    # parallel with the page navigation without serializing into a stall.
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
