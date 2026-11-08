"""Per-agent delivery preferences shared by the native inbox and APNs worker."""
from models import ClientBrowseAccount, TransactionParticipant


def identity_keys(*, participant=None, account=None):
    keys = set()
    contact_id = getattr(participant, 'contact_id', None) or getattr(account, 'contact_id', None)
    org_id = getattr(participant, 'organization_id', None) or getattr(account, 'organization_id', None)
    if participant:
        keys.add(f'participant:{participant.id}')
    if account:
        keys.add(f'browse:{account.id}')
    if contact_id:
        keys.add(f'contact:{contact_id}')
        keys.update(f'participant:{row.id}' for row in TransactionParticipant.query.filter_by(
            organization_id=org_id, contact_id=contact_id).all())
        keys.update(f'browse:{row.id}' for row in ClientBrowseAccount.query.filter_by(
            organization_id=org_id, contact_id=contact_id).all())
    return keys


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
