"""
Integration tests for JSON API endpoints.

Covers: AI chat, action plan, daily todo, and miscellaneous
API endpoints. Tests both access control and basic response formats.
"""


class TestAIChatAPI:
    """AI Chat (B.O.B.) API endpoints."""

    def test_conversation_creation_listing_and_session_clear(self, owner_a_client, agent_a_client, app, db, seed):
        from models import ChatConversation
        with owner_a_client.session_transaction() as session:
            session['chat_history'] = [{'role': 'user', 'content': 'Old question'}]
        response = owner_a_client.post('/api/ai-chat/conversations', json={})
        assert response.status_code == 201
        conversation_id = response.json['id']
        try:
            with app.app_context():
                conversation = db.session.get(ChatConversation, conversation_id)
                assert (conversation.user_id, conversation.organization_id) == (seed['owner_a'], seed['org_a'])
            own = owner_a_client.get('/api/ai-chat/conversations').json['conversations']
            assert conversation_id in [row['id'] for row in own]
            other = agent_a_client.get('/api/ai-chat/conversations').json['conversations']
            assert conversation_id not in [row['id'] for row in other]
            with owner_a_client.session_transaction() as session:
                assert session['chat_history'] == []
                session['chat_history'] = [{'role': 'user', 'content': 'New question'}]
            assert owner_a_client.post('/api/ai-chat/clear').status_code == 200
            with owner_a_client.session_transaction() as session:
                assert not session.get('chat_history')
            with app.app_context():
                assert db.session.get(ChatConversation, conversation_id) is not None
        finally:
            with app.app_context():
                db.session.delete(db.session.get(ChatConversation, conversation_id))
                db.session.commit()

    def test_contact_mentions_only_return_matching_owned_contacts(self, owner_a_client, seed, app, db):
        from models import Contact
        with app.app_context():
            contacts = [Contact(organization_id=seed[org], user_id=seed[user],
                                first_name='MentionAudit', last_name=last,
                                email=f'mention-{last}@test.com')
                        for user, org, last in [('owner_a', 'org_a', 'Own'),
                                               ('agent_a', 'org_a', 'Teammate'),
                                               ('owner_b', 'org_b', 'Foreign')]]
            db.session.add_all(contacts)
            db.session.commit()
            ids = [contact.id for contact in contacts]
        try:
            response = owner_a_client.get('/api/ai-chat/search-contacts?q=MentionAudit')
            assert response.status_code == 200
            assert response.json == [{'id': ids[0], 'name': 'MentionAudit Own', 'email': 'mention-Own@test.com'}]
            assert owner_a_client.get('/api/ai-chat/search-contacts?q=MentionNoMatch').json == []
        finally:
            with app.app_context():
                Contact.query.filter(Contact.id.in_(ids)).delete(synchronize_session=False)
                db.session.commit()

    def test_chat_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.post('/api/ai-chat', json={'message': 'Hello'})
        assert resp.status_code in (302, 401, 403)


class TestActionPlanAPI:
    """Action plan API endpoints."""

    def test_get_action_plan(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/action-plan/get')
        assert resp.status_code == 200

    def test_action_plan_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.get('/action-plan')
        assert resp.status_code in (302, 401, 403)


class TestDailyTodoAPI:
    """Daily Briefing API endpoints."""

    def test_get_today_briefing_when_missing(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/daily-briefing/today')
        assert resp.status_code == 404
        assert resp.json == {'error': 'No briefing for today', 'status': 'missing'}

    def test_briefing_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.get('/api/daily-briefing/today')
        assert resp.status_code in (302, 401, 403)

    def test_legacy_latest_endpoint_still_gated(self, owner_a_client, seed):
        resp = owner_a_client.get('/api/daily-todo/latest')
        assert resp.status_code == 404
        assert resp.json == {'error': 'No todo list found'}


class TestTaskWindowAPI:
    """Task window preference API."""

    def test_update_valid_window(self, owner_a_client, seed, app, db):
        from models import User
        with app.app_context():
            original = db.session.get(User, seed['owner_a']).task_window_days
        try:
            response = owner_a_client.post('/api/update-task-window', json={'days': 30})
            assert response.status_code == 200
            assert response.json == {'success': True, 'days': 30}
            with app.app_context():
                assert db.session.get(User, seed['owner_a']).task_window_days == 30
        finally:
            with app.app_context():
                db.session.get(User, seed['owner_a']).task_window_days = original
                db.session.commit()

    def test_update_window_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.post(
            '/api/update-task-window',
            json={'days': 30},
            content_type='application/json',
        )
        assert resp.status_code in (302, 401, 403)


class TestResourcesAPI:
    """Agent resources API."""

    def test_resources_unauthenticated(self, client, seed):
        client.get('/logout')
        resp = client.get('/api/resources')
        assert resp.status_code in (302, 401, 403)


class TestHealthAPI:
    """Health check endpoints."""

    def test_health_json(self, client, seed):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'status' in data
        assert data['status'] == 'healthy'
