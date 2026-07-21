import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402  -- needs sys.path patched first.
from models import db, ContactGroup, User  # noqa: E402
from services.tenant_service import (  # noqa: E402
    DEFAULT_CONTACT_GROUPS,
    create_default_groups_for_user,
)

# Kept for backward-compatible imports from scripts/seed_admin.py (legacy).
# Prefer DEFAULT_CONTACT_GROUPS from tenant_service.
INITIAL_GROUPS = DEFAULT_CONTACT_GROUPS

TASK_TYPES = [
    {
        "name": "Call",
        "sort_order": 10,
        "subtypes": [
            {"name": "Check-in", "sort_order": 1},
            {"name": "Schedule Showing", "sort_order": 2},
            {"name": "Discuss Offer", "sort_order": 3},
            {"name": "Follow-up", "sort_order": 4}
        ]
    },
    {
        "name": "Meeting",
        "sort_order": 20,
        "subtypes": [
            {"name": "Initial Consultation", "sort_order": 1},
            {"name": "Property Showing", "sort_order": 2},
            {"name": "Contract Review", "sort_order": 3},
            {"name": "Home Inspection", "sort_order": 4}
        ]
    },
    {
        "name": "Email",
        "sort_order": 30,
        "subtypes": [
            {"name": "Send Listings", "sort_order": 1},
            {"name": "Send Documents", "sort_order": 2},
            {"name": "Market Update", "sort_order": 3},
            {"name": "General Follow-up", "sort_order": 4}
        ]
    },
    {
        "name": "Document",
        "sort_order": 40,
        "subtypes": [
            {"name": "Prepare Contract", "sort_order": 1},
            {"name": "Review Documents", "sort_order": 2},
            {"name": "Submit Offer", "sort_order": 3},
            {"name": "Process Paperwork", "sort_order": 4}
        ]
    }
]


def init_db():
    app = create_app()
    with app.app_context():
        db.create_all()

        users = User.query.order_by(User.id).all()
        if not users:
            print(
                "No users found. Create an organization/user first "
                "(e.g. via signup or scripts/seed_admin.py), then re-run."
            )
            return

        print("Seeding per-user contact groups for existing users...")
        for user in users:
            if not user.organization_id:
                continue
            groups = create_default_groups_for_user(
                user.organization_id, user.id, commit=False
            )
            print(f"  user={user.email}: {len(groups)} groups")
        db.session.commit()

        all_groups = ContactGroup.query.order_by(
            ContactGroup.user_id, ContactGroup.sort_order
        ).all()
        print(f"\n{len(all_groups)} contact groups in database:")
        for group in all_groups[:50]:
            print(
                f"  user={group.user_id} {group.sort_order}: "
                f"{group.name} ({group.category})"
            )


if __name__ == '__main__':
    init_db()
