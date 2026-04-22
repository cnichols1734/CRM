"""Seed 25 sample contacts for the local dev database.

Every contact is assigned to an existing User + Organization and gets at least
one ContactGroup (the app's create-contact form requires this).

Usage:

    DATABASE_URL="sqlite:///$(pwd)/instance/crm_dev.db" \
        .venv/bin/python seed_contacts.py

Re-running is safe: contacts with the same email are skipped.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from app import create_app
from models import Contact, ContactGroup, Organization, User, db

RANDOM_SEED = 2026
ORG_SLUG = "origen-realty"
OWNER_EMAIL = "cassie@origenrealty.com"

# (first, last, email, groups, commission, days_since_last_contact_or_None)
CONTACTS: list[tuple[str, str, str, list[str], int, int | None]] = [
    # Top-of-list contacts shown in the screenshot
    ("Drake",   "Maye",    "ogtechnolog@gmail.com",      ["Buyer - New Potential Client"],       18000,  2),
    ("Cassie",  "Nichols", "cassie@origenrealty.com",    ["Buyer - Actively Showing Homes", "Seller - Active Listing"], 12000, 2),
    ("Debra",   "Nichols", "nicholsdeb@yahoo.com",       ["Seller - Previous Client"],           8000, 18),
    ("David",   "Millers", "cbn.a1fire@gmail.com",       ["Seller - Previous Client"],           8000,  2),
    ("Chris",   "Nichols", "test@test.com",              ["Buyer - New Potential Client"],       7500,115),

    # Additional contacts to reach 25
    ("Geordie", "Hess",    "geordie.hess@example.com",   ["Buyer - Actively Showing Homes"],     9500,  5),
    ("Sergio",  "Copaus",  "sergio.copaus@example.com",  ["Buyer - Actively Showing Homes"],     9500,  7),
    ("Olivia",  "Patel",   "olivia.patel@example.com",   ["Buyer - New Potential Client"],       7000, 21),
    ("Marcus",  "Reyes",   "marcus.reyes@example.com",   ["Buyer - New Potential Client"],       6500, 10),
    ("Priya",   "Singh",   "priya.singh@example.com",    ["Buyer - New Potential Client", "A"],  7500,  3),
    ("Jordan",  "Walsh",   "jordan.walsh@example.com",   ["Buyer - New Potential Client"],       6000, 30),
    ("Emily",   "Brooks",  "emily.brooks@example.com",   ["Buyer - New Potential Client"],       6000, 14),
    ("Noah",    "Chen",    "noah.chen@example.com",      ["Buyer - New Potential Client"],       5500, 42),
    ("Sofia",   "Martinez","sofia.martinez@example.com", ["Buyer - New Potential Client"],       5500,  9),
    ("Liam",    "Nguyen",  "liam.nguyen@example.com",    ["Buyer - New Potential Client"],       5000, 60),
    ("Aiden",   "Parker",  "aiden.parker@example.com",   ["Buyer - New Potential Client", "B"],  5000, 26),
    ("Harper",  "Quinn",   "harper.quinn@example.com",   ["Buyer - New Potential Client"],       4500, 33),
    ("Luca",    "Rossi",   "luca.rossi@example.com",     ["Buyer - New Potential Client"],       4500, 50),
    ("Mila",    "Stone",   "mila.stone@example.com",     ["Buyer - Previous Client"],            4000,120),
    ("Zara",    "Khan",    "zara.khan@example.com",      ["Seller - Previous Client"],           7500, 75),
    ("Ethan",   "Clarke",  "ethan.clarke@example.com",   ["Seller - Previous Client"],           6500, 90),
    ("Ava",     "Bennett", "ava.bennett@example.com",    ["Seller - Previous Client", "C"],      5000,100),
    ("Reese",   "Foster",  "reese.foster@example.com",   ["Friend"],                             3000, 12),
    ("Taylor",  "Hughes",  "taylor.hughes@example.com",  ["Friend"],                             3000, 40),
    ("Jamie",   "Lawrence","jamie.lawrence@example.com", ["Friend"],                             3000,  8),
]


def main() -> None:
    assert len(CONTACTS) == 25, f"expected 25 contacts, got {len(CONTACTS)}"

    rng = random.Random(RANDOM_SEED)
    app = create_app()
    today = date.today()

    with app.app_context():
        org = Organization.query.filter_by(slug=ORG_SLUG).first()
        if org is None:
            raise SystemExit(
                f"Organization with slug {ORG_SLUG!r} not found. "
                "Run seed_admin.py first."
            )

        owner = User.query.filter_by(email=OWNER_EMAIL).first()
        if owner is None:
            raise SystemExit(
                f"Owner user {OWNER_EMAIL!r} not found. Run seed_admin.py first."
            )

        groups_by_name = {g.name: g for g in ContactGroup.query.all()}
        missing = {
            name
            for _, _, _, names, _, _ in CONTACTS
            for name in names
            if name not in groups_by_name
        }
        if missing:
            raise SystemExit(
                f"ContactGroups missing: {sorted(missing)}. Run seed_admin.py."
            )

        created = 0
        skipped = 0
        for first, last, email, group_names, commission, days_ago in CONTACTS:
            if Contact.query.filter_by(email=email).first():
                skipped += 1
                continue

            last_contact = (
                today - timedelta(days=days_ago) if days_ago is not None else None
            )

            contact = Contact(
                user_id=owner.id,
                organization_id=org.id,
                created_by_id=owner.id,
                first_name=first,
                last_name=last,
                email=email,
                phone=f"({rng.randint(200, 989)}) {rng.randint(200, 989)}-{rng.randint(1000, 9999)}",
                city=rng.choice(["Dayton", "Houston", "Austin", "Dallas", "San Antonio"]),
                state="TX",
                potential_commission=Decimal(commission),
                last_email_date=last_contact,
                last_contact_date=last_contact,
            )
            contact.groups = [groups_by_name[n] for n in group_names]
            db.session.add(contact)
            created += 1

        db.session.commit()

        total_commission = sum(c for _, _, _, _, c, _ in CONTACTS)
        print(f"Created {created} contacts, skipped {skipped} existing.")
        print(f"Total potential commission across all 25: ${total_commission:,}")
        print(f"Average (all 25): ${total_commission / 25:,.2f}")


if __name__ == "__main__":
    main()
