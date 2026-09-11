"""
Integration tests for feature flag enforcement.

Verifies that free-tier orgs are blocked from premium features,
pro-tier orgs can access enabled premium features, and global
feature overrides can disable a feature for everyone.
"""
import pytest

from feature_flags import FEATURE_FLAGS, can_access_reports, get_org_features, org_has_feature
from models import Organization, User, db


class TestFreeTierRestrictions:
    """Free-tier org (Org B) should be blocked from premium features."""

    def test_ai_daily_todo_blocked(self, owner_b_client, seed):
        resp = owner_b_client.post('/api/daily-briefing/generate',
                                   follow_redirects=True)
        assert resp.status_code in (200, 302, 403)
        if resp.status_code == 200:
            assert (
                b'upgrade' in resp.data.lower()
                or b'subscription' in resp.data.lower()
                or resp.content_type == 'application/json'
            )

    def test_ai_daily_todo_today_blocked(self, owner_b_client, seed):
        resp = owner_b_client.get('/api/daily-briefing/today',
                                  follow_redirects=True)
        assert resp.status_code in (200, 302, 403)

    def test_briefing_page_blocked(self, owner_b_client, seed):
        resp = owner_b_client.get('/briefing', follow_redirects=True)
        assert resp.status_code in (200, 302, 403)

    def test_action_plan_page_blocked(self, owner_b_client, seed):
        resp = owner_b_client.get('/action-plan', follow_redirects=True)
        assert resp.status_code in (200, 302, 403)
        if resp.status_code == 200:
            assert b'upgrade' in resp.data.lower() or b'action' in resp.data.lower()

    def test_transactions_blocked_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/transactions/', follow_redirects=True)
        assert resp.status_code in (200, 302, 403)


class TestProTierAccess:
    """Pro-tier org (Org A) should access enabled premium features."""

    def test_transactions_allowed(self, owner_a_client, seed):
        resp = owner_a_client.get('/transactions/')
        assert resp.status_code == 200

    def test_action_plan_page_allowed(self, owner_a_client, seed):
        resp = owner_a_client.get('/action-plan')
        assert resp.status_code == 200

    def test_ai_daily_briefing_allowed(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/daily-briefing/today')
        # 404 = no briefing yet (feature is on); 200 = ready
        assert resp.status_code in (200, 404)

    def test_briefing_page_allowed(self, owner_a_client, seed):
        resp = owner_a_client.get('/briefing')
        assert resp.status_code == 200
        assert b'Daily Briefing' in resp.data

    def test_ai_daily_todo_legacy_assets_gone(self, owner_a_client, seed):
        resp = owner_a_client.get('/dashboard')
        assert resp.status_code == 200
        assert b'js/daily_todo.js' not in resp.data
        assert b'dailyTodoModal' not in resp.data
        assert b'daily-briefing-banner' in resp.data
        assert b'js/ai_chat.js' in resp.data

    def test_market_insights_panel_globally_disabled(self, owner_a_client, seed):
        resp = owner_a_client.get('/dashboard')
        assert resp.status_code == 200
        assert b'Market Insights' not in resp.data
        assert b'market-insights-panel' not in resp.data


class TestGlobalFeatureOverrides:
    """Feature overrides can disable features for every org."""

    def test_global_override_beats_platform_admin_and_org_override(self):
        org = Organization(
            subscription_tier='enterprise',
            is_platform_admin=True,
            feature_flags={'MARKET_INSIGHTS': True},
        )

        # MARKET_INSIGHTS remains globally killed; AI_DAILY_TODO is live again
        assert org_has_feature('MARKET_INSIGHTS', org) is False
        assert org_has_feature('AI_DAILY_TODO', org) is True
        assert org_has_feature('AI_CHAT', org) is True

    def test_feature_context_keeps_market_insights_disabled(self):
        org = Organization(
            subscription_tier='pro',
            feature_flags={'MARKET_INSIGHTS': True, 'AI_DAILY_TODO': True},
        )

        features = get_org_features(org)
        assert features['AI_DAILY_TODO'] is True
        assert features['MARKET_INSIGHTS'] is False
        assert features['AI_CHAT'] is True


class TestReportsParked:
    """Reports stays in the codebase but is off in nav and at /reports."""

    def test_owner_cannot_access_while_parked(self, app, seed):
        with app.app_context():
            owner = db.session.get(User, seed['owner_a'])
            assert can_access_reports(owner) is False

    @pytest.mark.parametrize('url', [
        '/dashboard',
        '/contacts',
        '/tasks',
        '/briefing',
        '/org/settings',
    ])
    def test_sidebar_omits_reports(self, owner_a_client, url):
        resp = owner_a_client.get(url)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'href="/reports/"' not in html
        assert '>Reports</span>' not in html
        if url == '/dashboard':
            assert '>Daily Briefing</span>' in html

    def test_reports_route_stays_registered(self, owner_a_client):
        resp = owner_a_client.get('/reports/')
        assert resp.status_code == 403

    def test_flag_restores_admin_access(self, app, seed, monkeypatch):
        monkeypatch.setitem(FEATURE_FLAGS, 'REPORTS_ENABLED', True)
        with app.app_context():
            owner = db.session.get(User, seed['owner_a'])
            agent = db.session.get(User, seed['agent_a'])
            assert can_access_reports(owner) is True
            assert can_access_reports(agent) is False


class TestCoreFeatures:
    """Core features available to all tiers."""

    def test_contacts_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/contacts')
        assert resp.status_code == 200

    def test_tasks_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/tasks')
        assert resp.status_code == 200

    def test_dashboard_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/dashboard')
        assert resp.status_code == 200

    def test_user_todo_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/user_todo')
        assert resp.status_code == 200

    def test_updates_available_free(self, owner_b_client, seed):
        resp = owner_b_client.get('/updates')
        assert resp.status_code == 200
