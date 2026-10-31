"""Native agent registration and email-code password recovery."""
from datetime import datetime
import base64
import json
import hashlib
import hmac
import secrets

from flask import current_app
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func
from werkzeug.security import generate_password_hash

from models import ActivationEvent, Organization, User, db
from services.activation_service import record_event
from services.tenant_service import (
    create_default_groups_for_user, create_default_task_types_for_org,
    create_default_transaction_types_for_org,
)

RESET_SECONDS = 900


def find_email(email):
    return User.query.filter(func.lower(User.email) == email.lower()).first()


def create_account(data):
    from utils import generate_unique_slug
    from services.marketing.system_templates import seed_for_org
    company = data['company_name']
    org = Organization(name=company, slug=generate_unique_slug(company),
                       subscription_tier='free', status='active',
                       approved_at=datetime.utcnow(), max_users=1,
                       max_contacts=10000, can_invite_users=False)
    db.session.add(org)
    db.session.flush()
    base = data['email'].split('@')[0][:60]
    username = base
    counter = 1
    while User.query.filter_by(username=username).first():
        username = f'{base}{counter}'
        counter += 1
    user = User(organization_id=org.id, org_role='owner', role='admin',
                is_super_admin=False, username=username, email=data['email'],
                first_name=data['first_name'], last_name=data['last_name'],
                last_login=datetime.utcnow())
    user.set_password(data['password'])
    db.session.add(user)
    db.session.flush()
    create_default_groups_for_user(org.id, user.id, commit=False)
    create_default_task_types_for_org(org.id, commit=False)
    create_default_transaction_types_for_org(org.id, commit=False)
    seed_for_org(org.id, commit=False)
    db.session.commit()
    # Email and inbox failures must not undo a usable account.
    try:
        from services.inbox_provisioning import provision_inbox_address
        provision_inbox_address(user)
        from services.sendgrid_outbound import send_account_welcome
        send_account_welcome(user)
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Native account welcome setup failed user_id=%s', user.id)
    record_event(ActivationEvent.ACCOUNT_CREATED, user=user,
                 data={'source': 'native_agent'}, once=True)
    return user


def _digest(value):
    return hmac.new(str(current_app.config['SECRET_KEY']).encode(),
                    value.encode(), hashlib.sha256).hexdigest()


def _serializer():
    key = bytes.fromhex(_digest('agent-password-code-v1'))
    return Fernet(base64.urlsafe_b64encode(key))


def make_reset_challenge(user):
    code = f'{secrets.randbelow(100000000):08d}'
    nonce = secrets.token_hex(16)
    # The client receives neither the code nor an offline-verifiable code hash.
    payload = {'uid': str(user.id if user else 0).zfill(20), 'nonce': nonce,
               'proof': _digest(nonce + ':' + code),
               'password': _digest(user.password_hash) if user else secrets.token_hex(32),
               'sv': (user.session_version or 1) if user else 1}
    return _serializer().encrypt(json.dumps(payload).encode()).decode(), code


def complete_reset(challenge, code, password):
    try:
        if base64.urlsafe_b64encode(base64.urlsafe_b64decode(challenge)).decode() != challenge:
            return False
        payload = json.loads(_serializer().decrypt(challenge.encode(), ttl=RESET_SECONDS))
        if not hmac.compare_digest(payload['proof'], _digest(payload['nonce'] + ':' + code)):
            return False
        user = db.session.get(User, int(payload['uid']))
        if not user or not hmac.compare_digest(payload['password'], _digest(user.password_hash)):
            return False
        if (user.session_version or 1) != payload['sv']:
            return False
    except (InvalidToken, KeyError, TypeError, ValueError):
        return False
    # Compare-and-swap prevents concurrent requests from replaying the same code.
    changed = User.query.filter_by(id=user.id, password_hash=user.password_hash,
                                   session_version=user.session_version).update({
        'password_hash': generate_password_hash(password),
        'session_version': (user.session_version or 1) + 1,
    }, synchronize_session=False)
    if changed != 1:
        db.session.rollback()
        return False
    from services.mcp.access import revoke_user_mcp_grants
    revoke_user_mcp_grants(user)
    db.session.commit()
    record_event(ActivationEvent.PASSWORD_RESET_COMPLETED, user=user, sync_person=False)
    return True
