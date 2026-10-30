"""Native BOB transport, authenticated with AgentDesk's bearer session."""
from datetime import datetime
from functools import wraps
import hashlib
import json
import logging
from uuid import UUID

from flask import g, jsonify, request
from redis import Redis

from config import Config
from feature_flags import org_has_feature
from models import BobAction, ChatConversation, ChatMessage, db
from routes.agent_api import agent_jwt_required
from services.ai_service import run_tool_conversation
from services.bob_tools import BobContext, dispatch, openai_tool_schemas, confirm_action, reject_action, undo_action, TOOLS_BY_NAME
from services.bob_tools.notifications import ActionCollector, flush

logger = logging.getLogger(__name__)


def register(bp):
    bp.add_url_rule('/bob/conversations', 'bob_conversations', conversations, methods=['GET', 'POST'])
    bp.add_url_rule('/bob/conversations/<int:cid>', 'bob_conversation', conversation, methods=['GET'])
    bp.add_url_rule('/bob/conversations/<int:cid>/messages', 'bob_message', message, methods=['POST'])
    bp.add_url_rule('/bob/actions/<int:aid>', 'bob_action', action, methods=['POST'])


def bob_required(view):
    @agent_jwt_required
    @wraps(view)
    def wrapped(user, *args, **kwargs):
        if not org_has_feature('AI_CHAT', user.organization):
            return jsonify(error='BOB is not available on this plan.'), 403
        # Existing BOB rate limits read Flask-Login identity. Never create a cookie login.
        g._login_user = user
        return view(user, *args, **kwargs)
    return wrapped


def scope(user):
    return ChatConversation.query.filter_by(user_id=user.id, organization_id=user.organization_id)


def preview_text(value, indent=0):
    if isinstance(value, dict):
        return '\n'.join(' ' * indent + str(k).replace('_', ' ').capitalize() + ': ' +
                         ('\n' + preview_text(v, indent + 2) if isinstance(v, (dict, list)) else preview_text(v))
                         for k, v in value.items())
    if isinstance(value, list):
        return '\n'.join(' ' * indent + '• ' + preview_text(v, indent + 2) for v in value)
    if value is None:
        return 'None'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value)


def serialize(conv, user):
    data = conv.to_dict(include_messages=True)
    actions = BobAction.query.filter_by(conversation_id=conv.id, user_id=user.id,
                                       organization_id=user.organization_id).order_by(BobAction.id.desc()).limit(30).all()
    data['actions'] = [{**a.to_dict(), 'undoable': bool(TOOLS_BY_NAME.get(a.tool_name) and TOOLS_BY_NAME[a.tool_name].undo and a.status == 'executed'),
                        'preview_text': preview_text(a.preview) if a.preview else None}
                       for a in actions]
    return data


@bob_required
def conversations(user):
    if request.method == 'POST':
        conv = ChatConversation(user_id=user.id, organization_id=user.organization_id, channel='ios')
        db.session.add(conv)
        db.session.commit()
        return jsonify(serialize(conv, user)), 201
    rows = scope(user).order_by(ChatConversation.updated_at.desc()).limit(100).all()
    return jsonify(conversations=[c.to_dict() for c in rows])


@bob_required
def conversation(user, cid):
    conv = scope(user).filter_by(id=cid).first()
    if not conv:
        return jsonify(error='Conversation not found.'), 404
    return jsonify(serialize(conv, user))


def receipt_store():
    return Redis.from_url(Config.REDIS_URL, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)


