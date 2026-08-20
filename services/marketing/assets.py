"""Public image uploads for marketing emails.

Existing CRM buckets are private with expiring signed URLs. An image in an
already-delivered email has to keep working, so production uses a public
Supabase bucket and we store that permanent URL.

Locally, that bucket often does not exist. Debug / SQLite falls back to
``static/marketing-uploads`` so the studio can still put a photo in the email.
"""
from __future__ import annotations

import io
import os
import uuid
from services.supabase_storage import get_supabase_client

BUCKET = 'marketing-assets'
LOCAL_SUBDIR = 'marketing-uploads'
ALLOWED_TYPES = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
    'image/gif': '.gif',
}
CONTENT_TYPE_ALIASES = {
    'image/jpg': 'image/jpeg',
    'image/pjpeg': 'image/jpeg',
    'image/x-png': 'image/png',
}
EXT_TO_TYPE = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
}
MAX_BYTES = 5 * 1024 * 1024
MAX_WIDTH = 1200


class AssetError(ValueError):
    pass


def _normalize_content_type(content_type: str, original_name: str) -> str:
    guessed = (content_type or '').lower().split(';')[0].strip()
    guessed = CONTENT_TYPE_ALIASES.get(guessed, guessed)
    if guessed in ALLOWED_TYPES:
        return guessed
    ext = os.path.splitext(original_name or '')[1].lower()
    return EXT_TO_TYPE.get(ext, guessed)


def _supabase_configured() -> bool:
    return bool(os.getenv('SUPABASE_URL') and os.getenv('SUPABASE_KEY'))


def _allow_local_store() -> bool:
    if os.getenv('MARKETING_ASSETS_LOCAL') == '1':
        return True
    db_url = os.getenv('DATABASE_URL', '')
    if db_url.startswith('sqlite'):
        return True
    try:
        from flask import current_app, has_app_context
        if has_app_context() and (current_app.debug or current_app.testing):
            return True
    except Exception:
        pass
    return False


def _local_root() -> str:
    override = os.getenv('MARKETING_ASSETS_DIR')
    if override:
        return override
    from flask import current_app
    return os.path.join(current_app.static_folder, LOCAL_SUBDIR)


def _public_url(path: str) -> str:
    client = get_supabase_client()
    # supabase-py v2: get_public_url returns a string or a dict depending on version.
    result = client.storage.from_(BUCKET).get_public_url(path)
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        data = result.get('data') or result
        return data.get('publicUrl') or data.get('public_url') or ''
    return str(result)


def _resize(file_bytes: bytes, content_type: str) -> bytes:
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(file_bytes))
        image.load()
        if image.width <= MAX_WIDTH:
            return file_bytes
        ratio = MAX_WIDTH / float(image.width)
        height = max(int(image.height * ratio), 1)
        image = image.resize((MAX_WIDTH, height))
        buf = io.BytesIO()
        fmt = 'JPEG' if content_type == 'image/jpeg' else image.format or 'PNG'
        if fmt.upper() == 'JPEG' and image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')
        image.save(buf, format=fmt.upper())
        return buf.getvalue()
    except Exception:
        return file_bytes


def _store_local(
    payload: bytes,
    *,
    content_type: str,
    organization_id: int,
    original_name: str,
) -> dict:
    ext = ALLOWED_TYPES[content_type]
    rel = f'org/{int(organization_id)}/{uuid.uuid4().hex}{ext}'
    dest = os.path.join(_local_root(), rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(payload)

    from flask import has_request_context, url_for
    if has_request_context():
        url = url_for('static', filename=f'{LOCAL_SUBDIR}/{rel}', _external=True)
    else:
        url = f'/static/{LOCAL_SUBDIR}/{rel}'
    return {
        'url': url,
        'path': rel,
        'content_type': content_type,
        'filename': original_name or rel,
        'storage': 'local',
    }


def _store_supabase(
    payload: bytes,
    *,
    content_type: str,
    organization_id: int,
    original_name: str,
) -> dict:
    ext = ALLOWED_TYPES[content_type]
    path = f'org/{int(organization_id)}/{uuid.uuid4().hex}{ext}'
    try:
        client = get_supabase_client()
        client.storage.from_(BUCKET).upload(
            path,
            payload,
            {'content-type': content_type, 'upsert': 'false'},
        )
    except AssetError:
        raise
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        lowered = message.lower()
        if 'bucket' in lowered or 'not found' in lowered:
            raise AssetError(
                'The marketing-assets storage bucket is missing. '
                'Create a public Supabase bucket named marketing-assets.'
            ) from exc
        raise AssetError(f'Could not store that image. {message}') from exc

    url = _public_url(path).rstrip('?')
    return {
        'url': url,
        'path': path,
        'content_type': content_type,
        'filename': original_name or path,
        'storage': 'supabase',
    }


def upload(
    file_bytes: bytes,
    *,
    content_type: str,
    organization_id: int,
    original_name: str = '',
) -> dict:
    content_type = _normalize_content_type(content_type, original_name)
    if content_type not in ALLOWED_TYPES:
        raise AssetError('Use a JPEG, PNG, WebP, or GIF.')
    if not file_bytes:
        raise AssetError('That file is empty.')
    if len(file_bytes) > MAX_BYTES:
        raise AssetError('Images have to be under 5 MB.')

    payload = _resize(file_bytes, content_type)

    if _supabase_configured():
        try:
            return _store_supabase(
                payload,
                content_type=content_type,
                organization_id=organization_id,
                original_name=original_name,
            )
        except AssetError:
            if _allow_local_store():
                return _store_local(
                    payload,
                    content_type=content_type,
                    organization_id=organization_id,
                    original_name=original_name,
                )
            raise

    if _allow_local_store():
        return _store_local(
            payload,
            content_type=content_type,
            organization_id=organization_id,
            original_name=original_name,
        )

    raise AssetError(
        'Image storage is not configured. Set SUPABASE_URL and SUPABASE_KEY, '
        'and create a public bucket named marketing-assets.'
    )
