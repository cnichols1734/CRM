"""
Daily Briefing routes — page + JSON APIs + scoped chat stream.

Replaces the legacy sync daily-todo modal endpoints.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, time
from functools import wraps

from flask import (
    Blueprint, jsonify, request, render_template, session,
    Response, current_app, stream_with_context,
)
from flask_login import login_required, current_user

from models import db, Contact, Task, DailyTodoList
from feature_flags import org_has_feature
from services.daily_briefing import (
    today_local,
    serialize_briefing,
    run_briefing_generation,
    CHAT_SYSTEM_PROMPT,
    build_chat_context,
)
from services.ai_service import stream_chat_response

logger = logging.getLogger(__name__)

daily_todo = Blueprint('daily_todo', __name__)


def briefing_feature_required(f):
    """JSON-friendly feature gate for Daily Briefing APIs and page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            if request.path.startswith('/api/'):
                return jsonify({"error": "Authentication required"}), 401
            from flask import redirect, url_for
            return redirect(url_for('auth.login'))

        if not org_has_feature('AI_DAILY_TODO'):
            if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
                return jsonify({
                    "error": "Daily Briefing requires a Pro subscription.",
                    "upgrade": True,
                }), 403
            from flask import flash, redirect, url_for
            flash('Daily Briefing requires a subscription upgrade.', 'warning')
            try:
                return redirect(url_for('org.upgrade'))
            except Exception:
                return jsonify({"error": "Feature unavailable"}), 403
        return f(*args, **kwargs)
    return decorated


def _start_generation(briefing: DailyTodoList):
    """Kick a background thread to fill a generating row."""
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=run_briefing_generation,
        args=(app, briefing.id, current_user.id, current_user.organization_id),
        daemon=True,
    )
    thread.start()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@daily_todo.route('/briefing')
@login_required
@briefing_feature_required
def briefing_page():
    """Dedicated Daily Briefing page."""
    plan_date = today_local()
    row = DailyTodoList.get_for_user_date(current_user.id, plan_date)

    if row and row.status == 'ready' and not row.viewed_at:
        row.viewed_at = datetime.utcnow()
        db.session.commit()

    # Reset chat history when opening a new day's briefing
    if row and session.get('briefing_chat_plan_date') != plan_date.isoformat():
        session['briefing_chat_history'] = []
        session['briefing_chat_plan_date'] = plan_date.isoformat()

    return render_template(
        'briefing/index.html',
        plan_date=plan_date,
        briefing=serialize_briefing(row) if row else None,
    )


# ---------------------------------------------------------------------------
# JSON APIs
# ---------------------------------------------------------------------------

@daily_todo.route('/api/daily-briefing/today', methods=['GET'])
@login_required
@briefing_feature_required
def get_today_briefing():
    """Return today's briefing (any status), or 404 if none."""
    row = DailyTodoList.get_for_user_date(current_user.id, today_local())
    if not row:
        return jsonify({"error": "No briefing for today", "status": "missing"}), 404
    return jsonify(serialize_briefing(row))


