"""Google connection round trips with real OAuth encoding and a fake HTTP provider."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from cryptography.fernet import Fernet

from config import Config
from models import UserEmailIntegration, db
from services import gmail_service


@pytest.fixture
def google_provider(monkeypatch, app, seed):
    monkeypatch.setattr(Config, 'GOOGLE_CLIENT_ID', 'test-client.apps.googleusercontent.com')
    monkeypatch.setattr(Config, 'GOOGLE_CLIENT_SECRET', 'test-client-secret')
    monkeypatch.setattr(Config, 'GMAIL_TOKEN_ENCRYPTION_KEY', Fernet.generate_key().decode())
    provider = {'challenge': None, 'exchanges': 0, 'reject': False}

    def send(session, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.request = request
        if request.url == 'https://oauth2.googleapis.com/token':
            provider['exchanges'] += 1
            body = request.body.decode() if isinstance(request.body, bytes) else request.body
            params = parse_qs(body)
            verifier = params.get('code_verifier', [''])[0]
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).rstrip(b'=').decode()
            if not verifier or challenge != provider['challenge'] or provider['reject']:
                response.status_code = 400
                payload = {'error': 'invalid_grant', 'error_description': 'Invalid code verifier'}
            else:
                payload = {
                    'access_token': 'test-access-token', 'refresh_token': 'test-refresh-token',
                    'token_type': 'Bearer', 'expires_in': 3600,
                    'scope': ' '.join(gmail_service.GMAIL_SCOPES),
                }
        elif request.url == 'https://www.googleapis.com/oauth2/v3/userinfo':
            payload = {'email': 'connected@example.com'}
        else:
            raise AssertionError(f'Unexpected external request: {request.method} {request.url}')
        response._content = json.dumps(payload).encode()
        response.headers['Content-Type'] = 'application/json'
        return response

    monkeypatch.setattr(requests.Session, 'send', send)
    yield provider
    with app.app_context():
        db.session.rollback()
        UserEmailIntegration.query.filter_by(user_id=seed['owner_a']).delete()
        db.session.commit()


def _authorization(response, provider):
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query)
    assert query['code_challenge_method'] == ['S256']
    provider['challenge'] = query['code_challenge'][0]
    return query['state'][0]


@pytest.mark.parametrize('calendar_reauth', [False, True])
def test_google_connection_preserves_pkce_and_saves_tokens(
    app, seed, owner_a_client, google_provider, monkeypatch, calendar_reauth,
):
    if calendar_reauth:
        with app.app_context():
            db.session.add(UserEmailIntegration(
                user_id=seed['owner_a'], organization_id=seed['org_a'],
                connected_email='previous@example.com', sync_enabled=True,
                calendar_sync_enabled=False,
            ))
            db.session.commit()
        monkeypatch.setattr('services.calendar_service.check_calendar_scope', lambda _: False)
        response = owner_a_client.post('/integrations/gmail/calendar/toggle')
    else:
        response = owner_a_client.get('/integrations/gmail/connect')
    state = _authorization(response, google_provider)
    callback = '/integrations/gmail/callback'
    response = owner_a_client.get(callback, query_string={'state': state, 'code': 'test-code'})
    assert response.status_code == 302
    with app.app_context():
        integration = UserEmailIntegration.query.filter_by(user_id=seed['owner_a']).one()
        assert integration.organization_id == seed['org_a']
        assert integration.connected_email == 'connected@example.com'
        assert integration.sync_enabled and integration.sync_status == 'active'
        assert integration.calendar_sync_enabled == calendar_reauth
        assert gmail_service.decrypt_token(integration.access_token_encrypted) == 'test-access-token'
        assert gmail_service.decrypt_token(integration.refresh_token_encrypted) == 'test-refresh-token'
    with owner_a_client.session_transaction() as session:
        assert 'gmail_oauth_state' not in session
        assert 'gmail_oauth_code_verifier' not in session
    owner_a_client.get(callback, query_string={'state': state, 'code': 'test-code'})
    assert google_provider['exchanges'] == 1


@pytest.mark.parametrize('failure', ['missing_verifier', 'wrong_state', 'denied', 'missing_code', 'token_error'])
def test_failed_callback_clears_attempt_without_connecting(
    app, seed, owner_a_client, google_provider, failure,
):
    state = _authorization(owner_a_client.get('/integrations/gmail/connect'), google_provider)
    query = {'state': state, 'code': 'test-code'}
    if failure == 'missing_verifier':
        with owner_a_client.session_transaction() as session:
            session.pop('gmail_oauth_code_verifier')
    elif failure == 'wrong_state':
        query['state'] = 'wrong-state'
    elif failure == 'denied':
        query = {'state': state, 'error': 'access_denied'}
    elif failure == 'missing_code':
        query.pop('code')
    else:
        google_provider['reject'] = True
    response = owner_a_client.get('/integrations/gmail/callback', query_string=query)
    assert response.status_code == 302
    assert google_provider['exchanges'] == (1 if failure == 'token_error' else 0)
    with owner_a_client.session_transaction() as session:
        assert 'gmail_oauth_state' not in session
        assert 'gmail_oauth_code_verifier' not in session
        if failure == 'missing_verifier':
            assert any('Please connect again' in message for _, message in session['_flashes'])
    with app.app_context():
        assert UserEmailIntegration.query.filter_by(user_id=seed['owner_a']).first() is None


def test_each_connection_attempt_gets_a_new_pkce_challenge(owner_a_client, google_provider):
    first_state = _authorization(owner_a_client.get('/integrations/gmail/connect'), google_provider)
    first_challenge = google_provider['challenge']
    second_state = _authorization(owner_a_client.get('/integrations/gmail/connect'), google_provider)
    assert second_state != first_state
    assert google_provider['challenge'] != first_challenge
