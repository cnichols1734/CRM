"""One-shot seeder: creates an org, an admin user, contact groups, task types."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402  -- needs sys.path patched first.
from models import db, Organization, User  # noqa: E402
from services.tenant_service import create_default_groups_for_user  # noqa: E402

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
        db.session.flush()
        print(f'Created admin user email={email} password=changeme123')
    else:
        print(f'User already exists email={email}')
        db.session.flush()

    groups = create_default_groups_for_user(org.id, user.id, commit=False)
    db.session.commit()
    print(f'Seeded {len(groups)} contact groups for user id={user.id}')
    print('OK')
