#!/usr/bin/env python3
"""Create an isolated browser-test database with one seeded organization/user."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from tests.browser_test_support import configure_browser_test_environment

browser_env = configure_browser_test_environment(project_root)

from app import create_app
from models import Organization, User, db
from services.tenant_service import (
    create_default_groups_for_org,
    create_default_task_types_for_org,
    create_default_transaction_types_for_org,
)


def bootstrap_browser_test_db() -> None:
    """Reset the local browser-test database and seed one usable org/user."""
    app = create_app()

    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()

        organization = Organization(
            name="Browser Test Realty",
            slug="browser-test-realty",
            subscription_tier="pro",
            status="active",
            max_users=10,
            max_contacts=1000,
            can_invite_users=True,
            approved_at=datetime.utcnow(),
        )
        db.session.add(organization)
        db.session.flush()

        user = User(
            organization_id=organization.id,
            username=browser_env["test_username"],
            email=browser_env["test_username"],
            first_name="Browser",
            last_name="Tester",
            role="admin",
            org_role="owner",
            last_login=datetime.utcnow(),
        )
        user.set_password(browser_env["test_password"])
        db.session.add(user)
        db.session.commit()

        create_default_groups_for_org(organization.id)
        create_default_task_types_for_org(organization.id)
        create_default_transaction_types_for_org(organization.id)

        print(
            "Seeded browser test database "
            f"for {browser_env['test_username']} at {browser_env['database_url']}"
        )


if __name__ == "__main__":
    bootstrap_browser_test_db()
