"""Signed opaque tokens for retention email attribution and churn surveys."""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

CHURN_REASONS = (
    'no_time',
    'unclear_value',
    'too_much_setup',
    'import_problem',
    'technical_problem',
    'login_problem',
    'missing_feature',
    'privacy_trust',
    'not_ready',
    'chose_another_tool',
    'only_exploring',
    'other',
)

CHURN_REASON_LABELS = {
    'no_time': 'I ran out of time',
    'unclear_value': "I wasn't sure what I'd get",
    'too_much_setup': 'It felt like too much setup',
    'import_problem': 'My contacts were hard to import',
    'technical_problem': 'Something technical went wrong',
    'login_problem': 'I had trouble logging back in',
    'missing_feature': 'A feature I needed was missing',
    'privacy_trust': 'I had privacy or trust concerns',
    'not_ready': "I'm not ready to switch CRMs yet",
    'chose_another_tool': 'I chose another tool',
    'only_exploring': 'I was only exploring',
    'other': 'Something else',
}


def _serializer(secret_key, salt):
    return URLSafeTimedSerializer(secret_key, salt=salt)


def make_churn_reason_token(app, *, user_id, reason, stage='stalled_3d'):
    if reason not in CHURN_REASONS:
        raise ValueError('invalid churn reason')
    return _serializer(app.config['SECRET_KEY'], 'retention-churn-reason').dumps({
        'uid': int(user_id),
        'reason': reason,
        'stage': stage,
    })


def parse_churn_reason_token(app, token, *, max_age=60 * 60 * 24 * 14):
    try:
        payload = _serializer(
            app.config['SECRET_KEY'], 'retention-churn-reason'
        ).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    reason = payload.get('reason')
    user_id = payload.get('uid')
    if reason not in CHURN_REASONS or not user_id:
        return None
    return {
        'user_id': int(user_id),
        'reason': reason,
        'stage': payload.get('stage') or 'stalled_3d',
    }


def make_email_click_token(app, *, user_id, campaign, stage=None):
    return _serializer(app.config['SECRET_KEY'], 'retention-email-click').dumps({
        'uid': int(user_id),
        'campaign': campaign,
        'stage': stage,
    })


def parse_email_click_token(app, token, *, max_age=60 * 60 * 24 * 30):
    try:
        payload = _serializer(
            app.config['SECRET_KEY'], 'retention-email-click'
        ).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict) or not payload.get('uid'):
        return None
    return {
        'user_id': int(payload['uid']),
        'campaign': payload.get('campaign'),
        'stage': payload.get('stage'),
    }
