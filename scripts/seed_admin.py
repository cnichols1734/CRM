"""One-shot seeder: creates an org, an admin user, contact groups, task types."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402  -- needs sys.path patched first.
from models import db, Organization, User, ContactGroup  # noqa: E402
from scripts.init_db import INITIAL_GROUPS, TASK_TYPES  # noqa: E402

app = create_app()
with app.app_context():
    db.create_all()

    # Organization
    org = Organization.query.filter_by(slug='origen-realty').first()
    if not org:
        org = Organization(
            name='Origen Realty',
            slug='origen-realty',
            subscription_tier='pro',
            max_users=10,
            max_contacts=None,
            status='active',
        )
        db.session.add(org)
        db.session.flush()
        print(f'Created organization id={org.id}')
    else:
        print(f'Organization already exists id={org.id}')

    # Admin user
    email = 'cassie@origenrealty.com'
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            username='cassie',
            email=email,
            first_name='Cassie',
            last_name='Nichols',
            role='admin',
            org_role='owner',
            is_super_admin=True,
            organization_id=org.id,
        )
        user.set_password('changeme123')
        db.session.add(user)
        print(f'Created admin user email={email} password=changeme123')
    else:
        print(f'User already exists email={email}')

    # Contact groups (globally for now; app uses them to validate contact forms)
    if ContactGroup.query.count() == 0:
        for g in INITIAL_GROUPS:
            db.session.add(ContactGroup(**g))
        print(f'Seeded {len(INITIAL_GROUPS)} contact groups')

    db.session.commit()
    print('OK')
