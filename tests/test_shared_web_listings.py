import uuid

import pytest

from models import db, ClientBrowseInquiry
from tests.test_client_discovery import BASE, signup, auth, enable_discovery
from tests.test_agent_api import _agent_session, _auth

LINK = 'https://www.zillow.com/homedetails/2442-W-Berenice-Ave-Chicago-IL-60618/463198300_zpid/'
PHOTO = 'https://photos.zillowstatic.com/fp/7ea07716b8c756947778cf1f24fa25a1-cc_ft_1536.jpg'
HOME = dict(url=LINK, address='2442 W Berenice Ave, Chicago, IL 60618', price='$899,000', photo_url=PHOTO)


def inquiry(home):
    return dict(kind='contact', body='Could we see this home?\n\n' + HOME['address'] + '\n' + LINK,
                consent=True, request_id=str(uuid.uuid4()), web_listing=home)


def test_listing_roundtrip_client_agent_legacy_shape_and_scope(app, seed, client):
    headers = auth(signup(client, agent_id=seed['agent_a']))
    request = inquiry(HOME)
    response = client.post(BASE + '/inquiries', headers=headers, json=request)
    assert response.status_code == 201
    inquiry_id = response.json['id']
    assert client.post(BASE + '/inquiries', headers=headers, json=request).json == response.json
    rows = client.get(BASE + '/inquiries', headers=headers).json['inquiries']
    shared = next(row for row in rows if row['id'] == inquiry_id)
    assert shared['messages'][0]['web_listing'] == HOME
    assert shared['body'] == request['body']
    assert shared['listing_snapshot'] is None, 'Web cards must not appear as native sample homes in older clients'
    agent = _auth(_agent_session(client, email='agent_a@test.com').json['token'])
    path = f'/api/agent/v1/inquiries/{inquiry_id}'
    assert client.get(path, headers=agent).json['inquiry']['messages'][0]['web_listing'] == HOME
    foreign = _auth(_agent_session(client, email='owner_b@test.com').json['token'])
    assert client.get(path, headers=foreign).status_code == 404
    assert client.get(BASE + '/inquiries', headers=auth(signup(client))).json['inquiries'] == []
    followup = dict(body='Saturday works.', request_id=str(uuid.uuid4()))
    assert client.post(BASE + f'/inquiries/{inquiry_id}/messages', headers=headers, json=followup).status_code == 200
    messages = client.get(path, headers=agent).json['inquiry']['messages']
    assert messages[0]['web_listing'] == HOME
    assert 'web_listing' not in messages[-1]
    with app.app_context():
        assert db.session.get(ClientBrowseInquiry, inquiry_id).listing_snapshot['web_listing'] == HOME
    for changed in ({**HOME, 'price': '$850,000'}, {**HOME, 'photo_url': PHOTO.replace('1536', '768')}, None):
        assert client.post(BASE + '/inquiries', headers=headers, json={**request, 'web_listing': changed}).status_code == 409


@pytest.mark.parametrize('change', [
    {'url': 'https://evil.example/homedetails/a/1_zpid/'},
    {'url': 'javascript:alert(1)'}, {'url': 'https://www.zillow.com/homes/'},
    {'url': 'https://name@www.zillow.com/homedetails/a/1_zpid/'},
    {'photo_url': 'https://127.0.0.1/private.jpg'},
    {'photo_url': PHOTO + '?tracking=private'}, {'photo_url': PHOTO.replace('photos.', 'evil.')},
    {'address': 'a' * 301}, {'address': 'home\nspoof'}, {'price': 899000}, {'price': '$-100'},
])
def test_rejects_invalid_listing_snapshot(client, change):
    headers = auth(signup(client))
    response = client.post(BASE + '/inquiries', headers=headers, json=inquiry({**HOME, **change}))
    assert response.status_code == 400
    assert client.get(BASE + '/inquiries', headers=headers).json['inquiries'] == []


def test_optional_price_photo_and_canonical_link(client):
    headers = auth(signup(client))
    home = dict(url=LINK + '?utm_source=test#photos', address=HOME['address'])
    request = inquiry(home)
    assert client.post(BASE + '/inquiries', headers=headers, json=request).status_code == 201
    listing = client.get(BASE + '/inquiries', headers=headers).json['inquiries'][0]['messages'][0]['web_listing']
    assert listing == dict(url=LINK, address=HOME['address'])
    assert client.post(BASE + '/inquiries', headers=headers, json={**request, 'web_listing': listing}).status_code == 200
    assert client.post(BASE + '/inquiries', headers=headers, json={**inquiry(HOME), 'listing_id': 'h01'}).status_code == 400
