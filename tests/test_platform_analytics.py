"""Protect platform-only reporting, tenant counts and truthful activity dates."""
from datetime import datetime, timedelta

import pytest
from models import ActivationEvent, ChatConversation, ChatMessage, Organization, OrganizationMetrics, User, db
from services.platform_analytics import organization_activity, platform_overview


@pytest.fixture
def reporting_orgs(app):
    with app.app_context():
        orgs = [Organization(name=f'Report test {n}', slug=f'report-test-{n}', status='active') for n in range(2)]
        db.session.add_all(orgs)
        db.session.flush()
        users = [User(organization_id=org.id, username=f'report-test-{n}', email=f'report-{n}@test.local', first_name='Report', last_name='Test', password_hash='unused') for n, org in enumerate(orgs)]
        db.session.add_all(users)
        db.session.commit()
        ids = [org.id for org in orgs]
        user_ids = [user.id for user in users]
    yield ids, user_ids
    with app.app_context():
        db.session.rollback()
        ActivationEvent.query.filter(ActivationEvent.organization_id.in_(ids)).delete(synchronize_session=False)
        OrganizationMetrics.query.filter(OrganizationMetrics.organization_id.in_(ids)).delete(synchronize_session=False)
        for convo in ChatConversation.query.filter(ChatConversation.organization_id.in_(ids)).all():
            db.session.delete(convo)
        db.session.flush()
        User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        Organization.query.filter(Organization.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def test_org_reports_keep_first_source_and_exclude_automated_activity(app, reporting_orgs):
    with app.app_context():
        now = datetime.utcnow()
        oid, other = reporting_orgs[0]
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


def test_platform_report_renders_aggregate_counts_without_message_content(app, seed, owner_a_client, reporting_orgs):
    with app.app_context():
        org = db.session.get(Organization, seed['org_a'])
        user = db.session.get(User, seed['owner_a'])
        previous = org.is_platform_admin, user.is_super_admin
        org.is_platform_admin, user.is_super_admin = True, True
        (target, other), (target_user, other_user) = reporting_orgs
        own = ChatConversation(organization_id=target, user_id=target_user)
        foreign = ChatConversation(organization_id=other, user_id=other_user)
        db.session.add_all([own, foreign])
        db.session.flush()
        db.session.add_all([
            ChatMessage(conversation_id=own.id, role='user', content='private-bob-question'),
            ChatMessage(conversation_id=own.id, role='assistant', content='private-bob-reply'),
            ChatMessage(conversation_id=foreign.id, role='user', content='foreign-private-question'),
        ])
        db.session.commit()
        try:
            response = owner_a_client.post(f"/platform/orgs/{target}/refresh-metrics")
            assert response.status_code == 302
            metrics = OrganizationMetrics.query.filter_by(organization_id=target).one()
            assert metrics.usage_summary['bob_user_messages'] == 1
            assert metrics.usage_summary['bob_replies'] == 1
            detail = owner_a_client.get(f"/platform/orgs/{target}")
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
            OrganizationMetrics.query.filter_by(organization_id=target).delete()
            db.session.commit()
