"""Photo ownership, normalized storage, and counterpart API projections."""
from io import BytesIO

import pytest
from PIL import Image

from models import db, User, ClientBrowseAccount, Organization
from services import supabase_storage
from tests.test_agent_api import _auth, _owner_token, _seller_thread
from tests.test_client_discovery import signup, auth, BASE
from tests.test_client_portal_api import _open_session


@pytest.fixture
def photo_storage(monkeypatch):
    objects = {}
    class Bucket:
        def list(self, prefix, options):
            return [{'name': path.rsplit('/', 1)[-1]} for path in objects if path.startswith(prefix + '/')][:100]
        def remove(self, paths):
            for path in paths:
                objects.pop(path, None)
    class Storage:
        def from_(self, bucket):
            return Bucket()
    from types import SimpleNamespace
    monkeypatch.setattr(supabase_storage, 'get_supabase_client', lambda: SimpleNamespace(storage=Storage()))
    monkeypatch.setattr(supabase_storage, 'upload_file', lambda bucket, path, data, *args: objects.update({path: data}))
    monkeypatch.setattr(supabase_storage, 'get_signed_url', lambda bucket, path: 'https://storage.example/' + path)
    monkeypatch.setattr(supabase_storage, 'delete_file', lambda bucket, path: objects.pop(path, None) is not None)
    return objects


def photo():
    image = BytesIO()
    Image.new('RGB', (40, 80), 'red').save(image, format='PNG')
    image.seek(0)
    return {'file': (image, 'photo.png')}


def test_agent_photo_normalized_visible_to_client_and_removed(app, client, seed, photo_storage):
    headers = _auth(_owner_token(client))
    route = '/api/agent/v1/me/photo'
    assert client.post(route, data=photo()).status_code == 401
    assert client.post(route, headers=headers, data={'file': (BytesIO(b'not an image'), 'x.jpg')}).status_code == 400
    response = client.post(route, headers=headers, data=photo())
    assert response.status_code == 200
    url = response.json['avatar_url']
    assert client.get('/api/agent/v1/me', headers=headers).json['user']['avatar_url'] == url
    stored = next(iter(photo_storage.values()))
    with Image.open(BytesIO(stored)) as image:
        assert image.format == 'JPEG'
        assert image.size == (512, 512)
        assert not image.getexif()
    with app.app_context():
        tx, participant, access = _seller_thread(seed)
        db.session.commit()
        code = access.invite_code
    client_headers = _auth(_open_session(client, code).json['token'])
    assert client.get('/api/client/v1/deal', headers=client_headers).json['agent']['avatar_url'] == url
    assert client.post(route, headers=client_headers, data=photo()).status_code == 401
    assert client.delete(route, headers=headers).json['avatar_url'] is None
    assert photo_storage == {}


def test_browse_and_deal_photos_have_separate_ownership(app, client, seed, photo_storage):
    with app.app_context():
        org = db.session.get(Organization, seed['org_a'])
        old_settings = org.client_app_settings
        org.client_app_settings = {'enabled': True}
        tx, participant, access = _seller_thread(seed)
        db.session.commit()
        code = access.invite_code
    try:
        account = signup(client, agent_id=seed['owner_a'])
        headers = auth(account)
        route = BASE + '/account/photo'
        uploaded = client.post(route, headers=headers, data=photo())
        assert uploaded.status_code == 200
        url = uploaded.json['avatar_url']
        assert client.get(BASE + '/account', headers=headers).json['account']['avatar_url'] == url
        stranger = auth(signup(client))
        assert client.get(BASE + '/account', headers=stranger).json['account']['avatar_url'] is None
        assert client.post('/api/client/v1/profile/photo', headers=headers, data=photo()).status_code == 401
        deal_headers = _auth(_open_session(client, code).json['token'])
        deal_url = client.post('/api/client/v1/profile/photo', headers=deal_headers, data=photo()).json['avatar_url']
        assert deal_url != url
        assert client.get('/api/client/v1/deal', headers=deal_headers).json['avatar_url'] == deal_url
        assert client.delete(route, headers=headers).json['avatar_url'] is None
        assert client.get('/api/client/v1/deal', headers=deal_headers).json['avatar_url'] == deal_url
        client.delete('/api/client/v1/profile/photo', headers=deal_headers)
        assert photo_storage == {}
    finally:
        with app.app_context():
            db.session.get(Organization, seed['org_a']).client_app_settings = old_settings
            db.session.commit()


def test_failed_replacement_preserves_photo_and_oversize_is_rejected(app, client, seed, photo_storage, monkeypatch):
    headers = _auth(_owner_token(client))
    route = '/api/agent/v1/me/photo'
    original = client.post(route, headers=headers, data=photo()).json['avatar_url']
    def fail_upload(*args, **kwargs):
        raise RuntimeError('Storage unavailable')
    monkeypatch.setattr(supabase_storage, 'upload_file', fail_upload)
    assert client.post(route, headers=headers, data=photo()).status_code == 503
    assert client.get('/api/agent/v1/me', headers=headers).json['user']['avatar_url'] == original
    oversized = {'file': (BytesIO(b'x' * (5 * 1024 * 1024 + 1)), 'large.jpg')}
    assert client.post(route, headers=headers, data=oversized).status_code == 413
    client.delete(route, headers=headers)


def test_photo_removal_purges_replaced_objects_without_touching_other_identity(app, client, seed, photo_storage):
    headers = _auth(_owner_token(client))
    client.post('/api/agent/v1/me/photo', headers=headers, data=photo())
    path = next(iter(photo_storage))
    old = path.rsplit('/', 1)[0] + '/orphan.jpg'
    other = f'profile-photos/{seed["org_b"]}/agent/{seed["owner_b"]}/keep.jpg'
    photo_storage[old] = b'old'
    photo_storage[other] = b'other'
    assert client.delete('/api/agent/v1/me/photo', headers=headers).status_code == 200
    assert set(photo_storage) == {other}
