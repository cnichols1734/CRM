"""Private, conversation-bound files for human messaging."""
from datetime import datetime, timedelta
from functools import lru_cache
from io import BytesIO
from pathlib import PurePath
import hashlib
import time
import uuid
import warnings
import zipfile

from flask import current_app, g, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from models import db, MobileChatAttachment
from services import supabase_storage as storage

BUCKET = storage.CHAT_ATTACHMENTS_BUCKET
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 64 * 1024
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS = 5
DAILY_UPLOAD_LIMIT = 100
DRAFT_LIFETIME = timedelta(hours=24)
DOCUMENT_TYPES = {
    '.pdf': 'application/pdf', '.txt': 'text/plain', '.csv': 'text/csv',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}


class AttachmentError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def attachment_ids(data):
    values = data.get('attachment_ids', [])
    if not isinstance(values, list) or len(values) > MAX_ATTACHMENTS:
        raise AttachmentError('Attach up to five files to one message.')
    try:
        ids = [str(uuid.UUID(value)) for value in values]
    except (ValueError, TypeError, AttributeError):
        raise AttachmentError('Choose valid uploaded attachments.')
    if len(set(ids)) != len(ids):
        raise AttachmentError('Each attachment can appear only once in a message.')
    return ids


def normalize_file(data, original_name):
    name = secure_filename(PurePath(original_name or 'attachment').name)[:160] or 'attachment'
    ext = PurePath(name).suffix.lower()
    if not data:
        raise AttachmentError('Choose a file that is not empty.')
    if ext in {'.jpg', '.jpeg', '.png', '.webp'}:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 25_000_000:
                        raise AttachmentError('Choose a photo under 25 megapixels.')
                    image.load()
                    photo = ImageOps.exif_transpose(image).convert('RGB')
                    photo.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
                    output = BytesIO()
                    photo.save(output, format='JPEG', quality=90)
                    return output.getvalue(), PurePath(name).stem[:150] + '.jpg', 'image/jpeg', 'image'
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise AttachmentError('Choose a valid JPEG, PNG or WebP image.')
    if ext not in DOCUMENT_TYPES:
        raise AttachmentError('Choose a photo, PDF, DOCX, XLSX, PPTX, TXT or CSV file.')
    if ext == '.pdf':
        try:
            import fitz
            if not data.startswith(b'%PDF-'):
                raise ValueError()
            with fitz.open(stream=data, filetype='pdf') as document:
                if document.is_encrypted or not 1 <= document.page_count <= 500:
                    raise ValueError()
        except Exception:
            raise AttachmentError('Choose a readable, unencrypted PDF with up to 500 pages.')
    elif ext in {'.docx', '.xlsx', '.pptx'}:
        required = {'.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml', '.pptx': 'ppt/presentation.xml'}[ext]
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                files = archive.infolist()
                names = {info.filename for info in files}
                if len(files) > 2000 or sum(info.file_size for info in files) > 40 * 1024 * 1024:
                    raise ValueError()
                if required not in names or '[Content_Types].xml' not in names:
                    raise ValueError()
                if any('vbaproject' in n.lower() or n.endswith(('.exe', '.dll', '.js')) for n in names):
                    raise ValueError()
                if any(info.flag_bits & 1 for info in files) or archive.testzip() is not None:
                    raise ValueError()
        except (ValueError, OSError, RuntimeError, zipfile.BadZipFile):
            raise AttachmentError('Choose a valid Office document without macros, under 40 MB when expanded.')
    else:
        try:
            text = data.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError()
        except (UnicodeError, ValueError):
            raise AttachmentError('Choose a UTF-8 text or CSV file.')
    return data, name, DOCUMENT_TYPES[ext], 'document'


@lru_cache(maxsize=4096)
def _signed_url(path, window):
    return storage.get_signed_url(BUCKET, path, expires_in=3600)