@daily_todo.route('/api/daily-briefing/generate', methods=['POST'])
@login_required
@briefing_feature_required
def generate_briefing():
    """
    Idempotent kickoff. Creates a `generating` row if needed and starts
    background generation. Returns immediately with current status.
    """
    data = request.get_json(silent=True) or {}
    force = bool(data.get('force'))
    plan_date = today_local()
    row = DailyTodoList.get_for_user_date(current_user.id, plan_date)

    if row and row.status == 'ready' and not force:
        return jsonify(serialize_briefing(row))

    if row and row.status == 'generating' and not force:
        # Stale generating (>3 min) — retry
        age = (datetime.utcnow() - (row.updated_at or row.generated_at)).total_seconds()
        if age < 180:
            return jsonify(serialize_briefing(row))

    if row and (force or row.status in ('failed', 'generating')):
        row.status = 'generating'
        row.error = None
        row.todo_content = row.todo_content or {}
        row.updated_at = datetime.utcnow()
        db.session.commit()
        _start_generation(row)
        return jsonify(serialize_briefing(row)), 202

    # Fresh row
    row = DailyTodoList(
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        plan_date=plan_date,
        status='generating',
        todo_content={},
        item_states={},
        generated_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.commit()
    _start_generation(row)
    return jsonify(serialize_briefing(row)), 202


@daily_todo.route('/api/daily-briefing/banner-later', methods=['POST'])
@login_required
@briefing_feature_required
def banner_later():
    """Hide the dashboard banner for the rest of today."""
    row = DailyTodoList.get_for_user_date(current_user.id, today_local())
    if not row:
        return jsonify({"error": "No briefing"}), 404
    row.mark_banner_later()
    db.session.commit()
    return jsonify({"ok": True})


@daily_todo.route('/api/daily-briefing/item-state', methods=['POST'])
@login_required
@briefing_feature_required
def set_item_state():
    """Mark a briefing item done or dismissed (or clear)."""
    data = request.get_json(silent=True) or {}
    item_id = (data.get('item_id') or '').strip()
    state = (data.get('state') or '').strip()  # done | dismissed | ''
    if not item_id:
        return jsonify({"error": "item_id required"}), 400
    if state not in ('done', 'dismissed', ''):
        return jsonify({"error": "Invalid state"}), 400

    row = DailyTodoList.get_for_user_date(current_user.id, today_local())
    if not row:
        return jsonify({"error": "No briefing"}), 404

    states = dict(row.item_states or {})
    if state:
        states[item_id] = state
    else:
        states.pop(item_id, None)
    row.item_states = states
    db.session.commit()
    return jsonify({"ok": True, "item_states": states})


@daily_todo.route('/api/daily-briefing/create-task', methods=['POST'])
@login_required
@briefing_feature_required
def create_task_from_briefing():
    """Create a CRM task from a briefing priority / reconnect item."""
    from services.task_suggestions import resolve_type_ids
    from routes.tasks import get_user_timezone, convert_to_utc

    data = request.get_json(silent=True) or {}
    contact_id = data.get('contact_id')
    subject = (data.get('subject') or '').strip()
    description = (data.get('description') or '').strip()
    action_type = (data.get('action_type') or 'call').lower()
    priority = (data.get('priority') or 'medium').lower()
    if priority not in ('low', 'medium', 'high'):
        priority = 'medium'

    if not contact_id or not subject:
        return jsonify({"error": "contact_id and subject required"}), 400

    contact = Contact.query.filter_by(
        id=contact_id,
        organization_id=current_user.organization_id,
    ).first()
    if not contact:
        return jsonify({"error": "Contact not found"}), 404

    type_map = {
        'call': ('Call', 'Follow-up'),
        'text': ('Text', 'Follow-up'),
        'email': ('Email', 'Follow-up'),
    }
    type_name, subtype_name = type_map.get(action_type, ('Call', 'Follow-up'))
    suggestion = {'task_type': type_name, 'task_subtype': subtype_name}
    task_type, task_subtype = resolve_type_ids(
        suggestion, current_user.organization_id
    )
    if not task_type or not task_subtype:
        # Fall back to any available type/subtype for the org
        from models import TaskType, TaskSubtype
        task_type = TaskType.query.filter_by(
            organization_id=current_user.organization_id
        ).first()
        task_subtype = (
            TaskSubtype.query.filter_by(
                organization_id=current_user.organization_id,
                task_type_id=task_type.id,
            ).first()
            if task_type else None
        )
    if not task_type or not task_subtype:
        return jsonify({"error": "Could not resolve task type"}), 400

    user_tz = get_user_timezone()
    due_local = datetime.now(user_tz)
    due_local = datetime.combine(due_local.date(), time(23, 59, 59))
    utc_due = convert_to_utc(due_local, user_tz)

    task = Task(
        organization_id=current_user.organization_id,
        contact_id=contact.id,
        assigned_to_id=current_user.id,
        created_by_id=current_user.id,
        type_id=task_type.id,
        subtype_id=task_subtype.id,
        subject=subject[:200],
        description=description[:500] or None,
        priority=priority,
        due_date=utc_due,
    )
    db.session.add(task)

    # Optionally mark the briefing item done
    item_id = (data.get('item_id') or '').strip()
    if item_id:
        row = DailyTodoList.get_for_user_date(current_user.id, today_local())
        if row:
            states = dict(row.item_states or {})
            states[item_id] = 'done'
            row.item_states = states

    db.session.commit()
    return jsonify({
        "ok": True,
        "task": {"id": task.id, "subject": task.subject},
    })


@daily_todo.route('/api/daily-briefing/chat/stream', methods=['POST'])
@login_required
@briefing_feature_required
def briefing_chat_stream():
    """SSE stream for briefing-scoped B.O.B. chat."""
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"error": "message required"}), 400

    row = DailyTodoList.get_for_user_date(current_user.id, today_local())
    if not row or row.status != 'ready':
        return jsonify({"error": "Briefing not ready yet"}), 409

    history = session.get('briefing_chat_history') or []
    # Keep last 10 exchanges
    history = history[-20:]

    history_block = ""
    for turn in history:
        role = turn.get('role', 'user')
        content = turn.get('content', '')
        history_block += f"\n{role.upper()}: {content}"

    briefing_json = build_chat_context(row)
    user_prompt = (
        f"Agent: {current_user.first_name or 'Agent'}\n"
        f"Today's briefing JSON:\n{briefing_json}\n\n"
        f"Conversation so far:{history_block or ' (none)'}\n\n"
        f"USER: {message}"
    )

    assistant_holder = {'text': ''}

    def generate_and_save():
        full = []
        try:
            for chunk in stream_chat_response(
                system_prompt=CHAT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            ):
                full.append(chunk)
                escaped = (
                    chunk.replace('\\', '\\\\')
                    .replace('\n', '\\n')
                    .replace('\r', '\\r')
                )
                yield f"data: {escaped}\n\n"
            yield "data: [DONE]\n\n"
            full_text = ''.join(full)
            assistant_holder['text'] = full_text
            # Client accumulates during the stream; trailer is optional metadata.
            escaped_full = (
                full_text.replace('\\', '\\\\')
                .replace('\n', '\\n')
                .replace('\r', '\\r')
            )
            yield f"data: [FULL_RESPONSE]{escaped_full}[/FULL_RESPONSE]\n\n"
        except Exception as e:
            logger.exception(f"Briefing chat stream error: {e}")
            yield "data: Sorry, something went wrong. Please try again.\n\n"
            yield "data: [DONE]\n\n"

        try:
            hist = list(session.get('briefing_chat_history') or [])
            hist.append({'role': 'user', 'content': message})
            if assistant_holder['text']:
                hist.append({
                    'role': 'assistant',
                    'content': assistant_holder['text'],
                })
            session['briefing_chat_history'] = hist[-20:]
            session.modified = True
        except Exception:
            pass

    return Response(
        stream_with_context(generate_and_save()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


# ---------------------------------------------------------------------------
# Legacy endpoints — kept as thin redirects so old tests / bookmarks don't 404
# ---------------------------------------------------------------------------

@daily_todo.route('/api/daily-todo/generate', methods=['POST'])
@login_required
@briefing_feature_required
def generate_todo_legacy():
    return generate_briefing()


@daily_todo.route('/api/daily-todo/latest', methods=['GET'])
@login_required
@briefing_feature_required
def get_latest_todo_legacy():
    row = DailyTodoList.get_latest_for_user(current_user.id)
    if not row:
        return jsonify({"error": "No todo list found"}), 404
    return jsonify(serialize_briefing(row))
