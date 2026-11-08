from models import AuditEvent, ClientBrowseAccount, ClientBrowseInquiry, PortalMessage, User, db
from tests.test_agent_api import _auth, _owner_token, _seller_thread, _agent_session

BASE = '/api/agent/v1'


def test_block_is_private_persistent_and_covers_linked_threads(app, client, seed):
    with app.app_context():
        tx, participant, _ = _seller_thread(seed)
        pid = participant.id
        other_tx, other, _ = _seller_thread(seed, 'Second thread')
        other_id = other.id
        account = ClientBrowseAccount(organization_id=seed['org_a'], email='safety@example.com', name='Safety client',
            password_hash='unused', contact_id=participant.contact_id, agent_id=seed['owner_a'])
        db.session.add(account)
        db.session.flush()
        inquiry = ClientBrowseInquiry(organization_id=seed['org_a'], account_id=account.id,
            agent_id=seed['owner_a'], request_id='safety-test', kind='general', body='Hidden inquiry')
        db.session.add(inquiry)
        for person in (participant, other):
            db.session.add(PortalMessage(organization_id=seed['org_a'], transaction_id=person.transaction_id,
                participant_id=person.id, sender='client', body='Hidden message'))
        db.session.commit()
        iid = inquiry.id
    headers = _auth(_owner_token(client))
    route = BASE + f'/conversations/{pid}/block'
    assert client.put(route, headers=headers, json={'blocked': 'yes'}).status_code == 400
    assert client.put(route, headers=headers, json={'blocked': True}).json == {'blocked': True}
    for thread in (pid, other_id):
        for suffix in ('', '/preview'):
            response = client.get(BASE + f'/conversations/{thread}/messages' + suffix, headers=headers)
            assert response.status_code == 200 and response.json['messages'] == []
        assert client.post(BASE + f'/conversations/{thread}/messages', headers=headers, json={'body': 'blocked reply'}).status_code == 403
    rows = client.get(BASE + '/conversations', headers=headers).json['conversations']
    assert all(r['blocked'] and r['unread'] == 0 and r['last_preview'] == 'Client blocked' for r in rows if r['participant_id'] in (pid, other_id))
    detail = client.get(BASE + f'/inquiries/{iid}', headers=headers).json['inquiry']
    assert detail['blocked'] and detail['messages'] == [] and detail['body'] == ''
    with app.app_context():
        from services.message_safety import is_blocked
        owner = db.session.get(User, seed['owner_a'])
        another = db.session.get(User, seed['agent_a'])
        person = db.session.get(type(participant), pid)
        assert is_blocked(owner, participant=person)
        assert not is_blocked(another, participant=person)
    other_token = _agent_session(client, email='owner_b@test.com').json['token']
    assert client.put(route, headers=_auth(other_token), json={'blocked': False}).status_code == 404
    # Unblocking from another linked conversation clears the same person's block.
    assert client.put(BASE + f'/inquiries/{iid}/block', headers=headers, json={'blocked': False}).json == {'blocked': False}
    assert client.get(BASE + f'/conversations/{pid}/messages', headers=headers).json['messages']


def test_report_scope_receipt_and_notification(app, client, seed, monkeypatch):
    notifications = []
    monkeypatch.setattr('services.sendgrid_outbound._send_html', lambda *args, **kwargs: notifications.append(args) or True)
    with app.app_context():
        _, participant, _ = _seller_thread(seed, 'Report thread')
        pid = participant.id
        db.session.commit()
    headers = _auth(_owner_token(client))
    report = {'kind': 'deal', 'subject_id': pid, 'reason': 'Harassment in this conversation'}
    response = client.post(BASE + '/content-reports', headers=headers, json=report)
    assert response.status_code == 201 and response.json['receipt'].startswith('AF-')
    assert notifications and 'Harassment' not in str(notifications)
    with app.app_context():
        row = db.session.get(AuditEvent, int(response.json['receipt'][3:]))
        assert row.organization_id == seed['org_a'] and row.actor_id == seed['owner_a']
        assert row.event_data['status'] == 'open' and row.event_data['notification_sent']
    other = _agent_session(client, email='owner_b@test.com').json['token']
    assert client.post(BASE + '/content-reports', headers=_auth(other), json=report).status_code == 404
    assert client.post(BASE + '/content-reports', headers=headers, json={**report, 'subject_id': True}).status_code == 400
    assert client.post(BASE + '/content-reports', headers=headers, json={**report, 'reason': 'x' * 2001}).status_code == 400
    assert client.post(BASE + '/content-reports', json=report).status_code == 401


