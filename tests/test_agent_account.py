"""Native account flows use isolated SQLite and never send real email."""
import pytest
from models import User, Organization, ContactGroup, TaskType, TransactionType, ClientBrowseRateLimit, db
from services import agent_account
from services.agent_auth import issue_agent_jwt, load_user_from_jwt

BASE = '/api/agent/v1'

@pytest.fixture(autouse=True)
def isolate_limits(app, monkeypatch):
    with app.app_context():
        ClientBrowseRateLimit.query.delete()
        db.session.commit()
    monkeypatch.setattr('services.inbox_provisioning.provision_inbox_address', lambda user: None)
    monkeypatch.setattr('services.sendgrid_outbound.send_account_welcome', lambda user: True)


def registration(email='native-new@example.com'):
    return dict(email=email, first_name='Native', last_name='Agent', company_name='Native Realty',
                password='a-new-password', confirm_password='a-new-password', accepted_terms='true')


def test_native_registration_creates_usable_crm_workspace(app, client):
    response = client.post(BASE + '/accounts', json=registration())
    assert response.status_code == 201, response.json
    assert 'Location' not in response.headers
    token = response.json['token']
    with app.app_context():
        user = agent_account.find_email('native-new@example.com')
        assert user.org_role == 'owner' and not user.is_super_admin
        assert user.organization.status == 'active'
        assert user.organization.subscription_tier == 'free'
        assert user.organization.max_contacts == 10000
        assert ContactGroup.query.filter_by(user_id=user.id).count() > 0
        assert TaskType.query.filter_by(organization_id=user.organization_id).count() > 0
        assert TransactionType.query.filter_by(organization_id=user.organization_id).count() == 5
    assert client.get(BASE + '/me', headers={'Authorization': 'Bearer ' + token}).status_code == 200
    assert client.post(BASE + '/session', json={'email': 'NATIVE-NEW@example.com', 'password': 'a-new-password'}).status_code == 200
    assert client.post(BASE + '/accounts', json=registration('NATIVE-NEW@example.com')).status_code == 409


def test_registration_validation_and_atomic_rollback(app, client, monkeypatch):
    data = registration('rollback@example.com')
    data['accepted_terms'] = 'false'
    assert client.post(BASE + '/accounts', json=data).status_code == 400
    data['accepted_terms'] = 'true'
    def fail(*args, **kwargs):
        raise RuntimeError('seed failed')
    monkeypatch.setattr(agent_account, 'create_default_transaction_types_for_org', fail)
    with app.app_context():
        before = Organization.query.count()
    assert client.post(BASE + '/accounts', json=data).status_code == 503
    with app.app_context():
        assert agent_account.find_email(data['email']) is None
        assert Organization.query.count() == before


def test_reset_code_changes_password_once_and_revokes_session(app, seed, client, monkeypatch):
    codes = []
    monkeypatch.setattr('services.email_service.EmailService.send_password_reset_code', lambda self, user, code: codes.append(code) or True)
    with app.app_context():
        user = User.query.filter_by(email='owner_b@test.com').one()
        old_token = issue_agent_jwt(user)
    response = client.post(BASE + '/password-reset', json={'email': 'OWNER_B@test.com'})
    assert response.status_code == 200 and len(codes) == 1
    payload = dict(challenge=response.json['challenge'], code=codes[0], password='new-secure-password', confirm_password='new-secure-password')
    wrong = dict(payload, code='bad-code')
    assert client.post(BASE + '/password-reset/complete', json=wrong).status_code == 400
    assert client.post(BASE + '/password-reset/complete', json=payload).status_code == 200
    assert client.post(BASE + '/password-reset/complete', json=payload).status_code == 400
    with app.app_context():
        user = User.query.filter_by(email='owner_b@test.com').one()
        assert user.check_password('new-secure-password')
        assert load_user_from_jwt(old_token)[0] is None
        user.set_password('password123')
        db.session.commit()


def test_unknown_email_and_delivery_failure_have_same_response(app, seed, client, monkeypatch):
    monkeypatch.setattr('services.email_service.EmailService.send_password_reset_code', lambda *args: False)
    known = client.post(BASE + '/password-reset', json={'email': 'owner_a@test.com'})
    unknown = client.post(BASE + '/password-reset', json={'email': 'unknown@example.com'})
    assert known.status_code == unknown.status_code == 200
    assert known.json.keys() == unknown.json.keys()
    assert len(known.json['challenge']) == len(unknown.json['challenge'])
    assert 'code' not in known.json
    payload = dict(challenge=unknown.json['challenge'], code='00000000', password='new-password', confirm_password='new-password')
    for _ in range(5):
        assert client.post(BASE + '/password-reset/complete', json=payload).status_code == 400
    assert client.post(BASE + '/password-reset/complete', json=payload).status_code == 429


def test_expired_and_tampered_challenges(app, seed, monkeypatch):
    with app.app_context():
        user = User.query.filter_by(email='owner_a@test.com').one()
        challenge, code = agent_account.make_reset_challenge(user)
        assert not agent_account.complete_reset(challenge + 'x', code, 'new-password')
        import time
        now = time.time()
        monkeypatch.setattr(time, 'time', lambda: now + 901)
        assert not agent_account.complete_reset(challenge, code, 'new-password')


@pytest.mark.parametrize('path', ['/accounts', '/password-reset', '/password-reset/complete'])
def test_malformed_json_is_rejected(client, path):
    assert client.post(BASE + path, json=['bad']).status_code == 400
    assert client.post(BASE + path, json={'email': {'nested': True}}).status_code == 400
