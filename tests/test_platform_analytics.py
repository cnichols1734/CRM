"""Protect platform-only reporting, tenant counts and truthful activity dates."""
from datetime import datetime, timedelta

import pytest
from models import ActivationEvent, ChatConversation, ChatMessage, Organization, OrganizationMetrics, User, db
from services.platform_analytics import organization_activity, platform_overview


def test_org_reports_keep_first_source_and_exclude_automated_activity(app, seed):
    with app.app_context():
        now = datetime.utcnow()
        oid, other = seed['org_a'], seed['org_b']
        rows = [
            ActivationEvent(organization_id=oid, event='account_created', created_at=now-timedelta(days=3), event_data={'first_touch_utm_source': 'newsletter', 'first_touch_at': '2026-11-01T12:00:00+00:00'}),
            ActivationEvent(organization_id=oid, event='account_created', created_at=now-timedelta(days=2), event_data={'first_touch_utm_source': 'later-campaign'}),
            ActivationEvent(organization_id=oid, event='meaningful_action', created_at=now-timedelta(hours=2), event_data={'action': 'interaction_logged', 'private_note': 'must-not-render'}),
            ActivationEvent(organization_id=oid, event='retention_stage_changed', created_at=now, event_data={}),
            ActivationEvent(organization_id=other, event='meaningful_action', created_at=now-timedelta(hours=1), event_data={'action': 'csv_import_completed'}),
        ]
        db.session.add_all(rows)
        db.session.commit()
        try:
            report = organization_activity(oid, now)
            assert report['source']['label'] == 'newsletter'
            assert report['actions_30d'] == 1
            assert sum(day['count'] for day in report['days']) == 1
            assert len(report['days']) == 30
            assert report['last_activity'] == rows[2].created_at
            assert report['actions'] == [('Interactions logged', 1)]
            assert len(report['timeline']) == 3
            assert 'private_note' not in str(report)
            overview = platform_overview([oid, other], now)
            assert overview['actions'] == 2
            assert overview['captured'] == 1
            assert overview['orgs'][other]['source']['label'] == 'Not captured'
            assert platform_overview([], now)['sources'] == []
        finally:
            for row in rows:
                db.session.delete(row)
            db.session.commit()


@pytest.mark.parametrize('path,method', [('/platform/dashboard', 'get'), ('/platform/orgs/{oid}', 'get'), ('/platform/orgs/{oid}/refresh-metrics', 'post')])
def test_customer_admin_cannot_access_platform_reports(owner_a_client, seed, path, method):
    response = getattr(owner_a_client, method)(path.format(oid=seed['org_b']))
    assert response.status_code == 403


def test_platform_report_renders_aggregate_counts_without_message_content(app, seed, owner_a_client):
    with app.app_context():
        org = db.session.get(Organization, seed['org_a'])
        user = db.session.get(User, seed['owner_a'])
        previous = org.is_platform_admin, user.is_super_admin
        org.is_platform_admin, user.is_super_admin = True, True
        own = ChatConversation(organization_id=seed['org_b'], user_id=seed['owner_b'])
        foreign = ChatConversation(organization_id=seed['org_a'], user_id=seed['owner_a'])
        db.session.add_all([own, foreign])
        db.session.flush()
        db.session.add_all([
            ChatMessage(conversation_id=own.id, role='user', content='private-bob-question'),
            ChatMessage(conversation_id=own.id, role='assistant', content='private-bob-reply'),
            ChatMessage(conversation_id=foreign.id, role='user', content='foreign-private-question'),
        ])
        db.session.commit()
        try:
            response = owner_a_client.post(f"/platform/orgs/{seed['org_b']}/refresh-metrics")
            assert response.status_code == 302
            metrics = OrganizationMetrics.query.filter_by(organization_id=seed['org_b']).one()
            assert metrics.usage_summary['bob_user_messages'] == 1
            assert metrics.usage_summary['bob_replies'] == 1
            detail = owner_a_client.get(f"/platform/orgs/{seed['org_b']}")
            assert detail.status_code == 200
            html = detail.get_data(as_text=True)
            assert 'BOB messages sent' in html
            assert 'Meaningful activity' in html
            assert 'Not captured' in html
            assert 'private-bob' not in html
            dashboard = owner_a_client.get('/platform/dashboard')
            assert dashboard.status_code == 200
            assert 'Signup sources' in dashboard.get_data(as_text=True)
        finally:
            db.session.rollback()
            org.is_platform_admin, user.is_super_admin = previous
            for convo in (own, foreign):
                db.session.delete(convo)
            OrganizationMetrics.query.filter_by(organization_id=seed['org_b']).delete()
            db.session.commit()
