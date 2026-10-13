"""Workspace navigation, onboarding dismissal, and public contact delivery."""
import pytest


class TestDashboard:
    """Dashboard-specific tests."""

    def test_dashboard_dismiss_onboarding(self, owner_a_client, seed, app, db):
        from models import User
        with app.app_context():
            user = db.session.get(User, seed['owner_a'])
            original = user.has_seen_dashboard_onboarding
            user.has_seen_dashboard_onboarding = False
            db.session.commit()
        try:
            response = owner_a_client.post('/dashboard/dismiss-onboarding')
            assert response.status_code == 200
            with app.app_context():
                assert db.session.get(User, seed['owner_a']).has_seen_dashboard_onboarding is True
        finally:
            with app.app_context():
                db.session.get(User, seed['owner_a']).has_seen_dashboard_onboarding = original
                db.session.commit()


class TestContactUs:
    """Contact form (public)."""

    @pytest.mark.parametrize('sent,status', [(True, 200), (False, 500)])
    def test_contact_form_reports_delivery_outcome(self, client, monkeypatch, sent, status):
        from types import SimpleNamespace
        delivered = []
        def send_contact_form(**message):
            delivered.append(message)
            return sent
        monkeypatch.setattr('routes.contact_us.get_email_service',
                            lambda: SimpleNamespace(send_contact_form=send_contact_form))
        response = client.post('/contact-us', json={
            'subject': ' Test Question ', 'email': 'test@example.com',
            'message': ' Hello, I have a question. ',
        })
        assert response.status_code == status
        assert response.json['success'] is sent
        assert delivered == [{'subject': 'Test Question', 'user_email': 'test@example.com',
                              'message': 'Hello, I have a question.'}]

    def test_contact_form_validation(self, client, seed):
        resp = client.post('/contact-us', json={
            'subject': '',
            'email': '',
            'message': '',
        })
        assert resp.status_code == 400


@pytest.mark.parametrize('url', ['/dashboard', '/profile', '/groups', '/user_todo'])
def test_agent_workspace_pages_load(agent_a_client, url):
    assert agent_a_client.get(url).status_code == 200


def test_owner_todo_page_loads(owner_a_client):
    assert owner_a_client.get('/user_todo').status_code == 200
