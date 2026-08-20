"""MCP connector: OAuth, protocol shape, scopes, isolation, and kill switches.

MCP Inspector checklist (local, before Cowork):

1. Run the app on port 5011 with a local SQLite DATABASE_URL.
2. Open MCP Inspector against http://127.0.0.1:5011/mcp.
3. Confirm GET /mcp returns 405 (no SSE hang).
4. Complete OAuth: DCR → authorize in the browser → PKCE token.
5. initialize negotiates 2025-06-18 or 2025-11-25.
6. tools/list includes whoami and hides inspect_attachment.
7. A read-only connector URL lists no write tools.
8. Revoke from /integrations/mcp and confirm the next tools/call is 401.

Run with: .venv/bin/python -m pytest tests/test_mcp.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import login
from models import (
    McpUserGrant, Organization, Transaction, TransactionAssignment,
    TransactionRequirement, db,
)
from services.bob_tools import CONFIRM_PRECLEARED, BobContext, dispatch
from services.mcp.crypto import new_secret, s256_challenge
from services.mcp.rate_limit import reset_register_limits


def _pkce():
    verifier = new_secret(32)
    return verifier, s256_challenge(verifier)


def _register(client, name='Claude', uris=None):
    reset_register_limits()
    resp = client.post('/oauth/register', json={
        'client_name': name,
        'redirect_uris': uris or ['https://claude.ai/api/mcp/auth_callback'],
    })
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return resp.get_json()


def _authorize(client, *, client_id, challenge, scopes='read write offline_access',
               resource=None, redirect_uri='https://claude.ai/api/mcp/auth_callback'):
    query = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': scopes,
        'state': 'st',
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    if resource:
        query['resource'] = resource
    page = client.get('/oauth/authorize', query_string=query)
    assert page.status_code == 200, page.get_data(as_text=True)
    with client.session_transaction() as sess:
        pending = sess['mcp_oauth_pending']
        csrf = pending['csrf']
    approved = client.post('/oauth/authorize', data={
        'csrf': csrf,
        'decision': 'approve',
        'scope_read': '1',
        **({'scope_write': '1'} if 'write' in scopes.split() else {}),
        **({'scope_destructive': '1'} if 'destructive' in scopes.split() else {}),
    }, follow_redirects=False)
    assert approved.status_code in (302, 303)
    location = approved.headers['Location']
    assert 'code=' in location
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(location).query)['code'][0]


def _token(client, *, client_id, code, verifier,
           redirect_uri='https://claude.ai/api/mcp/auth_callback', resource=None):
    data = {
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'code': code,
        'redirect_uri': redirect_uri,
        'code_verifier': verifier,
    }
    if resource:
        data['resource'] = resource
    resp = client.post('/oauth/token', data=data)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _connect(client, username, **kwargs):
    login(client, username)
    verifier, challenge = _pkce()
    registered = _register(client, name=kwargs.pop('name', f'Test {username} {new_secret(4)}'))
    code = _authorize(
        client,
        client_id=registered['client_id'],
        challenge=challenge,
        **kwargs,
    )
    tokens = _token(client, client_id=registered['client_id'], code=code, verifier=verifier)
    return registered, tokens


def _rpc(client, tokens, method, params=None, path='/mcp'):
    return client.post(
        path,
        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}},
        headers={'Authorization': f'Bearer {tokens["access_token"]}'},
    )


@pytest.fixture()
def anon_client(app):
    return app.test_client()


class TestOAuth:
    def test_pkce_success_and_metadata(self, owner_a_client, anon_client, seed):
        meta = anon_client.get('/.well-known/oauth-authorization-server')
        assert meta.status_code == 200
        body = meta.get_json()
        assert 'S256' in body['code_challenge_methods_supported']
        assert 'none' in body['token_endpoint_auth_methods_supported']

        registered, tokens = _connect(owner_a_client, 'owner_a')
        assert tokens['token_type'] == 'Bearer'
        assert 'refresh_token' in tokens

        who = _rpc(owner_a_client, tokens, 'tools/call', {'name': 'whoami'})
        assert who.status_code == 200
        payload = who.get_json()['result']['structuredContent']
        assert payload['email'] == 'owner_a@test.com'
        assert payload['organization_id'] == seed['org_a']

    def test_pkce_failure_burns_code(self, owner_a_client):
        login(owner_a_client, 'owner_a')
        verifier, challenge = _pkce()
        registered = _register(owner_a_client, name=f'pkce-fail {new_secret(4)}')
        code = _authorize(owner_a_client, client_id=registered['client_id'], challenge=challenge)
        bad = owner_a_client.post('/oauth/token', data={
            'grant_type': 'authorization_code',
            'client_id': registered['client_id'],
            'code': code,
            'redirect_uri': 'https://claude.ai/api/mcp/auth_callback',
            'code_verifier': 'wrong-verifier-value-xxxxxxxx',
        })
        assert bad.status_code == 400
        assert bad.get_json()['error'] == 'invalid_grant'
        retry = owner_a_client.post('/oauth/token', data={
            'grant_type': 'authorization_code',
            'client_id': registered['client_id'],
            'code': code,
            'redirect_uri': 'https://claude.ai/api/mcp/auth_callback',
            'code_verifier': verifier,
        })
        assert retry.status_code == 400

    def test_redirect_must_match(self, owner_a_client):
        login(owner_a_client, 'owner_a')
        registered = _register(owner_a_client, name=f'redir {new_secret(4)}')
        page = owner_a_client.get('/oauth/authorize', query_string={
            'response_type': 'code',
            'client_id': registered['client_id'],
            'redirect_uri': 'https://evil.example/callback',
            'scope': 'read',
            'code_challenge': 'abc',
            'code_challenge_method': 'S256',
        })
        assert page.status_code == 400

    def test_dcr_dedupe_and_rejects_wildcard(self, anon_client):
        reset_register_limits()
        first = _register(anon_client, name='Same Client', uris=['https://claude.ai/api/mcp/auth_callback'])
        second = _register(anon_client, name='Same Client', uris=['https://claude.ai/api/mcp/auth_callback'])
        assert first['client_id'] == second['client_id']
        wild = anon_client.post('/oauth/register', json={
            'client_name': 'Wild',
            'redirect_uris': ['https://example.com/*'],
        })
        assert wild.status_code == 400

    def test_dcr_accepts_cursor_desktop_bundle(self, anon_client):
        reset_register_limits()
        cursor = anon_client.post('/oauth/register', json={
            'client_name': 'Cursor',
            'redirect_uris': [
                'cursor://anysphere.cursor-mcp/oauth/callback',
                'https://www.cursor.com/agents/mcp/oauth/callback',
                'http://localhost:8787/callback',
            ],
        })
        assert cursor.status_code in (200, 201), cursor.get_data(as_text=True)
        named = anon_client.post('/oauth/register', json={
            'client_name': 'Cursor Named',
            'redirect_uris': ['cursor://anysphere.cursor-mcp/oauth/agentflow/callback'],
        })
        assert named.status_code in (200, 201), named.get_data(as_text=True)
        spoofed = anon_client.post('/oauth/register', json={
            'client_name': 'Spoofed Cursor',
            'redirect_uris': ['cursor://evil.example/oauth/callback'],
        })
        assert spoofed.status_code == 400
        remote_http = anon_client.post('/oauth/register', json={
            'client_name': 'Remote HTTP',
            'redirect_uris': ['http://example.com/callback'],
        })
        assert remote_http.status_code == 400

    def test_dcr_rate_limit(self, anon_client):
        reset_register_limits()
        last = None
        for i in range(21):
            last = anon_client.post('/oauth/register', json={
                'client_name': f'rate {i}',
                'redirect_uris': ['https://claude.ai/api/mcp/auth_callback'],
            })
        assert last.status_code == 429

    def test_audience_reject_and_readonly_metadata(self, owner_a_client, anon_client):
        readonly_meta = anon_client.get('/.well-known/oauth-protected-resource/mcp/readonly')
        assert readonly_meta.status_code == 200
        assert readonly_meta.get_json()['resource'].endswith('/mcp/readonly')

        _, tokens = _connect(owner_a_client, 'owner_a', name=f'aud {new_secret(4)}')
        crossed = owner_a_client.post(
            '/mcp/readonly',
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            headers={'Authorization': f'Bearer {tokens["access_token"]}'},
        )
        assert crossed.status_code == 401
        assert 'resource_metadata' in crossed.headers.get('WWW-Authenticate', '')

    def test_refresh_rotation_and_revoked_grant(self, owner_a_client, app, seed):
        registered, tokens = _connect(owner_a_client, 'owner_a', name=f'rot {new_secret(4)}')
        first = owner_a_client.post('/oauth/token', data={
            'grant_type': 'refresh_token',
            'client_id': registered['client_id'],
            'refresh_token': tokens['refresh_token'],
        })
        assert first.status_code == 200
        rotated = first.get_json()
        replay = owner_a_client.post('/oauth/token', data={
            'grant_type': 'refresh_token',
            'client_id': registered['client_id'],
            'refresh_token': tokens['refresh_token'],
        })
        assert replay.status_code == 400
        assert replay.get_json()['error'] == 'invalid_grant'

        with app.app_context():
            grant = McpUserGrant.query.filter_by(
                user_id=seed['owner_a'], client_id=registered['client_id'],
            ).first()
            from services.mcp.access import revoke_grant
            revoke_grant(grant, actor_id=seed['owner_a'])
        dead = owner_a_client.post('/oauth/token', data={
            'grant_type': 'refresh_token',
            'client_id': registered['client_id'],
            'refresh_token': rotated['refresh_token'],
        })
        assert dead.status_code == 400
        assert dead.get_json()['error'] == 'invalid_grant'


class TestProtocol:
    def test_get_is_405(self, anon_client):
        resp = anon_client.get('/mcp')
        assert resp.status_code == 405
        assert 'POST' in resp.headers.get('Allow', '')

    def test_missing_token_is_401(self, anon_client):
        resp = anon_client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})
        assert resp.status_code == 401
        assert 'resource_metadata' in resp.headers.get('WWW-Authenticate', '')

    def test_notification_is_202_empty(self, owner_a_client):
        _, tokens = _connect(owner_a_client, 'owner_a', name=f'note {new_secret(4)}')
        resp = owner_a_client.post(
            '/mcp',
            json={'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            headers={'Authorization': f'Bearer {tokens["access_token"]}'},
        )
        assert resp.status_code == 202
        assert resp.get_data() == b''

    def test_unknown_version_negotiates(self, owner_a_client):
        _, tokens = _connect(owner_a_client, 'owner_a', name=f'ver {new_secret(4)}')
        resp = _rpc(owner_a_client, tokens, 'initialize', {'protocolVersion': '1999-01-01'})
        assert resp.status_code == 200
        assert resp.get_json()['result']['protocolVersion'] == '2025-11-25'
        instructions = resp.get_json()['result']['instructions']
        assert 'Never invent' in instructions
        assert 'How the records fit together' in instructions
        assert 'When they say X, use Y' in instructions
        assert 'This connection is stateless' in instructions

    def test_mcp_catalog_does_not_promise_in_app_approval(self, owner_a_client):
        _, tokens = _connect(owner_a_client, 'owner_a', name=f'cat {new_secret(4)}')
        listed = _rpc(owner_a_client, tokens, 'tools/list')
        by_name = {tool['name']: tool['description'] for tool in listed.get_json()['result']['tools']}
        assert 'waiting for approval' not in by_name['update_contact']
        assert 'Applies immediately on this connector' in by_name['update_contact']
        assert 'Applies immediately on this connector' in by_name['update_task']
        assert "needs the agent's approval" not in by_name['append_contact_note']


class TestScopesAndIsolation:
    def test_read_cannot_create_contact(self, owner_a_client):
        _, tokens = _connect(
            owner_a_client, 'owner_a',
            name=f'read {new_secret(4)}',
            scopes='read offline_access',
        )
        listed = _rpc(owner_a_client, tokens, 'tools/list')
        names = {tool['name'] for tool in listed.get_json()['result']['tools']}
        assert 'search_contacts' in names
        assert 'create_contact' not in names
        called = _rpc(owner_a_client, tokens, 'tools/call', {
            'name': 'create_contact',
            'arguments': {'first_name': 'Nope', 'last_name': 'Nope'},
        })
        assert called.get_json()['result']['isError'] is True

    def test_write_cannot_delete_without_destructive(self, owner_a_client):
        _, tokens = _connect(
            owner_a_client, 'owner_a',
            name=f'write {new_secret(4)}',
            scopes='read write offline_access',
        )
        listed = _rpc(owner_a_client, tokens, 'tools/list')
        names = {tool['name'] for tool in listed.get_json()['result']['tools']}
        assert 'delete_contact' not in names
        called = _rpc(owner_a_client, tokens, 'tools/call', {
            'name': 'delete_contact',
            'arguments': {'contact_id': 1},
        })
        assert called.get_json()['result']['isError'] is True

    def test_readonly_url_has_no_write_tools(self, owner_a_client):
        login(owner_a_client, 'owner_a')
        verifier, challenge = _pkce()
        registered = _register(owner_a_client, name=f'ro {new_secret(4)}')
        resource = 'http://localhost/mcp/readonly'
        code = _authorize(
            owner_a_client,
            client_id=registered['client_id'],
            challenge=challenge,
            scopes='read offline_access',
            resource=resource,
        )
        tokens = _token(owner_a_client, client_id=registered['client_id'], code=code, verifier=verifier)
        listed = _rpc(owner_a_client, tokens, 'tools/list', path='/mcp/readonly')
        assert listed.status_code == 200
        names = {tool['name'] for tool in listed.get_json()['result']['tools']}
        assert 'search_contacts' in names
        assert 'create_contact' not in names
        assert 'delete_contact' not in names

    def test_org_a_cannot_read_org_b_contact(self, owner_a_client, seed):
        _, tokens = _connect(owner_a_client, 'owner_a', name=f'iso {new_secret(4)}')
        called = _rpc(owner_a_client, tokens, 'tools/call', {
            'name': 'get_contact',
            'arguments': {'contact_id': seed['contact_b']},
        })
        body = called.get_json()['result']
        assert body['isError'] is True

    def test_agent_cannot_search_whole_org(self, agent_a_client, seed):
        _, tokens = _connect(agent_a_client, 'agent_a', name=f'agent {new_secret(4)}')
        called = _rpc(agent_a_client, tokens, 'tools/call', {
            'name': 'search_contacts',
            'arguments': {'query': 'Jane', 'scope': 'organization'},
        })
        payload = called.get_json()['result']['structuredContent']
        ids = [row.get('contact_id') for row in payload.get('contacts') or payload.get('results') or []]
        assert seed['contact_a'] not in ids

    def test_collaborator_cannot_edit(self, app, seed, agent_a_client):
        with app.app_context():
            assignment = TransactionAssignment(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                user_id=seed['agent_a'],
                role='collaborator',
                capabilities=['view'],
            )
            db.session.add(assignment)
            db.session.commit()
            assignment_id = assignment.id
        try:
            _, tokens = _connect(agent_a_client, 'agent_a', name=f'collab {new_secret(4)}')
            viewed = _rpc(agent_a_client, tokens, 'tools/call', {
                'name': 'get_transaction_summary',
                'arguments': {'transaction_id': seed['tx_a']},
            })
            assert viewed.get_json()['result']['isError'] is False
            edited = _rpc(agent_a_client, tokens, 'tools/call', {
                'name': 'add_transaction_note',
                'arguments': {'transaction_id': seed['tx_a'], 'note': 'should fail'},
            })
            assert edited.get_json()['result']['isError'] is True
        finally:
            with app.app_context():
                row = db.session.get(TransactionAssignment, assignment_id)
                if row is not None:
                    db.session.delete(row)
                    db.session.commit()

    def test_free_org_hides_deal_tools(self, owner_b_client):
        _, tokens = _connect(owner_b_client, 'owner_b', name=f'free {new_secret(4)}')
        listed = _rpc(owner_b_client, tokens, 'tools/list')
        names = {tool['name'] for tool in listed.get_json()['result']['tools']}
        assert 'search_transactions' not in names
        assert 'create_transaction' not in names


class TestSettingsPages:
    def test_profile_settings_page(self, owner_a_client):
        resp = owner_a_client.get('/integrations/mcp')
        assert resp.status_code == 200
        assert b'Connector URLs' in resp.data
        assert b'/mcp/readonly' in resp.data

    def test_org_owner_can_save_mcp_controls(self, owner_a_client, app, seed):
        resp = owner_a_client.post('/org/settings/mcp', data={
            'mcp_enabled': '1',
            'mcp_admin_only': '1',
        }, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            org = db.session.get(Organization, seed['org_a'])
            assert org.mcp_admin_only is True
            org.mcp_admin_only = False
            flags = dict(org.feature_flags or {})
            flags.pop('MCP_CONNECTOR', None)
            org.feature_flags = flags
            db.session.commit()


class TestKillSwitch:
    def test_org_disable_rejects_live_token(self, app, seed, owner_a_client):
        _, tokens = _connect(owner_a_client, 'owner_a', name=f'kill {new_secret(4)}')
        with app.app_context():
            org = db.session.get(Organization, seed['org_a'])
            previous = dict(org.feature_flags or {})
            org.feature_flags = {**previous, 'MCP_CONNECTOR': False}
            db.session.commit()
        try:
            ping = _rpc(owner_a_client, tokens, 'ping')
            assert ping.status_code == 401
        finally:
            with app.app_context():
                org = db.session.get(Organization, seed['org_a'])
                org.feature_flags = previous
                db.session.commit()

    def test_global_override_blocks_everyone(self, owner_a_client, monkeypatch):
        import feature_flags
        monkeypatch.setitem(feature_flags.GLOBAL_FEATURE_OVERRIDES, 'MCP_CONNECTOR', False)
        login(owner_a_client, 'owner_a')
        verifier, challenge = _pkce()
        registered = _register(owner_a_client, name=f'glob {new_secret(4)}')
        page = owner_a_client.get('/oauth/authorize', query_string={
            'response_type': 'code',
            'client_id': registered['client_id'],
            'redirect_uri': 'https://claude.ai/api/mcp/auth_callback',
            'scope': 'read',
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
        })
        assert b'turned off' in page.get_data() or b'MCP' in page.get_data()


class TestGapTools:
    def test_create_transaction_and_listing_round_trip(self, app, seed):
        ctx = BobContext(
            user_id=seed['owner_a'], organization_id=seed['org_a'],
            org_role='owner', is_org_admin=True, surface='mcp',
        )
        with app.app_context():
            created = dispatch('create_transaction', {
                'transaction_type': 'seller',
                'street_address': '501 MCP Lane',
                'city': 'Austin',
                'state': 'TX',
                'contact_id': seed['contact_a'],
            }, ctx, confirmation=CONFIRM_PRECLEARED)
            assert created.ok, created.error
            tx_id = created.data['transaction_id']
            listed = dispatch('list_listings', {'query': 'MCP Lane'}, ctx)
            assert listed.ok
            assert any(row['transaction_id'] == tx_id for row in listed.data['listings'])
            updated = dispatch('update_listing_fields', {
                'transaction_id': tx_id, 'list_price': 450000, 'mls_number': 'MCP-1',
            }, ctx, confirmation=CONFIRM_PRECLEARED)
            assert updated.ok
            offer = dispatch('create_offer', {
                'transaction_id': tx_id,
                'buyer_names': 'Casey Buyer',
                'offer_price': 440000,
            }, ctx, confirmation=CONFIRM_PRECLEARED)
            assert offer.ok
            reviewed = dispatch('review_offer', {'offer_id': offer.data['offer_id']}, ctx)
            assert reviewed.ok
            briefing = dispatch('get_daily_briefing', {}, ctx)
            assert briefing.ok
            draft = dispatch('draft_email', {
                'contact_id': seed['contact_a'],
                'subject': 'Hello',
                'body': 'Just a draft',
            }, ctx)
            assert not draft.ok
            db.session.delete(db.session.get(Transaction, tx_id))
            db.session.commit()

    def test_requirement_status(self, app, seed):
        ctx = BobContext(
            user_id=seed['owner_a'], organization_id=seed['org_a'],
            org_role='owner', is_org_admin=True, surface='mcp',
        )
        with app.app_context():
            req = TransactionRequirement(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                package_key='mcp',
                phase_key='test',
                requirement_key='mcp_test_req',
                title='MCP test requirement',
                work_status='pending',
            )
            db.session.add(req)
            db.session.commit()
            req_id = req.id
            result = dispatch('complete_requirement', {'requirement_id': req_id}, ctx)
            assert result.ok, result.error
            fresh = db.session.get(TransactionRequirement, req_id)
            assert fresh.work_status == 'completed'
            db.session.delete(fresh)
            db.session.commit()
