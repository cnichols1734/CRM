"""Private profile photos owned by the authenticated identity."""
from io import BytesIO
import uuid
import warnings
import time
from functools import lru_cache

from flask import current_app, g, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge

from models import db
from services import supabase_storage as storage

MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_REQUEST_BYTES = MAX_PHOTO_BYTES + 64 * 1024
BUCKET = storage.CONTACT_FILES_BUCKET


@lru_cache(maxsize=4096)
def _signed_photo(path, window):
    return storage.get_signed_url(BUCKET, path)


def avatar_url(identity):
    path = getattr(identity, 'avatar_path', None)
    if not path:
        return None
    cache = getattr(g, '_profile_photo_urls', None)
    if cache is None:
        cache = g._profile_photo_urls = {}
    if path not in cache:
        try:
            cache[path] = _signed_photo(path, int(time.time() // 600))
        except Exception:
            current_app.logger.warning('Profile photo URL unavailable')
            cache[path] = None
    return cache[path]


def delete_photo_object(path):
    try:
        return storage.delete_file(BUCKET, path)
    except Exception:
        current_app.logger.warning('Profile photo storage deletion failed')
        return False


def purge_identity_photos(identity, kind):
    prefix = f'profile-photos/{identity.organization_id}/{kind}/{identity.id}'
    try:
        bucket = storage.get_supabase_client().storage.from_(BUCKET)
        for _ in range(100):
            rows = bucket.list(prefix, {'limit': 100, 'offset': 0})
            names = [row['name'] for row in rows if row.get('name') and '/' not in row['name']]
            if not names:
                return True
            bucket.remove([f'{prefix}/{name}' for name in names])
        current_app.logger.warning('Profile photo cleanup exceeded its batch limit')
    except Exception:
        current_app.logger.warning('Profile photo cleanup failed')
    return False


def normalize_photo(data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 25_000_000:
                    raise ValueError('Choose a JPEG, PNG or WebP photo under 25 megapixels.')
                image.load()
                photo = ImageOps.exif_transpose(image).convert('RGB')
                photo = ImageOps.fit(photo, (512, 512), method=Image.Resampling.LANCZOS)
                output = BytesIO()
                photo.save(output, format='JPEG', quality=88)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('Choose a valid JPEG, PNG or WebP photo.')


def change_photo(identity, kind):
    request.max_content_length = PHOTO_REQUEST_BYTES
    data = None
    if request.method == 'POST':
        try:
            upload = request.files.get('file')
            if upload is None:
                return jsonify(error='Choose a profile photo.'), 400
            raw = upload.stream.read(MAX_PHOTO_BYTES + 1)
            if len(raw) > MAX_PHOTO_BYTES:
                return jsonify(error='Choose a photo smaller than 5 MB.'), 413
            data = normalize_photo(raw)
        except RequestEntityTooLarge:
            return jsonify(error='Choose a photo smaller than 5 MB.'), 413
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
    # Serialize replacements so concurrent uploads cannot leak the previous object.
    identity = type(identity).query.filter_by(id=identity.id, organization_id=identity.organization_id).with_for_update().populate_existing().one()
    old_path = identity.avatar_path
    path = f'profile-photos/{identity.organization_id}/{kind}/{identity.id}/{uuid.uuid4().hex}.jpg' if data else None
    if request.method == 'DELETE' and old_path and not purge_identity_photos(identity, kind):
        db.session.rollback()
        return jsonify(error='Your photo could not be removed. Try again.'), 503
    try:
        if path:
            storage.upload_file(BUCKET, path, data, 'profile.jpg', 'image/jpeg')
        identity.avatar_path = path
        result_url = avatar_url(identity)
        db.session.commit()
    except Exception:
        db.session.rollback()
        if path:
            delete_photo_object(path)
        current_app.logger.warning('Profile photo could not be saved')
        return jsonify(error='Your photo could not be saved. Try again.'), 503
    if old_path and request.method != 'DELETE':
        delete_photo_object(old_path)
    return jsonify(avatar_url=result_url)


def contact_avatar_url(contact):
    from models import ClientBrowseAccount
    accounts = ClientBrowseAccount.query.filter_by(organization_id=contact.organization_id,
        contact_id=contact.id, owns_contact=True).limit(2).all()
    return avatar_url(accounts[0]) if len(accounts) == 1 else None
