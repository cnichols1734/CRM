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
    @pytest.mark.parametrize('method, path, destination', [
        ('post', '/api/daily-briefing/generate', None),
        ('get', '/api/daily-briefing/today', None),
        ('get', '/briefing', '/org/upgrade'),
        ('get', '/action-plan', '/org/upgrade'),
        ('get', '/transactions/', '/dashboard'),
    ])
    def test_premium_route_rejects_free_tier(self, owner_b_client, method, path, destination):
        response = getattr(owner_b_client, method)(path)
        if destination:
            assert response.status_code == 302
            assert response.location.endswith(destination)
        else:
            assert response.status_code == 403
            assert response.json == {'error': 'Daily Briefing requires a Pro subscription.', 'upgrade': True}


class TestProTierAccess:
    """Pro-tier org (Org A) should access enabled premium features."""

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

    def test_shared_sidebar_omits_reports(self, owner_a_client):
        response = owner_a_client.get('/dashboard')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'href="/reports/"' not in html
        assert '>Reports</span>' not in html
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

    @pytest.mark.parametrize('tier, overrides, platform_admin, available', [
        ('free', {}, False, False),
        ('pro', {}, False, True),
        ('enterprise', {}, False, True),
        ('free', {'TEAM_UPDATES': True}, False, True),
        ('free', {}, True, True),
    ])
    def test_updates_shows_upgrade_only_without_access(
        self, owner_b_client, seed, app, tier, overrides, platform_admin, available
    ):
        with app.app_context():
            org = db.session.get(Organization, seed['org_b'])
            original = org.subscription_tier, org.feature_flags, org.is_platform_admin
            org.subscription_tier, org.feature_flags, org.is_platform_admin = tier, overrides, platform_admin
            db.session.commit()
        try:
            resp = owner_b_client.get('/updates')
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert ('Company Updates is available on paid plans.' in html) is not available
            assert ('href="/updates/new"' in html) is available
            if not available:
                assert 'href="/org/upgrade"' in html
                assert 'Explore Pro' in html
                assert 'No updates yet' not in html
                assert owner_b_client.get('/org/upgrade').status_code == 200
        finally:
            with app.app_context():
                org = db.session.get(Organization, seed['org_b'])
                org.subscription_tier, org.feature_flags, org.is_platform_admin = original
                db.session.commit()
