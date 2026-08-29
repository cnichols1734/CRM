"""GA4 head tag: present once, no live Google fetch under pytest."""
from pathlib import Path

from flask import render_template_string

from config import Config
from services.ga4 import hostname_from_host_header, should_load_gtag


ROOT = Path(__file__).resolve().parents[1]
GTAG_SRC = 'googletagmanager.com/gtag/js?id=G-H05PW4JXBN'
GTAG_CONFIG = "gtag('config', 'G-H05PW4JXBN')"
MARKER = 'name="ga4-measurement-id"'
DEFAULT_ID = 'G-H05PW4JXBN'

DOCUMENT_HEADS = (
    'templates/base.html',
    'templates/landing.html',
    'templates/free_real_estate_crm.html',
    'templates/follow_up_boss_alternative.html',
    'templates/kvcore_alternative.html',
    'templates/wise_agent_alternative.html',
    'templates/portal/base.html',
)


def _html(resp):
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


def _assert_test_safe_once(html):
    assert '</head>' in html
    head = html.split('</head>', 1)[0]
    assert head.count(MARKER) == 1
    assert f'content="{DEFAULT_ID}"' in head
    assert html.count(MARKER) == 1
    assert 'googletagmanager.com/gtag/js' not in html


class TestGa4Config:
    def test_production_default_is_agentflow_web_stream(self):
        assert Config.GA4_MEASUREMENT_ID == DEFAULT_ID


class TestGa4DocumentHeads:
    def test_user_facing_roots_include_the_partial_once(self):
        for rel in DOCUMENT_HEADS:
            text = (ROOT / rel).read_text()
            assert text.count('{% include "components/ga4.html" %}') == 1, rel

    def test_auth_templates_do_not_add_a_second_tag(self):
        auth_dir = ROOT / 'templates' / 'auth'
        for path in auth_dir.glob('*.html'):
            assert 'components/ga4.html' not in path.read_text(), path.name
            assert 'googletagmanager.com/gtag/js' not in path.read_text(), path.name


class TestGa4RenderedPages:
    def test_public_landing(self, client):
        _assert_test_safe_once(_html(client.get('/')))

    def test_login(self, client, seed):
        _assert_test_safe_once(_html(client.get('/login')))

    def test_authed_dashboard(self, owner_a_client, seed):
        _assert_test_safe_once(_html(owner_a_client.get('/dashboard')))

    def test_pytest_does_not_load_gtag_js(self, client, owner_a_client, seed):
        pages = (
            client.get('/'),
            client.get('/login'),
            owner_a_client.get('/dashboard'),
        )
        for resp in pages:
            html = _html(resp)
            assert 'googletagmanager.com' not in html
            assert MARKER in html


class TestGa4HostParsing:
    def test_ipv6_loopback_forms_are_local(self):
        assert hostname_from_host_header('::1') == '::1'
        assert hostname_from_host_header('[::1]') == '::1'
        assert hostname_from_host_header('[::1]:5011') == '::1'
        assert hostname_from_host_header('127.0.0.1:5011') == '127.0.0.1'
        assert hostname_from_host_header('localhost:5011') == 'localhost'

    def test_ipv6_loopback_does_not_load_gtag(self, app):
        previous = app.config['TESTING']
        app.config['TESTING'] = False
        try:
            for host in ('::1', '[::1]', '[::1]:5011'):
                with app.test_request_context('/', headers={'Host': host}):
                    assert should_load_gtag() is False, host
            with app.test_request_context('/', base_url='http://[::1]:5011'):
                assert should_load_gtag() is False
        finally:
            app.config['TESTING'] = previous


class TestGa4SnippetSwitch:
    def test_production_host_emits_standard_snippet_once(self, app):
        previous = app.config['TESTING']
        app.config['TESTING'] = False
        try:
            with app.test_request_context(
                '/',
                base_url='https://www.origentechnolog.com',
            ):
                html = render_template_string('{% include "components/ga4.html" %}')
        finally:
            app.config['TESTING'] = previous

        assert html.count(GTAG_SRC) == 1
        assert html.count(GTAG_CONFIG) == 1
        assert html.count("gtag('js', new Date())") == 1
        assert MARKER not in html
        assert 'GTM-' not in html

    def test_empty_id_emits_nothing(self, app):
        previous = app.config['GA4_MEASUREMENT_ID']
        app.config['GA4_MEASUREMENT_ID'] = ''
        try:
            with app.test_request_context('/'):
                html = render_template_string('{% include "components/ga4.html" %}')
        finally:
            app.config['GA4_MEASUREMENT_ID'] = previous

        assert not html.strip()
        assert 'googletagmanager.com' not in html
        assert MARKER not in html