def test_filter_masks_threats_without_sending_content_to_another_service():
    from services.message_safety import screened_text, screened_messages
    safe = "We can close on Friday."
    assert screened_text(safe) == safe
    threat = "I WILL KILL YOU"
    assert screened_text(threat).startswith("Message hidden")
    row = screened_messages([{'body': threat, 'attachments': [{'url': 'private'}], 'web_listing': {'url': 'private'}}])[0]
    assert row['attachments'] == [] and row['web_listing'] is None
    assert 'KILL' not in row['body']


def test_invitation_link_blocks_without_contact_reassignment_and_reuses_index(app, client, seed):
    from sqlalchemy import event
    from services.message_safety import is_blocked
    with app.app_context():
        _, participant, access = _seller_thread(seed, 'Invitation-only link')
        account = ClientBrowseAccount(organization_id=seed['org_a'], email='linked-safety@example.com',
            name='Linked client', password_hash='unused', contact_id=None, agent_id=seed['owner_a'],
            linked_access_ids=[access.id])
        db.session.add(account)
        db.session.flush()
        inquiry = ClientBrowseInquiry(organization_id=seed['org_a'], account_id=account.id,
            agent_id=seed['owner_a'], request_id='linked-safety', kind='general', body='Linked inquiry')
        db.session.add(inquiry)
        db.session.commit()
        aid, iid, pid = account.id, inquiry.id, participant.id
    headers = _auth(_owner_token(client))
    assert client.put(BASE + f'/inquiries/{iid}/block', headers=headers, json={'blocked': True}).json['blocked']
    assert client.get(BASE + f'/conversations/{pid}/messages', headers=headers).json['blocked']
    with app.app_context():
        owner = db.session.get(User, seed['owner_a'])
        account = db.session.get(ClientBrowseAccount, aid)
        from models import TransactionParticipant
        participant = db.session.get(TransactionParticipant, pid)
        assert account.contact_id is None
        queries = []
        def count(*args):
            queries.append(1)
        event.listen(db.engine, 'before_cursor_execute', count)
        try:
            for _ in range(100):
                assert is_blocked(owner, account=account)
                assert is_blocked(owner, participant=participant)
        finally:
            event.remove(db.engine, 'before_cursor_execute', count)
        assert len(queries) == 3
    assert client.put(BASE + f'/conversations/{pid}/block', headers=headers, json={'blocked': False}).json == {'blocked': False}
    assert not client.get(BASE + f'/inquiries/{iid}', headers=headers).json['inquiry'].get('blocked', False)


def test_inquiry_reply_and_preview_are_screened_before_truncation(app, client, seed):
    from models import ClientInquiryMessage
    with app.app_context():
        account = ClientBrowseAccount(organization_id=seed['org_a'], email='filtered-safety@example.com',
            name='Filtered client', password_hash='unused', agent_id=seed['owner_a'])
        db.session.add(account)
        db.session.flush()
        inquiry = ClientBrowseInquiry(organization_id=seed['org_a'], account_id=account.id,
            agent_id=seed['owner_a'], request_id='filtered-safety', kind='general', body='Hello')
        db.session.add(inquiry)
        db.session.flush()
        db.session.add(ClientInquiryMessage(organization_id=seed['org_a'], inquiry_id=inquiry.id,
            sender='agent', author_user_id=seed['owner_a'], body=('a' * 230) + ' I will kill you', request_id='filtered-message'))
        db.session.commit()
        iid = inquiry.id
    headers = _auth(_owner_token(client))
    detail = client.get(BASE + f'/inquiries/{iid}', headers=headers).json['inquiry']
    assert detail['reply'].startswith('Message hidden')
    row = next(r for r in client.get(BASE + '/inquiries', headers=headers).json['inquiries'] if r['id'] == iid)
    assert row['last_preview'].startswith('Message hidden')
