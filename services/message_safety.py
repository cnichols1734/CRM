"""Per-agent delivery preferences shared by the native inbox and APNs worker."""
from flask import g
from sqlalchemy import func
from models import ClientBrowseAccount, ClientPortalAccess, Contact, Transaction, TransactionParticipant, db
from services.portal_service import CLIENT_PORTAL_ROLES


def identity_index(org_id):
    cached = getattr(g, '_native_safety_identity_index', {})
    if org_id in cached:
        return cached[org_id]
    parents = {}

    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def link(left, right):
        parents[root(left)] = root(right)

    participants = db.session.query(TransactionParticipant.id, TransactionParticipant.contact_id,
        func.lower(func.trim(func.coalesce(func.nullif(Contact.email, ''), TransactionParticipant.email))),
        TransactionParticipant.role, TransactionParticipant.user_id, Contact.organization_id).join(
        Transaction, (Transaction.id == TransactionParticipant.transaction_id) & (Transaction.organization_id == org_id)
        ).outerjoin(Contact, Contact.id == TransactionParticipant.contact_id).filter(
        TransactionParticipant.organization_id == org_id).all()
    client_ids = set()
    email_ids = {}
    for participant_id, contact_id, email, role, user_id, contact_org in participants:
        key = f'participant:{participant_id}'
        root(key)
        if contact_id and contact_org == org_id:
            link(key, f'contact:{contact_id}')
        if role in CLIENT_PORTAL_ROLES:
            client_ids.add(participant_id)
            if user_id is None and (contact_id is None or contact_org == org_id) and email:
                email_ids.setdefault(email, set()).add(participant_id)
    grants = {}
    latest_active = {}
    access_rows = db.session.query(ClientPortalAccess.id, ClientPortalAccess.participant_id,
        ClientPortalAccess.is_active).join(TransactionParticipant,
        (TransactionParticipant.id == ClientPortalAccess.participant_id)
        & (TransactionParticipant.transaction_id == ClientPortalAccess.transaction_id)
        & (TransactionParticipant.organization_id == org_id)).filter(
        ClientPortalAccess.organization_id == org_id).order_by(ClientPortalAccess.id).all()
    for access_id, participant_id, active in access_rows:
        latest_active[participant_id] = active
        if active and participant_id in client_ids:
            grants[access_id] = participant_id
    accounts = db.session.query(ClientBrowseAccount.id, ClientBrowseAccount.contact_id,
        ClientBrowseAccount.linked_access_ids, ClientBrowseAccount.email, ClientBrowseAccount.email_verified_at
        ).filter_by(organization_id=org_id).all()
    for account_id, contact_id, linked_ids, email, verified_at in accounts:
        key = f'browse:{account_id}'
        root(key)
        if contact_id:
            link(key, f'contact:{contact_id}')
        for access_id in linked_ids or []:
            if type(access_id) is int and access_id in grants:
                link(key, f'participant:{grants[access_id]}')
        if verified_at:
            for participant_id in email_ids.get(email, set()):
                if latest_active.get(participant_id, True):
                    link(key, f'participant:{participant_id}')
    groups = {}
    for key in list(parents):
        groups.setdefault(root(key), set()).add(key)
    index = {key: groups[root(key)] for key in parents}
    g._native_safety_identity_index = {**cached, org_id: index}
    return index


def identity_keys(*, participant=None, account=None):
    subject = participant or account
    if subject is None:
        return set()
    key = f'participant:{participant.id}' if participant else f'browse:{account.id}'
    return identity_index(subject.organization_id).get(key, {key})


def is_blocked(user, *, participant=None, account=None):
    subject = participant or account
    if subject is None or subject.organization_id != user.organization_id:
        return False
    prefs = user.notification_prefs or {}
    blocked = set(prefs.get('blocked_clients', []))
    return bool(blocked and blocked & identity_keys(participant=participant, account=account))


def screened_text(body):
    import re
    import unicodedata
    value = unicodedata.normalize('NFKC', body or '').casefold()
    patterns = (r'\b(?:kill|rape)\s+you\b', r'\bchild\s+porn(?:ography)?\b',
                r'\bn[i1]gg(?:er|a)s?\b', r'\bfagg?ots?\b')
    if any(re.search(pattern, value) for pattern in patterns):
        return 'Message hidden by the safety filter. Report this conversation to request a review.'
    return body


def screened_messages(messages):
    result = []
    for message in messages:
        body = message.get('body')
        screened = screened_text(body)
        if screened != body:
            message = {**message, 'body': screened, 'attachments': [], 'web_listing': None}
        result.append(message)
    return result