@bob_required
def message(user, cid):
    from routes.ai_chat import SYSTEM_PROMPT, check_rate_limit, hydrate_page_entity
    conv = scope(user).filter_by(id=cid).first()
    if not conv:
        return jsonify(error='Conversation not found.'), 404
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(error='Send a JSON object.'), 400
    text = body.get('message')
    if not isinstance(text, str) or not text.strip() or len(text) > 12000:
        return jsonify(error='Enter a message of up to 12,000 characters.'), 400
    text = text.strip()
    try:
        rid = str(UUID(str(body.get('request_id', ''))))
    except ValueError:
        return jsonify(error='A valid request_id is required.'), 400
    key = f'bob:ios:turn:{user.organization_id}:{user.id}:{rid}'
    digest = hashlib.sha256(f'{cid}:{text}'.encode()).hexdigest()
    lock = f'bob:ios:conversation:{user.organization_id}:{user.id}:{cid}'
    try:
        store = receipt_store()
        previous = store.get(key)
        if previous:
            previous = json.loads(previous)
            if previous['digest'] != digest:
                return jsonify(error='This request ID belongs to another message.'), 409
            if previous.get('complete'):
                return jsonify(serialize(conv, user))
            return jsonify(error='This message is processing or was interrupted. Refresh the conversation before sending another message.'), 409
        if not check_rate_limit()[0]:
            return jsonify(error='Your daily BOB message limit has been reached.'), 429
        if not store.set(lock, rid, nx=True, ex=180):
            return jsonify(error='BOB is still responding in this conversation.'), 409
        if not store.set(key, json.dumps({'digest': digest}), nx=True, ex=604800):
            store.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end", 1, lock, rid)
            return jsonify(error='This message has already been submitted. Refresh the conversation.'), 409
    except Exception:
        logger.exception('Native BOB receipt store unavailable')
        return jsonify(error='BOB is temporarily unavailable. Your message has not been sent.'), 503

    try:
        history = conv.messages.order_by(None).order_by(ChatMessage.id.desc()).limit(20).all()[::-1]
        messages = [{'role': m.role, 'content': m.content} for m in history if m.role in ('user', 'assistant')]
        messages.append({'role': 'user', 'content': text})
        db.session.add(ChatMessage(conversation_id=cid, role='user', content=text))
        conv.title = conv.title or text[:90]
        conv.updated_at = datetime.utcnow()
        db.session.commit()
        entity = hydrate_page_entity('transaction', conv.transaction_id) if conv.transaction_id else None
        ctx = BobContext.from_user(user, surface='bob_ios', page_entity=entity)
        collector = ActionCollector()
        parts = []

        def execute(name, args):
            result = dispatch(name, args, ctx, conversation_id=cid, collector=collector)
            return result.for_model(), result.for_client()

        try:
            for event, payload in run_tool_conversation(
                system_prompt=SYSTEM_PROMPT + f'\nToday: {ctx.today().isoformat()}. You are in the native AgentDesk app.',
                messages=messages, tools=openai_tool_schemas(ctx), execute_tool=execute,
                safety_identifier=f'org:{user.organization_id}:user:{user.id}',
            ):
                if event == 'text':
                    parts.append(payload)
                elif event == 'error':
                    parts.append('BOB could not finish this response. Review any actions below before trying again.')
        except Exception:
            logger.exception('Native BOB response interrupted')
            parts.append('BOB could not finish this response. Review any actions below before trying again.')
        flush(collector, ctx)
        reply = ''.join(parts).strip() or 'No response was returned. Review any actions below before trying again.'
        db.session.add(ChatMessage(conversation_id=cid, role='assistant', content=reply))
        conv.updated_at = datetime.utcnow()
        db.session.commit()
        store.set(key, json.dumps({'digest': digest, 'complete': True}), ex=604800)
        return jsonify(serialize(conv, user))
    except Exception:
        db.session.rollback()
        logger.exception('Native BOB turn failed')
        return jsonify(error='The connection was interrupted. Refresh this conversation to check its messages and actions.'), 500
    finally:
        try:
            store.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end", 1, lock, rid)
        except Exception:
            logger.warning('Native BOB lock release failed', exc_info=True)


@bob_required
def action(user, aid):
    row = BobAction.query.filter_by(id=aid, organization_id=user.organization_id, user_id=user.id).first()
    if not row:
        return jsonify(error='Action not found.'), 404
    decision = (request.get_json(silent=True) or {}).get('decision')
    operation = {'approve': confirm_action, 'reject': reject_action, 'undo': undo_action}.get(decision)
    if operation is None:
        return jsonify(error='Choose approve, reject, or undo.'), 400
    result = operation(aid, BobContext.from_user(user, surface='bob_ios'))
    return jsonify(ok=result.ok, summary=result.summary, error=result.error), 200 if result.ok else 400
