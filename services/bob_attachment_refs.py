"""Signed, user-bound attachment references for B.O.B. chat uploads.

The browser never hands the stream endpoint a raw storage path. Upload returns
an ``itsdangerous`` token that embeds ownership metadata and a content digest.
Resolve verifies the token, ownership, storage prefix, size, and digest before
returning bytes.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from services.supabase_storage import CHAT_ATTACHMENTS_BUCKET, download_file

logger = logging.getLogger(__name__)

ATTACHMENT_REF_MAX_AGE = 30 * 60  # 30 minutes
ATTACHMENT_REF_SALT = 'bob-chat-attachment-v1'


class AttachmentRefError(Exception):
    """Raised when an attachment reference cannot be trusted or loaded."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AttachmentMeta:
    user_id: int
    organization_id: int
    storage_path: str
    mime: str
    size: int
    filename: str
    digest: str
    bucket: str = CHAT_ATTACHMENTS_BUCKET

    def to_public_dict(self) -> dict[str, Any]:
        return {
            'filename': self.filename,
            'mime': self.mime,
            'size': self.size,
            'digest': self.digest,
        }


@dataclass(frozen=True)
class ResolvedAttachment:
    meta: AttachmentMeta
    data: bytes


def sha256_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_storage_prefix(user_id: int) -> str:
    return f'user_{int(user_id)}/'


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config['SECRET_KEY'],
        salt=ATTACHMENT_REF_SALT,
    )


def make_attachment_ref(
    *,
    user_id: int,
    organization_id: int,
    storage_path: str,
    mime: str,
    size: int,
    filename: str,
    digest: str,
    bucket: str = CHAT_ATTACHMENTS_BUCKET,
) -> str:
    """Sign ownership metadata for a freshly uploaded chat attachment."""
    prefix = expected_storage_prefix(user_id)
    if not storage_path.startswith(prefix):
        raise AttachmentRefError('Attachment path is not owned by this user.')
    if size < 0:
        raise AttachmentRefError('Attachment size is invalid.')
    if not digest:
        raise AttachmentRefError('Attachment digest is required.')

    payload = {
        'user_id': int(user_id),
        'organization_id': int(organization_id),
        'storage_path': storage_path,
        'mime': mime or 'application/octet-stream',
        'size': int(size),
        'filename': filename or 'attachment',
        'digest': digest,
        'bucket': bucket,
    }
    return _serializer().dumps(payload)


def parse_attachment_ref(
    token: str,
    *,
    user_id: int,
    organization_id: int,
    max_age: int = ATTACHMENT_REF_MAX_AGE,
) -> AttachmentMeta:
    """Validate a signed reference without downloading bytes."""
    if not token or not isinstance(token, str):
        raise AttachmentRefError('Attachment reference is missing.')

    try:
        payload = _serializer().loads(token, max_age=max_age)
    except SignatureExpired as exc:
        raise AttachmentRefError(
            'That attachment link expired. Upload the file again.'
        ) from exc
    except BadSignature as exc:
        raise AttachmentRefError(
            'That attachment reference is not valid.'
        ) from exc

    if not isinstance(payload, dict):
        raise AttachmentRefError('That attachment reference is not valid.')

    try:
        meta = AttachmentMeta(
            user_id=int(payload['user_id']),
            organization_id=int(payload['organization_id']),
            storage_path=str(payload['storage_path']),
            mime=str(payload.get('mime') or 'application/octet-stream'),
            size=int(payload['size']),
            filename=str(payload.get('filename') or 'attachment'),
            digest=str(payload['digest']),
            bucket=str(payload.get('bucket') or CHAT_ATTACHMENTS_BUCKET),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AttachmentRefError(
            'That attachment reference is not valid.'
        ) from exc

    if meta.user_id != int(user_id) or meta.organization_id != int(organization_id):
        raise AttachmentRefError('That attachment belongs to a different account.')

    prefix = expected_storage_prefix(user_id)
    if not meta.storage_path.startswith(prefix):
        raise AttachmentRefError('That attachment path is not allowed.')

    return meta


def resolve_attachment(
    token: str,
    *,
    user_id: int,
    organization_id: int,
    max_age: int = ATTACHMENT_REF_MAX_AGE,
) -> ResolvedAttachment:
    """Parse, download, and integrity-check an attachment reference."""
    meta = parse_attachment_ref(
        token,
        user_id=user_id,
        organization_id=organization_id,
        max_age=max_age,
    )

    try:
        data = download_file(meta.bucket, meta.storage_path)
    except Exception as exc:
        logger.warning(
            'B.O.B. attachment download failed path=%s err=%s',
            meta.storage_path, exc,
        )
        raise AttachmentRefError(
            'Could not load that attachment. Upload it again.'
        ) from exc

    if len(data) != meta.size:
        raise AttachmentRefError(
            'That attachment no longer matches what was uploaded.'
        )
    if sha256_digest(data) != meta.digest:
        raise AttachmentRefError(
            'That attachment no longer matches what was uploaded.'
        )

    return ResolvedAttachment(meta=meta, data=data)
