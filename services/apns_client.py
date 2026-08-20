"""HTTP/2 APNs sender. Only imported when APNS_* env is present."""
from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)


def send_alert(device_token: str, msg) -> bool:
    key_id = (os.environ.get('APNS_KEY_ID') or '').strip()
    team_id = (os.environ.get('APNS_TEAM_ID') or '').strip()
    key_pem = (os.environ.get('APNS_KEY') or '').strip()
    bundle_id = (os.environ.get('APNS_BUNDLE_ID') or '').strip()
    if not (key_id and team_id and key_pem and bundle_id and device_token):
        return False

    host = 'api.push.apple.com'
    if (os.environ.get('APNS_ENVIRONMENT') or '').strip().lower() == 'sandbox':
        host = 'api.sandbox.push.apple.com'

    preview = (msg.body or '').strip()
    if len(preview) > 120:
        preview = preview[:117] + '...'
    title = 'New message' if msg.sender == 'client' else 'Update from your agent'
    payload = {
        'aps': {
            'alert': {'title': title, 'body': preview or 'Open AgentFlow'},
            'sound': 'default',
        },
        'transaction_id': msg.transaction_id,
        'participant_id': msg.participant_id,
        'message_id': msg.id,
    }

    token = _apns_jwt(key_id, team_id, key_pem)
    if not token:
        return False

    try:
        import httpx
    except ImportError:
        logger.info('APNs skipped: httpx is not installed')
        return False

    url = f'https://{host}/3/device/{device_token}'
    headers = {
        'authorization': f'bearer {token}',
        'apns-topic': bundle_id,
        'apns-push-type': 'alert',
        'apns-priority': '10',
        'apns-expiration': '0',
    }
    try:
        response = httpx.post(url, headers=headers, content=json.dumps(payload), timeout=10.0)
    except Exception:
        logger.exception('APNs HTTP request failed')
        return False
    if response.status_code == 200:
        return True
    logger.info('APNs rejected token suffix %s: %s', device_token[-8:], response.status_code)
    return False


def _apns_jwt(key_id, team_id, key_pem):
    try:
        import jwt
    except ImportError:
        logger.info('APNs skipped: PyJWT is not installed')
        return None
    if 'BEGIN' not in key_pem:
        key_pem = (
            '-----BEGIN PRIVATE KEY-----\n'
            + key_pem
            + '\n-----END PRIVATE KEY-----'
        )
    now = int(time.time())
    return jwt.encode(
        {'iss': team_id, 'iat': now},
        key_pem,
        algorithm='ES256',
        headers={'kid': key_id},
    )
