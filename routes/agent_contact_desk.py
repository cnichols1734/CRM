"""Contact-detail JSON for the agent iPhone app.

Attach via ``register(bp)``. File and voice-memo GET return JSON
``{"url": "<signed url>"}`` only — no 302, no streamed bytes.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from flask import jsonify, request
from sqlalchemy import case
from sqlalchemy.orm import joinedload

from feature_flags import org_has_feature
from models import (
    Contact,
    ContactEmail,
    ContactFile,
    ContactVoiceMemo,
    Interaction,
    Task,
    Transaction,
    TransactionParticipant,
    UserEmailIntegration,
    db,
)
from routes.agent_api import (
    _contact_visible,
    _json_body,
    _json_error,
    _parse_date,
    _serialize_transaction,
    agent_api_bp,
    agent_jwt_required,
    transactions_flag_required,
)
from routes.contacts import (
    format_email,
    format_file,
    format_interaction,
    format_task,
    format_voice_memo,
    get_user_timezone,
)
from services import supabase_storage
from services.tenant_service import org_query_for_id

logger = logging.getLogger(__name__)

_registered = False

ACTIVITY_TYPES = frozenset({'call', 'email', 'text', 'meeting', 'other'})
TASK_STATUS_FILTERS = frozenset({'pending', 'completed', 'all'})
TIMELINE_FILTERS = frozenset({
    'all', 'interaction', 'email', 'task', 'file', 'voice_memo',
})
VOICE_MEMO_MAX_BYTES = 10 * 1024 * 1024


def _load_contact(user, contact_id):
    contact = org_query_for_id(Contact, user.organization_id).filter_by(
        id=contact_id,
    ).first()
    if not _contact_visible(user, contact):
        return None, _json_error('Contact not found.', 404)
    return contact, None


def _iso(value):
    return value.isoformat() if value is not None else None


def _serialize_contact_task(task):
    return {
        'id': task.id,
        'subject': task.subject,
        'status': task.status,
        'priority': task.priority,
        'due_date': _iso(task.due_date),
        'type': task.task_type.name if task.task_type else None,
        'subtype': task.task_subtype.name if task.task_subtype else None,
        'transaction_id': task.transaction_id,
        'description': task.description,
    }


def _serialize_contact_file(row):
    return {
        'id': row.id,
        'original_filename': row.original_filename,
        'file_type': row.file_type,
        'file_size': row.file_size,
        'created_at': _iso(row.created_at),
        'is_image': bool(row.is_image),
    }


def _serialize_voice_memo(memo):
    return {
        'id': memo.id,
        'created_at': _iso(memo.created_at),
        'duration_seconds': memo.duration_seconds,
        'transcription': memo.transcription,
        'transcription_status': memo.transcription_status,
    }


def _serialize_activity(interaction):
    return {
        'id': interaction.id,
        'type': interaction.type,
        'notes': interaction.notes,
        'date': _iso(interaction.date),
        'follow_up_date': _iso(interaction.follow_up_date),
        'created_at': _iso(interaction.created_at),
    }


def _apply_activity_dates(contact, activity_type, activity_date):
    if activity_type == 'call':
        if contact.last_phone_call_date is None or activity_date > contact.last_phone_call_date:
            contact.last_phone_call_date = activity_date
    elif activity_type == 'email':
        if contact.last_email_date is None or activity_date > contact.last_email_date:
            contact.last_email_date = activity_date
    elif activity_type == 'text':
        if contact.last_text_date is None or activity_date > contact.last_text_date:
            contact.last_text_date = activity_date
    elif activity_type in ('meeting', 'other'):
        if contact.last_contact_date is None or activity_date > contact.last_contact_date:
            contact.last_contact_date = activity_date
    contact.update_last_contact_date()


def _gmail_connected(user):
    integration = UserEmailIntegration.query.filter_by(user_id=user.id).first()
    return bool(integration and integration.sync_enabled)


def _signed_file_url(bucket, storage_path):
    url = supabase_storage.get_signed_url(bucket, storage_path, expires_in=3600)
    if not isinstance(url, str) or not url.strip():
        raise ValueError('empty signed url')
    return url


def _suggestions_allowed(user):
    if org_has_feature('AI_TASK_SUGGESTIONS', user.organization):
        return None
    return _json_error(
        'Task suggestions are not on this plan.',
        403,
        code='feature_required',
    )


@agent_jwt_required
def contact_desk_tasks(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    status = (request.args.get('status') or 'pending').strip().lower()
    if status not in TASK_STATUS_FILTERS:
        return _json_error('status must be pending, completed, or all.', 400)
    query = Task.query.options(
        joinedload(Task.task_type),
        joinedload(Task.task_subtype),
    ).filter_by(
        contact_id=contact.id,
        organization_id=user.organization_id,
    )
    if status != 'all':
        query = query.filter(Task.status == status)
    tasks = query.order_by(
        case((Task.status == 'pending', 0), else_=1),
        Task.due_date.asc(),
    ).all()
    return jsonify({'tasks': [_serialize_contact_task(task) for task in tasks]})


@agent_jwt_required
@transactions_flag_required
def contact_desk_transactions(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    tx_ids = [
        row.transaction_id
        for row in TransactionParticipant.query.filter_by(
            contact_id=contact.id,
            organization_id=user.organization_id,
        ).all()
    ]
    if not tx_ids:
        return jsonify({'transactions': []})
    txs = (
        org_query_for_id(Transaction, user.organization_id)
        .filter(Transaction.id.in_(tx_ids))
        .options(joinedload(Transaction.transaction_type))
        .order_by(Transaction.created_at.desc())
        .all()
    )
    return jsonify({
        'transactions': [_serialize_transaction(tx) for tx in txs],
    })


@agent_jwt_required
def contact_desk_files(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    rows = ContactFile.query.filter_by(
        contact_id=contact.id,
        organization_id=user.organization_id,
    ).order_by(ContactFile.created_at.desc()).all()
    return jsonify({'files': [_serialize_contact_file(row) for row in rows]})


@agent_jwt_required
def contact_desk_upload_file(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    uploaded = request.files.get('file')
    if uploaded is None or not uploaded.filename:
        return _json_error('Upload a file.', 400)
    if not ContactFile.allowed_file(uploaded.filename):
        allowed = ', '.join(sorted(ContactFile.ALLOWED_EXTENSIONS))
        return _json_error(f'File type not allowed. Allowed types: {allowed}', 400)
    file_data = uploaded.read()
    if len(file_data) > ContactFile.MAX_FILE_SIZE:
        max_mb = ContactFile.MAX_FILE_SIZE / (1024 * 1024)
        return _json_error(f'File too large. Maximum size is {max_mb:.0f}MB.', 400)
    try:
        result = supabase_storage.upload_contact_file(
            contact_id=contact.id,
            file_data=file_data,
            original_filename=uploaded.filename,
            content_type=uploaded.content_type,
        )
    except Exception:
        logger.exception('Agent contact desk file upload failed')
        return _json_error('Could not store that file.', 500)
    row = ContactFile(
        organization_id=user.organization_id,
        contact_id=contact.id,
        user_id=user.id,
        filename=result['filename'],
        original_filename=uploaded.filename,
        file_type=uploaded.content_type,
        file_size=result['size'],
        storage_path=result['path'],
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({'file': _serialize_contact_file(row)}), 201


@agent_jwt_required
def contact_desk_file_url(user, contact_id, file_id):
    """Return JSON ``{"url": "<signed url>"}``. Native downloads that URL."""
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    row = ContactFile.query.filter_by(
        id=file_id,
        contact_id=contact.id,
        organization_id=user.organization_id,
    ).first()
    if not row:
        return _json_error('File not found.', 404)
    if not row.storage_path:
        return _json_error('That file has no file yet.', 404)
    try:
        url = _signed_file_url(
            supabase_storage.CONTACT_FILES_BUCKET,
            row.storage_path,
        )
    except Exception:
        logger.exception('Agent contact desk signed file URL failed')
        return _json_error('Could not create a file link.', 502)
    return jsonify({'url': url})


@agent_jwt_required
def contact_desk_delete_file(user, contact_id, file_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    row = ContactFile.query.filter_by(
        id=file_id,
        contact_id=contact.id,
        organization_id=user.organization_id,
    ).first()
    if not row:
        return _json_error('File not found.', 404)
    try:
        if row.storage_path:
            supabase_storage.delete_file(
                supabase_storage.CONTACT_FILES_BUCKET,
                row.storage_path,
            )
        db.session.delete(row)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Agent contact desk file delete failed')
        return _json_error('Could not delete that file.', 500)
    return jsonify({'ok': True})


@agent_jwt_required
def contact_desk_voice_memos(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    memos = ContactVoiceMemo.query.filter_by(
        contact_id=contact.id,
        organization_id=user.organization_id,
    ).order_by(ContactVoiceMemo.created_at.desc()).all()
    return jsonify({
        'voice_memos': [_serialize_voice_memo(memo) for memo in memos],
    })


@agent_jwt_required
def contact_desk_upload_voice_memo(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    uploaded = request.files.get('file')
    if uploaded is None or not uploaded.filename:
        return _json_error('Upload an audio file.', 400)
    audio_data = uploaded.read()
    if len(audio_data) > VOICE_MEMO_MAX_BYTES:
        return _json_error('Audio file too large. Maximum size is 10MB.', 400)
    duration_seconds = request.form.get('duration', type=int)
    filename = uploaded.filename or 'memo.webm'
    try:
        result = supabase_storage.upload_voice_memo(
            contact_id=contact.id,
            file_data=audio_data,
            original_filename=filename,
            content_type=uploaded.content_type or 'audio/webm',
        )
    except Exception:
        logger.exception('Agent contact desk voice memo upload failed')
        return _json_error('Could not store that voice memo.', 500)
    memo = ContactVoiceMemo(
        organization_id=user.organization_id,
        contact_id=contact.id,
        user_id=user.id,
        storage_path=result['path'],
        file_name=result['filename'],
        duration_seconds=duration_seconds,
        file_size=result['size'],
        transcription_status='pending',
    )
    db.session.add(memo)
    db.session.commit()
    try:
        from services.ai_service import transcribe_audio
        transcription = transcribe_audio(audio_data=audio_data, filename=filename)
        memo.transcription = transcription
        memo.transcription_status = 'completed'
        db.session.commit()
    except Exception as transcribe_error:
        logger.warning(
            'Transcription failed for memo %s: %s',
            memo.id,
            transcribe_error,
        )
        memo.transcription_status = 'failed'
        db.session.commit()
    return jsonify({'voice_memo': _serialize_voice_memo(memo)}), 201


@agent_jwt_required
def contact_desk_voice_memo_url(user, contact_id, memo_id):
    """Return JSON ``{"url": "<signed url>"}``. Native plays that URL."""
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    memo = ContactVoiceMemo.query.filter_by(
        id=memo_id,
        contact_id=contact.id,
        organization_id=user.organization_id,
    ).first()
    if not memo:
        return _json_error('Voice memo not found.', 404)
    if not memo.storage_path:
        return _json_error('That voice memo has no file yet.', 404)
    try:
        url = supabase_storage.get_voice_memo_url(memo.storage_path, expires_in=3600)
    except Exception:
        logger.exception('Agent contact desk signed voice-memo URL failed')
        return _json_error('Could not create a file link.', 502)
    if not isinstance(url, str) or not url.strip():
        return _json_error('Could not create a file link.', 502)
    return jsonify({'url': url})


@agent_jwt_required
def contact_desk_delete_voice_memo(user, contact_id, memo_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    memo = ContactVoiceMemo.query.filter_by(
        id=memo_id,
        contact_id=contact.id,
        organization_id=user.organization_id,
    ).first()
    if not memo:
        return _json_error('Voice memo not found.', 404)
    try:
        if memo.storage_path:
            supabase_storage.delete_voice_memo(memo.storage_path)
        db.session.delete(memo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Agent contact desk voice memo delete failed')
        return _json_error('Could not delete that voice memo.', 500)
    return jsonify({'ok': True})


def _timeline_payload(user, contact, filter_type, page, per_page):
    activities = []
    counts = {
        'all': 0,
        'interaction': 0,
        'email': 0,
        'task': 0,
        'file': 0,
        'voice_memo': 0,
    }

    if filter_type in ('all', 'interaction'):
        interactions = Interaction.query.filter_by(contact_id=contact.id).all()
        counts['interaction'] = len(interactions)
        activities.extend(format_interaction(row) for row in interactions)
    else:
        counts['interaction'] = Interaction.query.filter_by(
            contact_id=contact.id,
        ).count()

    if filter_type in ('all', 'email'):
        emails = ContactEmail.query.filter_by(
            contact_id=contact.id,
            user_id=user.id,
        ).all()
        counts['email'] = len(emails)
        activities.extend(format_email(row) for row in emails)
    else:
        counts['email'] = ContactEmail.query.filter_by(
            contact_id=contact.id,
            user_id=user.id,
        ).count()

    if filter_type in ('all', 'task'):
        tasks = Task.query.filter_by(contact_id=contact.id).all()
        task_count = 0
        for task in tasks:
            activities.append(format_task(task, 'created'))
            task_count += 1
            if task.status == 'completed' and task.completed_at:
                activities.append(format_task(task, 'completed'))
                task_count += 1
        counts['task'] = task_count
    else:
        task_count = Task.query.filter_by(contact_id=contact.id).count()
        completed_count = Task.query.filter_by(
            contact_id=contact.id,
            status='completed',
        ).count()
        counts['task'] = task_count + completed_count

    if filter_type in ('all', 'file'):
        files = ContactFile.query.filter_by(contact_id=contact.id).all()
        counts['file'] = len(files)
        activities.extend(format_file(row) for row in files)
    else:
        counts['file'] = ContactFile.query.filter_by(contact_id=contact.id).count()

    if filter_type in ('all', 'voice_memo'):
        memos = ContactVoiceMemo.query.filter_by(
            contact_id=contact.id,
            organization_id=user.organization_id,
        ).all()
        counts['voice_memo'] = len(memos)
        activities.extend(format_voice_memo(memo) for memo in memos)
    else:
        counts['voice_memo'] = ContactVoiceMemo.query.filter_by(
            contact_id=contact.id,
            organization_id=user.organization_id,
        ).count()

    counts['all'] = (
        counts['interaction']
        + counts['email']
        + counts['task']
        + counts['file']
        + counts['voice_memo']
    )
    activities.sort(key=lambda item: item['timestamp'] or '', reverse=True)
    total = len(activities)
    start = (page - 1) * per_page
    return {
        'activities': activities[start:start + per_page],
        'counts': counts,
        'page': page,
        'per_page': per_page,
        'total': total,
    }


@agent_jwt_required
def contact_desk_timeline(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    filter_type = (request.args.get('filter') or 'all').strip()
    if filter_type not in TIMELINE_FILTERS:
        filter_type = 'all'
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = request.args.get('per_page', 20, type=int) or 20
    per_page = min(max(per_page, 1), 50)
    return jsonify(_timeline_payload(user, contact, filter_type, page, per_page))


@agent_jwt_required
def contact_desk_log_activity(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    data = _json_body()
    activity_type = (data.get('type') or '').strip().lower()
    if not activity_type:
        return _json_error('Activity type is required.', 400)
    if activity_type not in ACTIVITY_TYPES:
        return _json_error(
            'Activity type must be call, email, text, meeting, or other.',
            400,
        )
    notes = (data.get('notes') or '').strip() or None
    try:
        activity_date = _parse_date(data.get('date'))
        follow_up_date = _parse_date(data.get('follow_up_date'))
    except ValueError as exc:
        return _json_error(str(exc), 400)
    user_tz = get_user_timezone()
    if activity_date is None:
        activity_date = datetime.now(user_tz).date()
    activity_dt = user_tz.localize(datetime.combine(activity_date, datetime.min.time()))
    follow_up_dt = None
    if follow_up_date is not None:
        follow_up_dt = user_tz.localize(
            datetime.combine(follow_up_date, datetime.min.time()),
        )
    interaction = Interaction(
        organization_id=user.organization_id,
        contact_id=contact.id,
        user_id=user.id,
        type=activity_type,
        notes=notes,
        date=activity_dt,
        follow_up_date=follow_up_dt,
    )
    db.session.add(interaction)
    _apply_activity_dates(contact, activity_type, activity_date)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Agent contact desk activity log failed')
        return _json_error('Could not log that activity.', 500)
    return jsonify({'activity': _serialize_activity(interaction)}), 201


@agent_jwt_required
def contact_desk_emails(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    if not _gmail_connected(user):
        return jsonify({'emails': [], 'connected': False})
    from services import gmail_service
    emails = gmail_service.get_email_threads_for_contact(contact.id, user.id)
    return jsonify({'emails': emails, 'connected': True})


@agent_jwt_required
def contact_desk_email_thread(user, contact_id, thread_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    emails = ContactEmail.query.filter_by(
        contact_id=contact.id,
        user_id=user.id,
        gmail_thread_id=thread_id,
    ).order_by(ContactEmail.sent_at.asc()).all()
    if not emails:
        return _json_error('Email thread not found.', 404)
    return jsonify({'emails': [row.to_dict() for row in emails]})


@agent_jwt_required
def contact_desk_task_suggestions(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    blocked = _suggestions_allowed(user)
    if blocked:
        return blocked
    from services.task_suggestions import generate_task_suggestions
    try:
        suggestions = generate_task_suggestions(
            contact_id=contact.id,
            org_id=user.organization_id,
            user_id=user.id,
        )
    except ValueError as exc:
        return _json_error(str(exc), 422)
    except Exception:
        logger.exception('Agent contact desk task suggestions failed')
        return _json_error('Could not generate suggestions right now.', 500)
    return jsonify({'suggestions': suggestions})


@agent_jwt_required
def contact_desk_create_suggested_task(user, contact_id):
    contact, error = _load_contact(user, contact_id)
    if error:
        return error
    blocked = _suggestions_allowed(user)
    if blocked:
        return blocked
    data = _json_body()
    subject = (data.get('subject') or '').strip()
    if not subject:
        return _json_error('Subject is required.', 400)
    from services.task_suggestions import resolve_type_ids
    suggestion = {
        'task_type': data.get('task_type') or 'Call',
        'task_subtype': data.get('task_subtype') or 'Follow-up',
    }
    task_type, task_subtype = resolve_type_ids(suggestion, user.organization_id)
    if not task_type or not task_subtype:
        return _json_error('Could not resolve task type.', 400)
    try:
        due_in_days = int(data.get('due_in_days', 3))
    except (TypeError, ValueError):
        due_in_days = 3
    if due_in_days < 1:
        due_in_days = 3
    priority = (data.get('priority') or 'medium').strip().lower()
    if priority not in ('low', 'medium', 'high'):
        priority = 'medium'
    from routes.tasks import convert_to_utc, get_user_timezone as task_timezone
    user_tz = task_timezone()
    due_local = datetime.now(user_tz) + timedelta(days=due_in_days)
    due_local = datetime.combine(due_local.date(), time(23, 59, 59))
    utc_due_date = convert_to_utc(due_local, user_tz)
    description = (data.get('description') or '').strip()[:500] or None
    task = Task(
        organization_id=user.organization_id,
        contact_id=contact.id,
        assigned_to_id=user.id,
        created_by_id=user.id,
        type_id=task_type.id,
        subtype_id=task_subtype.id,
        subject=subject[:200],
        description=description,
        priority=priority,
        due_date=utc_due_date,
    )
    db.session.add(task)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Agent contact desk suggested task create failed')
        return _json_error('Could not create that task.', 500)
    try:
        from services import calendar_service
        integration = UserEmailIntegration.query.filter_by(user_id=user.id).first()
        if integration and integration.calendar_sync_enabled:
            calendar_service.sync_task_to_calendar(task, request.url_root.rstrip('/'))
    except Exception:
        logger.warning('Calendar sync failed for suggested task %s', task.id)
    return jsonify({'task': _serialize_contact_task(task)}), 201


def register(bp):
    """Attach all contact-desk routes to the agent_api blueprint."""
    global _registered
    if _registered:
        return
    _registered = True
    rules = (
        ('/contacts/<int:contact_id>/tasks', ['GET'], contact_desk_tasks),
        (
            '/contacts/<int:contact_id>/transactions',
            ['GET'],
            contact_desk_transactions,
        ),
        ('/contacts/<int:contact_id>/files', ['GET'], contact_desk_files),
        ('/contacts/<int:contact_id>/files', ['POST'], contact_desk_upload_file),
        (
            '/contacts/<int:contact_id>/files/<int:file_id>/file',
            ['GET'],
            contact_desk_file_url,
        ),
        (
            '/contacts/<int:contact_id>/files/<int:file_id>',
            ['DELETE'],
            contact_desk_delete_file,
        ),
        (
            '/contacts/<int:contact_id>/voice-memos',
            ['GET'],
            contact_desk_voice_memos,
        ),
        (
            '/contacts/<int:contact_id>/voice-memos',
            ['POST'],
            contact_desk_upload_voice_memo,
        ),
        (
            '/contacts/<int:contact_id>/voice-memos/<int:memo_id>/file',
            ['GET'],
            contact_desk_voice_memo_url,
        ),
        (
            '/contacts/<int:contact_id>/voice-memos/<int:memo_id>',
            ['DELETE'],
            contact_desk_delete_voice_memo,
        ),
        ('/contacts/<int:contact_id>/timeline', ['GET'], contact_desk_timeline),
        ('/contacts/<int:contact_id>/activity', ['POST'], contact_desk_log_activity),
        ('/contacts/<int:contact_id>/emails', ['GET'], contact_desk_emails),
        (
            '/contacts/<int:contact_id>/emails/<thread_id>',
            ['GET'],
            contact_desk_email_thread,
        ),
        (
            '/contacts/<int:contact_id>/task-suggestions',
            ['POST'],
            contact_desk_task_suggestions,
        ),
        (
            '/contacts/<int:contact_id>/task-suggestions/create',
            ['POST'],
            contact_desk_create_suggested_task,
        ),
    )
    for rule, methods, view in rules:
        bp.add_url_rule(rule, view.__name__, view, methods=methods)


# Safe self-register if this module is imported from app.
# Agent A will also call register(). Make register() idempotent.
register(agent_api_bp)
