from tests.test_client_discovery import enable_discovery, signup, auth, inquiry_body, BASE
from tests.test_client_portal_api import _seller_tx, _participant, _grant, _open_session
from models import db, ClientBrowseInquiry, ClientPortalAccess

def test_two_agents_link_then_switch(app, seed, client, agent_a_client):
    with app.app_context():
        first = _seller_tx(seed, 'Seller fixture')
        second = _seller_tx(seed, 'Buyer fixture')
        second.created_by_id = seed['agent_a']
        one = _grant(seed, first, _participant(seed, first, name='Client', email='client@example.test'))
        two = _grant(seed, second, _participant(seed, second, role='buyer', name='Client', email='client@example.test'))
        db.session.commit()
        codes=[one.invite_code,two.invite_code]; pids=[one.participant_id,two.participant_id]; gid=two.id
    headers=auth(signup(client, agent_id=seed['owner_a']))
    tokens=[_open_session(client,c).json['token'] for c in codes]
    assert client.post(BASE+'/connection',headers=headers,json={'deal_token':tokens[0]}).status_code==200
    created = client.post(BASE+'/inquiries',headers=headers,json=inquiry_body())
    assert created.status_code == 201
    inquiry_id = created.json['id']
    assert client.post(BASE+'/connection',headers=headers,json={'deal_token':tokens[1]}).status_code==200
    assert {r['id'] for r in client.get(BASE+'/deals',headers=headers).json['deals']}==set(pids)
    assert agent_a_client.get(f'/messages?thread=inquiry-{inquiry_id}').status_code == 404
    for token in (tokens[0], tokens[1], tokens[0]):
        assert client.post(BASE+'/connection', headers=headers, json={'deal_token': token}).status_code == 200
    with app.app_context():
        assert ClientBrowseInquiry.query.one().agent_id==seed['owner_a']
    selected=client.post(BASE+f'/deals/{pids[0]}/session',headers=headers).json['deal_session']['token']
    assert client.get(BASE+'/account',headers=headers).json['account']['agent_id']==seed['owner_a']
    first_headers={'Authorization':'Bearer '+selected}
    assert client.get('/api/client/v1/deal',headers=first_headers).json['agent']['name']=='Alice Owner'
    assert client.post('/api/client/v1/messages',headers=first_headers,json={'body':'Only first deal'}).status_code==201
    second_headers={'Authorization':'Bearer '+tokens[1]}
    assert all(m['body']!='Only first deal' for m in client.get('/api/client/v1/messages',headers=second_headers).json['messages'])
    with app.app_context():
        db.session.get(ClientPortalAccess,gid).is_active=False;db.session.commit()
    assert [r['id'] for r in client.get(BASE+'/deals',headers=headers).json['deals']]==[pids[0]]
    assert client.get('/api/client/v1/deal',headers=second_headers).status_code==401
