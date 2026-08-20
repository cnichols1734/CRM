"""Shared setup for marketing integration tests."""
from models import Contact, Organization, User, db
from services.marketing import templates as tpl


def enable_campaigns(org, *, broker=True):
    flags = dict(org.feature_flags or {})
    flags['EMAIL_CAMPAIGNS'] = True
    org.feature_flags = flags
    if broker:
        org.broker_name = org.broker_name or org.name or 'Test Realty'
        org.broker_license_number = org.broker_license_number or '1234567'
        org.broker_address = org.broker_address or '100 Main St, Austin, TX 78701'
    db.session.flush()
    return org


def load_org_user(seed, org_key='org_a', user_key='owner_a'):
    org = db.session.get(Organization, seed[org_key])
    user = db.session.get(User, seed[user_key])
    return org, user


def ready_template(org, user, name='Check-in'):
    return tpl.save(
        organization_id=org.id,
        user_id=user.id,
        org=org,
        agent=user,
        name=name,
        subject='Checking in',
        preheader='Just a note',
        blocks=[
            {'type': 'paragraph', 'text': 'Hi {{contact.first_name|there}}, just checking in.'},
            {'type': 'signature'},
        ],
        category='check_in',
        visibility='org',
    )


def make_contact(org, user, *, first, last, email, **kwargs):
    contact = Contact(
        organization_id=org.id,
        user_id=user.id,
        created_by_id=user.id,
        first_name=first,
        last_name=last,
        email=email,
        city=kwargs.get('city'),
        state=kwargs.get('state'),
        zip_code=kwargs.get('zip_code'),
        marketing_consent=kwargs.get('marketing_consent', 'unknown'),
    )
    db.session.add(contact)
    db.session.flush()
    return contact
