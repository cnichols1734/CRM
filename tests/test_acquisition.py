"""First-visit retention, source privacy and cookie boundaries."""
from flask import Flask, redirect
from flask_login import LoginManager
import pytest

from services.acquisition import COOKIE_NAME, MAX_AGE, init_acquisition_tracking


@pytest.fixture
def visitor_app(monkeypatch):
    app = Flask(__name__)
    app.config.update(SECRET_KEY='acquisition-test', POSTHOG_ENABLED=True)
    login = LoginManager(app)
    login.user_loader(lambda _: None)
    app.add_url_rule('/', 'main.landing', lambda: 'Landing')
    app.add_url_rule('/register', 'auth.register', lambda: 'Register')
    app.add_url_rule('/api/agent/v1/me', 'agent_api.me', lambda: '{}')
    init_acquisition_tracking(app)
    captured = []
    monkeypatch.setattr(
        'services.product_analytics.capture_anonymous',
        lambda event, **kwargs: captured.append((event, kwargs)),
    )
    return app, captured


@pytest.mark.parametrize(('referrer', 'kind', 'domain'), [
    ('https://www.google.com/search?q=private+search', 'external', 'www.google.com'),
    ('https://agentflow.origentechnolog.com/', 'internal', 'agentflow.origentechnolog.com'),
    ('', 'direct_or_unknown', None),
    ('https://[broken', 'direct_or_unknown', None),
])
def test_referrals_keep_only_domain(visitor_app, referrer, kind, domain):
    app, captured = visitor_app
    client = app.test_client()
    response = client.get('/?utm_source=google&email=private@example.org',
                          headers={'Referer': referrer})
    props = captured[0][1]['properties']
    assert props['first_touch_referrer_kind'] == kind
    assert props.get('first_touch_referring_domain') == domain
    assert props['first_touch_landing_path'] == '/'
    assert 'private' not in str(props)
    assert response.cache_control.private and response.cache_control.no_store
    assert 'HttpOnly' in response.headers['Set-Cookie']


def test_rejects_sensitive_campaign_values(visitor_app):
    app, captured = visitor_app
    app.test_client().get('/', query_string={
        'utm_source': 'name@example.org', 'utm_medium': 'email',
        'utm_campaign': 'https://example.org/private',
        'utm_content': 'free form private text', 'ref': '555-123-4567',
    })
    props = captured[0][1]['properties']
    assert props['first_touch_utm_medium'] == 'email'
    assert not any(key in props for key in (
        'first_touch_utm_source', 'first_touch_utm_campaign',
        'first_touch_campaign_variant', 'first_touch_ref',
    ))


def test_direct_first_visit_survives_return_with_campaign(visitor_app, monkeypatch):
    import time
    app, captured = visitor_app
    client = app.test_client()
    client.get('/')
    cookie = client.get_cookie(COOKIE_NAME).value
    tomorrow = int(time.time()) + 24 * 60 * 60
    monkeypatch.setattr('itsdangerous.timed.TimestampSigner.get_timestamp', lambda _: tomorrow)
    client.get('/register?utm_source=later')
    assert client.get_cookie(COOKIE_NAME).value == cookie
    assert len(captured) == 1
    assert captured[0][1]['properties']['first_touch_referrer_kind'] == 'direct_or_unknown'


@pytest.mark.parametrize('invalid_cookie', ['tampered', 'expired'])
def test_invalid_cookie_cannot_preserve_old_source(visitor_app, monkeypatch, invalid_cookie):
    app, captured = visitor_app
    client = app.test_client()
    client.get('/?utm_source=old')
    if invalid_cookie == 'tampered':
        client.set_cookie(COOKIE_NAME, client.get_cookie(COOKIE_NAME).value + 'broken')
    else:
        import time
        now = int(time.time())
        monkeypatch.setattr('itsdangerous.timed.TimestampSigner.get_timestamp',
                            lambda _: now + MAX_AGE + 1)
    client.get('/register?utm_source=new')
    assert captured[-1][1]['properties']['first_touch_utm_source'] == 'new'
    assert captured[-1][1]['distinct_id'] != captured[0][1]['distinct_id']


def test_legacy_host_redirect_preserves_referral(visitor_app):
    app, captured = visitor_app
    from flask import request

    @app.before_request
    def legacy_redirect():
        if request.host == 'www.origentechnolog.com':
            return redirect('https://agentflow.origentechnolog.com/', code=301)

    client = app.test_client()
    response = client.get(
        '/?utm_source=partner', base_url='https://www.origentechnolog.com',
        headers={'Referer': 'https://example.org/guide'},
    )
    assert response.status_code == 301
    assert 'Domain=origentechnolog.com' in response.headers['Set-Cookie']
    assert 'Secure' in response.headers['Set-Cookie']
    client.get('/', base_url='https://agentflow.origentechnolog.com')
    client.get('/register', base_url='https://agentflow.origentechnolog.com')
    assert len(captured) == 1
    assert captured[0][1]['properties']['first_touch_utm_source'] == 'partner'


def test_no_tracking_on_api_head_or_disabled_analytics(visitor_app):
    app, captured = visitor_app
    client = app.test_client()
    for method, path in [('GET', '/api/agent/v1/me'), ('HEAD', '/')]:
        response = client.open(path, method=method)
        assert COOKIE_NAME not in response.headers.get('Set-Cookie', '')
    app.config['POSTHOG_ENABLED'] = False
    client.get('/?utm_source=disabled')
    assert not captured
    assert client.get_cookie(COOKIE_NAME) is None
