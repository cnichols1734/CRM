"""Marketing image upload: local fallback when Supabase is not available."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import db

from marketing_helpers import enable_campaigns, load_org_user


# 1x1 PNG
TINY_PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082'
)


class TestMarketingAssets:
    def test_upload_stores_locally_without_supabase(self, owner_a_client, app, seed, tmp_path, monkeypatch):
        monkeypatch.delenv('SUPABASE_URL', raising=False)
        monkeypatch.delenv('SUPABASE_KEY', raising=False)
        monkeypatch.setenv('MARKETING_ASSETS_DIR', str(tmp_path))
        monkeypatch.setenv('MARKETING_ASSETS_LOCAL', '1')
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()

        resp = owner_a_client.post(
            '/marketing/api/upload',
            data={'file': (io.BytesIO(TINY_PNG), 'house.png')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        payload = resp.get_json()
        assert payload['url']
        assert payload['storage'] == 'local'
        written = list(tmp_path.rglob('*.png'))
        assert written

    def test_rejects_non_image(self, owner_a_client, app, seed, monkeypatch):
        monkeypatch.setenv('MARKETING_ASSETS_LOCAL', '1')
        with app.app_context():
            org, _ = load_org_user(seed)
            enable_campaigns(org)
            db.session.commit()

        resp = owner_a_client.post(
            '/marketing/api/upload',
            data={'file': (io.BytesIO(b'not an image'), 'notes.txt')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 400
        assert 'JPEG' in resp.get_json()['error']