def attachment_payload(row):
    try:
        url = _signed_url(row.storage_path, int(time.time() // 600))
    except Exception:
        current_app.logger.warning('Message attachment URL unavailable')
        url = None
    return dict(id=row.id, filename=row.filename, content_type=row.content_type,
                size=row.size, kind=row.kind, url=url)


def consume_upload_allowance(org_id, owner_kind, owner_id):
    from models import ClientBrowseRateLimit
    if db.engine.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    identity = f'mobile-attachment:{org_id}:{owner_kind}:{owner_id}:{datetime.utcnow():%Y%m%d}'
    key = hashlib.sha256(identity.encode()).hexdigest()
    stmt = insert(ClientBrowseRateLimit).values(key=key, hits=1, expires_at=int(time.time()) + 172800)
    db.session.execute(stmt.on_conflict_do_update(index_elements=['key'], set_={'hits': ClientBrowseRateLimit.hits + 1}))
    used = db.session.query(ClientBrowseRateLimit.hits).filter_by(key=key).scalar()
    return used <= DAILY_UPLOAD_LIMIT


def upload(owner, owner_kind, target_kind, target_id):
    request.max_content_length = MAX_REQUEST_BYTES
    try:
        file = request.files.get('file')
        if file is None:
            raise AttachmentError('Choose a file to attach.')
        raw = file.stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise AttachmentError('Choose a file smaller than 10 MB.', 413)
        data, filename, content_type, kind = normalize_file(raw, file.filename)
        if len(data) > MAX_FILE_BYTES:
            raise AttachmentError('Choose a smaller photo.', 413)
        request_id = str(uuid.UUID(request.form.get('request_id') or str(uuid.uuid4())))
    except RequestEntityTooLarge:
        return jsonify(error='Choose a file smaller than 10 MB.'), 413
    except AttachmentError as exc:
        return jsonify(error=str(exc)), exc.status
    except (ValueError, TypeError, AttributeError):
        return jsonify(error='Use a valid upload request ID.'), 400
    version = getattr(owner, 'session_version', None)
    owner = type(owner).query.filter_by(id=owner.id, organization_id=owner.organization_id).with_for_update().populate_existing().first()
    if owner is None or (version is not None and owner.session_version != version):
        return jsonify(error='Sign in again before uploading.'), 401
    query = MobileChatAttachment.query.filter_by(organization_id=owner.organization_id, owner_kind=owner_kind, owner_id=owner.id)
    digest = hashlib.sha256(data).hexdigest()
    prior = query.filter_by(target_kind=target_kind, target_id=target_id, request_id=request_id).first()
    if prior and prior.message_id is None and prior.created_at < datetime.utcnow() - DRAFT_LIFETIME:
        if not delete_object(prior.storage_path):
            return jsonify(error='The expired upload could not be removed. Try again.'), 503
        db.session.delete(prior)
        db.session.flush()
        prior = None
    if prior:
        if prior.digest != digest or prior.filename != filename:
            return jsonify(error='This upload request ID belongs to another file.'), 409
        return jsonify(attachment=attachment_payload(prior)), 200
    cleanup_expired(owner.organization_id, owner_kind, owner.id, limit=20)
    if not consume_upload_allowance(owner.organization_id, owner_kind, owner.id):
        return jsonify(error='Your daily attachment upload limit has been reached.'), 429
    if query.filter_by(message_id=None).count() >= 20:
        return jsonify(error='Send or remove your pending attachments before uploading more.'), 409
    identifier = str(uuid.uuid4())
    path = f'mobile-chat/{owner.organization_id}/{owner_kind}/{owner.id}/{identifier}{PurePath(filename).suffix}'
    row = MobileChatAttachment(id=identifier, organization_id=owner.organization_id, owner_kind=owner_kind,
        owner_id=owner.id, target_kind=target_kind, target_id=target_id, request_id=request_id,
        filename=filename, content_type=content_type, kind=kind, size=len(data), digest=digest, storage_path=path)
    try:
        storage.upload_file(BUCKET, path, data, filename, content_type)
        db.session.add(row)
        result = attachment_payload(row)
        if result['url'] is None:
            raise RuntimeError('Attachment URL unavailable')
        db.session.commit()
        return jsonify(attachment=result), 201
    except Exception:
        db.session.rollback()
        delete_object(path)
        current_app.logger.warning('Message attachment upload failed')
        return jsonify(error='Your file could not be uploaded. Try again.'), 503


def delete_object(path):
    try:
        return storage.delete_file(BUCKET, path)
    except Exception:
        return False


def bind_attachments(ids, *, org_id, owner_kind, owner_id, target_kind, target_id, message_kind, message_id, replay=False):
    existing = MobileChatAttachment.query.filter_by(organization_id=org_id, message_kind=message_kind, message_id=message_id).all()
    if replay or existing:
        if {row.id for row in existing} != set(ids):
            raise AttachmentError('This request ID belongs to different attachments.', 409)
        return
    rows = MobileChatAttachment.query.filter(MobileChatAttachment.organization_id == org_id,
        MobileChatAttachment.id.in_(ids)).order_by(MobileChatAttachment.id).with_for_update().all() if ids else []
    if len(rows) != len(ids):
        raise AttachmentError('An attachment is unavailable. Upload it again.', 410)
    if sum(row.size for row in rows) > MAX_MESSAGE_BYTES:
        raise AttachmentError('Keep the attachments for one message under 25 MB.')
    for row in rows:
        if (row.owner_kind, row.owner_id, row.target_kind, row.target_id) != (owner_kind, owner_id, target_kind, target_id):
            raise AttachmentError('That attachment belongs to another conversation.', 403)
        if row.message_id is not None:
            raise AttachmentError('That attachment has already been sent.', 409)
        if row.created_at < datetime.utcnow() - DRAFT_LIFETIME:
            raise AttachmentError('That upload expired. Upload the file again.', 410)
        row.message_kind, row.message_id = message_kind, message_id


def message_attachments(org_id, message_kind, message_ids):
    cache = getattr(g, '_mobile_message_attachments', None)
    if cache is None:
        cache = g._mobile_message_attachments = {}
    missing = [identifier for identifier in message_ids if (org_id, message_kind, identifier) not in cache]
    if missing:
        for identifier in missing:
            cache[(org_id, message_kind, identifier)] = []
        rows = MobileChatAttachment.query.filter(MobileChatAttachment.organization_id == org_id,
            MobileChatAttachment.message_kind == message_kind, MobileChatAttachment.message_id.in_(missing)).order_by(
            MobileChatAttachment.created_at, MobileChatAttachment.id).all()
        for row in rows:
            cache[(org_id, message_kind, row.message_id)].append(row)
    return {identifier: [attachment_payload(row) for row in cache[(org_id, message_kind, identifier)]] for identifier in message_ids}


def discard(row, owner_kind, owner_id):
    row = MobileChatAttachment.query.filter_by(id=row.id, organization_id=row.organization_id).with_for_update().populate_existing().first()
    if row is None:
        return jsonify(error='Attachment not found.'), 404
    if (row.owner_kind, row.owner_id) != (owner_kind, owner_id) or row.message_id is not None:
        return jsonify(error='Only your unsent attachments can be removed.'), 403
    if not delete_object(row.storage_path):
        return jsonify(error='Your file could not be removed. Try again.'), 503
    db.session.delete(row)
    db.session.commit()
    return jsonify(ok=True)


def cleanup_expired(org_id, owner_kind=None, owner_id=None, limit=100):
    query = MobileChatAttachment.query.filter_by(organization_id=org_id, message_id=None).filter(
        MobileChatAttachment.created_at < datetime.utcnow() - DRAFT_LIFETIME)
    if owner_kind is not None:
        query = query.filter_by(owner_kind=owner_kind, owner_id=owner_id)
    rows = query.order_by(MobileChatAttachment.created_at).limit(limit).with_for_update(skip_locked=True).all()
    removed = 0
    for row in rows:
        if delete_object(row.storage_path):
            db.session.delete(row)
            removed += 1
    db.session.flush()
    return removed


def purge_browse_attachments(account):
    from sqlalchemy import or_, and_
    from models import ClientBrowseInquiry
    ids = db.session.query(ClientBrowseInquiry.id).filter_by(organization_id=account.organization_id, account_id=account.id)
    rows = MobileChatAttachment.query.filter(MobileChatAttachment.organization_id == account.organization_id,
        or_(and_(MobileChatAttachment.owner_kind == 'browse', MobileChatAttachment.owner_id == account.id),
            and_(MobileChatAttachment.target_kind == 'new_inquiry', MobileChatAttachment.target_id == account.id),
            and_(MobileChatAttachment.target_kind == 'inquiry', MobileChatAttachment.target_id.in_(ids)))).with_for_update().all()
    for row in rows:
        if not delete_object(row.storage_path):
            return False
    # Also remove upload objects left behind by a failed database commit.
    if not purge_owner_prefix(account.organization_id, 'browse', account.id):
        return False
    for row in rows:
        db.session.delete(row)
    return True


def purge_owner_prefix(org_id, owner_kind, owner_id):
    from services.profile_photos import storage_configured
    if not storage_configured():
        return True
    prefix = f'mobile-chat/{org_id}/{owner_kind}/{owner_id}'
    try:
        bucket = storage.get_supabase_client().storage.from_(BUCKET)
        for _ in range(1000):
            rows = bucket.list(prefix, {'limit': 100, 'offset': 0})
            names = [row['name'] for row in rows if row.get('name') and '/' not in row['name']]
            if not names:
                return True
            bucket.remove([f'{prefix}/{name}' for name in names])
    except Exception:
        current_app.logger.warning('Message attachment cleanup failed')
    return False


def purge_org_attachments(org_id):
    from models import User, ClientBrowseAccount, TransactionParticipant
    rows = MobileChatAttachment.query.filter_by(organization_id=org_id).with_for_update().all()
    owners = {(row.owner_kind, row.owner_id) for row in rows}
    for kind, model in [('agent', User), ('browse', ClientBrowseAccount), ('participant', TransactionParticipant)]:
        owners.update((kind, identifier) for (identifier,) in db.session.query(model.id).filter_by(organization_id=org_id))
    for row in rows:
        if not delete_object(row.storage_path):
            return False
    for kind, identifier in owners:
        if not purge_owner_prefix(org_id, kind, identifier):
            return False
    for row in rows:
        db.session.delete(row)
    db.session.flush()
    return True
