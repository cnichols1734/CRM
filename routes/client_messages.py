"""Agent workspace for client inquiries and transaction conversations."""
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload, noload
from werkzeug.exceptions import HTTPException, InternalServerError

from feature_flags import can_access_transactions
from models import (db, User, Organization, AuditEvent, ClientBrowseAccount, ClientBrowseInquiry,
                    ClientInquiryMessage, PortalMessage, Transaction, TransactionParticipant)
from routes.client_discovery import org_context
from services.device_push import enqueue_portal_push
from services.client_conversations import inbox_admin, agent_name, assign_client, append_reply
from services.portal_service import CLIENT_PORTAL_ROLES
from services.transaction_auth import CAP_SEND_COMMS, has_capability, transactions_visible_query


client_messages_bp = Blueprint('client_messages', __name__)


def _wants_json():
    return request.args.get('format') == 'json' or request.accept_mimetypes.best == 'application/json'


@client_messages_bp.before_request
def _require_json_session():
    if _wants_json() and not current_user.is_authenticated:
        return jsonify(ok=False, error='Your session ended. Sign in to continue.'), 401


@client_messages_bp.after_request
def _private_response(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Accept')
    return response


@client_messages_bp.errorhandler(HTTPException)
def _http_error(error):
    if not _wants_json():
        return error
    messages = {
        400: 'We could not submit this request. Refresh and try again.',
        403: 'You no longer have permission to reply to this conversation.',
        404: 'This conversation is no longer available to you.',
        409: 'This conversation changed. Try again with its current assignment.',
    }
    description = error.description if error.description != type(error).description else messages.get(error.code, 'The request could not be completed.')
    return jsonify(ok=False, error=description), error.code


@client_messages_bp.errorhandler(Exception)
def _unexpected_error(error):
    if not _wants_json():
        current_app.logger.exception('Could not load the messages workspace')
        return InternalServerError()
    db.session.rollback()
    current_app.logger.exception('Could not update the messages workspace')
    return jsonify(ok=False, error='Messages could not connect. Try again in a moment.'), 500


def _threads():
    org_id = current_user.organization_id
    latest_inquiry = db.session.query(
        ClientInquiryMessage.inquiry_id.label('inquiry_id'),
        ClientInquiryMessage.body.label('body'),
        ClientInquiryMessage.sender.label('sender'),
        ClientInquiryMessage.created_at.label('created_at'),
        func.row_number().over(partition_by=ClientInquiryMessage.inquiry_id,
            order_by=ClientInquiryMessage.id.desc()).label('position'),
    ).filter(ClientInquiryMessage.organization_id == org_id).subquery()
    inquiries = db.session.query(ClientBrowseInquiry, ClientBrowseAccount, User,
        latest_inquiry.c.body, latest_inquiry.c.sender, latest_inquiry.c.created_at).join(
        ClientBrowseAccount, ClientBrowseAccount.id == ClientBrowseInquiry.account_id,
    ).outerjoin(User, and_(User.id == ClientBrowseInquiry.agent_id, User.organization_id == org_id)).outerjoin(
        latest_inquiry, and_(latest_inquiry.c.inquiry_id == ClientBrowseInquiry.id,
                            latest_inquiry.c.position == 1),
    ).filter(ClientBrowseInquiry.organization_id == org_id,
             ClientBrowseAccount.organization_id == org_id).options(noload(ClientBrowseInquiry.messages))
    is_admin = inbox_admin(current_user)
    if not is_admin:
        inquiries = inquiries.filter((ClientBrowseInquiry.agent_id == current_user.id) | ClientBrowseInquiry.agent_id.is_(None))
    rows = []
    for inquiry, account, assigned, last_body, last_sender, last_at in inquiries.all():
        snapshot = inquiry.listing_snapshot or {}
        sender = last_sender or ('agent' if inquiry.reply else 'client')
        rows.append(dict(key=f'inquiry-{inquiry.id}', kind='inquiry', name=account.name,
                         email=account.email, subject=(f"{snapshot['street']}, {snapshot.get('city', '')} · Repliers sample" if snapshot.get('street') else f'Sample home {inquiry.listing_id}'
                         if inquiry.listing_id else 'General inquiry'),
                         label='Showing request' if inquiry.kind == 'showing' else 'Home inquiry',
                         preview=last_body if last_body is not None else inquiry.reply or inquiry.body,
                         at=last_at or inquiry.created_at, last_sender=sender,
                         attention=sender == 'client', row=inquiry, account=account,
                         agent_name=agent_name(assigned), unassigned=inquiry.agent_id is None,
                         can_reply=inquiry.agent_id is not None and (is_admin or inquiry.agent_id == current_user.id)))
    if can_access_transactions(current_user):
        txs = transactions_visible_query(current_user)
        if is_admin:
            txs = Transaction.query.filter_by(organization_id=org_id)
        activity = db.session.query(
            PortalMessage.participant_id.label('participant_id'),
            PortalMessage.id.label('latest_id'),
            func.row_number().over(partition_by=PortalMessage.participant_id,
                order_by=(PortalMessage.created_at.desc(), PortalMessage.id.desc())).label('position'),
            func.sum(case(((PortalMessage.sender == 'client') &
                           PortalMessage.read_by_agent_at.is_(None), 1), else_=0)).over(
                               partition_by=PortalMessage.participant_id).label('unread'),
        ).filter(PortalMessage.organization_id == org_id,
                 PortalMessage.transaction_id.in_(txs.with_entities(Transaction.id))).subquery()
        conversations = db.session.query(TransactionParticipant, Transaction, PortalMessage, activity.c.unread).join(
            Transaction, Transaction.id == TransactionParticipant.transaction_id,
        ).outerjoin(activity, and_(activity.c.participant_id == TransactionParticipant.id,
                                 activity.c.position == 1)).outerjoin(
            PortalMessage, PortalMessage.id == activity.c.latest_id,
        ).filter(TransactionParticipant.organization_id == org_id,
                 TransactionParticipant.role.in_(tuple(CLIENT_PORTAL_ROLES)),
                 Transaction.id.in_(txs.with_entities(Transaction.id))).options(
                     joinedload(TransactionParticipant.contact),
                     joinedload(TransactionParticipant.user)).all()
        for participant, tx, last, unread in conversations:
            rows.append(dict(key=f'deal-{participant.id}', kind='deal', name=participant.display_name,
                             email=participant.display_email or '', subject=tx.street_address or 'Transaction',
                             label='Transaction', preview=last.body if last else 'Start a conversation',
                             at=last.created_at if last else None, attention=bool(unread),
                             last_sender=last.sender if last else None,
                             row=participant, transaction=tx))
    return sorted(rows, key=lambda row: row['at'] or datetime.min, reverse=True)


def _selected(threads, key):
    selected = next((row for row in threads if row['key'] == key), None)
    if key and selected is None:
        abort(404)
    if selected and selected['kind'] == 'deal':
        selected['can_reply'] = has_capability(
            selected['transaction'], CAP_SEND_COMMS, current_user).allowed
    return selected


def _messages(selected, mark_read=False):
    if not selected:
        return []
    org_id = current_user.organization_id
    if selected['kind'] == 'inquiry':
        inquiry = selected['row']
        messages = [dict(id=f'initial-{inquiry.id}', sender='client', body=inquiry.body,
                         created_at=inquiry.created_at, author_name=None)]
        if inquiry.reply:
            messages.append(dict(id=f'legacy-{inquiry.id}', sender='agent', body=inquiry.reply,
                                 created_at=None, author_name=None))
        replies = ClientInquiryMessage.query.filter_by(organization_id=org_id,
            inquiry_id=inquiry.id).options(joinedload(ClientInquiryMessage.author)).order_by(ClientInquiryMessage.id).all()
        messages.extend(dict(id=f'message-{message.id}', sender=message.sender, body=message.body,
                             created_at=message.created_at, author_name=agent_name(message.author)) for message in replies)
        return messages
    messages = PortalMessage.query.filter_by(organization_id=org_id,
        transaction_id=selected['transaction'].id, participant_id=selected['row'].id).options(
            joinedload(PortalMessage.author)).order_by(PortalMessage.created_at, PortalMessage.id).all()
    if mark_read:
        now = datetime.utcnow()
        changed = False
        for message in messages:
            if message.sender == 'client' and message.read_by_agent_at is None:
                message.read_by_agent_at = now
                changed = True
        if changed:
            db.session.flush()
            selected['_marked_read'] = True
        selected['attention'] = False
    return messages


def _request_id():
    value = request.form.get('request_id') or str(uuid.uuid4())
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        abort(400, 'The message could not be identified. Refresh and try again.')


def _post(selected, csrf_token):
    if not selected:
        abort(404)
    if not hmac.compare_digest(csrf_token, request.form.get('csrf_token', '')):
        abort(400, 'Your session needs to refresh before you can send. Your draft is still here.')
    org_id = current_user.organization_id
    action = request.form.get('action', 'reply')
    if selected['kind'] == 'inquiry':
        # Assignment and replies share this account lock with the client API.
        account = ClientBrowseAccount.query.filter_by(id=selected['account'].id,
            organization_id=org_id).with_for_update().populate_existing().first()
        row = ClientBrowseInquiry.query.filter_by(id=selected['row'].id,
            organization_id=org_id).options(noload(ClientBrowseInquiry.messages)).populate_existing().first()
        if account is None or row is None:
            abort(404)
        if not inbox_admin(current_user) and row.agent_id not in (None, current_user.id):
            abort(404)
        selected['account'], selected['row'] = account, row
        if action in ('claim', 'assign'):
            if action == 'assign' and not inbox_admin(current_user):
                abort(403)
            if action == 'claim' and (account.agent_id is not None or row.agent_id is not None):
                abort(409, 'Another agent already accepted this client.')
            if action == 'assign' and str(account.agent_id or '') != request.form.get('expected_agent', ''):
                abort(409, 'The assignment changed. Review the current agent before assigning again.')
            raw_target = request.form.get('agent_id', '')
            if action == 'assign' and raw_target and (not raw_target.isdecimal() or len(raw_target) > 12 or int(raw_target) < 1):
                abort(400, 'Choose an agent from this brokerage.')
            target_id = current_user.id if action == 'claim' else int(raw_target) if raw_target else None
            target = User.query.filter_by(id=target_id, organization_id=org_id).first() if target_id else None
            if target_id and not target:
                abort(400, 'Choose an agent from this brokerage.')
            try:
                assign_client(account, db.session.get(Organization, org_id), target, current_user)
            except ValueError as exc:
                db.session.rollback()
                abort(409, str(exc))
            db.session.commit()
            return 'Client assigned.' if target else 'Client returned to the general inbox.', 'mine' if target_id == current_user.id else 'all'
        if action != 'reply' or row.agent_id is None or (row.agent_id != current_user.id and not inbox_admin(current_user)):
            abort(403)
    elif action != 'reply':
        abort(400)
    if not selected['can_reply']:
        abort(403)
    body = request.form.get('body', '').strip()
    if not body or len(body) > 4000:
        abort(400, 'Write a message of up to 4,000 characters.')
    request_id = _request_id()
    message = None
    if selected['kind'] == 'inquiry':
        prior = ClientInquiryMessage.query.filter_by(organization_id=org_id,
            inquiry_id=selected['row'].id, sender='agent', request_id=request_id).first()
        if prior and (prior.body != body or prior.author_user_id != current_user.id):
            abort(409, 'This send was already used for another message. Review the conversation before sending again.')
        append_reply(selected['row'], body, current_user, request_id)
    else:
        participant = TransactionParticipant.query.filter_by(id=selected['row'].id,
            transaction_id=selected['transaction'].id, organization_id=org_id).filter(
                TransactionParticipant.role.in_(tuple(CLIENT_PORTAL_ROLES))).with_for_update().populate_existing().first()
        if participant is None:
            abort(404)
        if not has_capability(selected['transaction'], CAP_SEND_COMMS, current_user).allowed:
            abort(403)
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        # The participant lock serializes retries across workers. The receipt and
        # message commit together, including when the response never reaches the browser.
        receipt = AuditEvent.query.filter_by(organization_id=org_id,
            transaction_id=selected['transaction'].id, actor_id=current_user.id,
            event_type='client_message_sent').filter(
                AuditEvent.event_data['request_id'].as_string() == request_id,
                AuditEvent.event_data['participant_id'].as_integer() == participant.id).first()
        if receipt:
            if receipt.event_data.get('body_hash') != body_hash:
                abort(409, 'This send was already used for another message. Review the conversation before sending again.')
        else:
            message = PortalMessage(organization_id=org_id,
                transaction_id=selected['transaction'].id, participant_id=participant.id,
                sender='agent', kind='message', body=body, author_user_id=current_user.id)
            db.session.add(message)
            db.session.flush()
            db.session.add(AuditEvent(organization_id=org_id, transaction_id=message.transaction_id,
                actor_id=current_user.id, event_type='client_message_sent', description='Sent a message to the client app.',
                event_data={'request_id': request_id, 'participant_id': participant.id,
                            'message_id': message.id, 'body_hash': body_hash}))
    db.session.commit()
    if message:
        try:
            enqueue_portal_push(message)
        except Exception:
            current_app.logger.exception('Could not enqueue client message push')
            db.session.rollback()
    return 'Reply sent to the client app.', None


def _conversation_version(selected, messages):
    if not selected:
        return ''
    values = dict(key=selected['key'], name=selected['name'], email=selected['email'],
                  subject=selected['subject'], can_reply=selected['can_reply'],
                  agent=selected.get('agent_name'), agent_id=getattr(selected['row'], 'agent_id', None),
                  snapshot=getattr(selected['row'], 'listing_snapshot', None), messages=[])
    for message in messages:
        if isinstance(message, dict):
            values['messages'].append(message)
        else:
            values['messages'].append(dict(id=message.id, body=message.body, sender=message.sender,
                created_at=message.created_at, kind=message.kind, attachment_name=message.attachment_name,
                author=agent_name(message.author)))
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


@client_messages_bp.route('/messages', methods=['GET', 'POST'])
@login_required
def inbox():
    # Request hooks can commit, clearing PostgreSQL's transaction-local context.
    org_context(current_user.organization_id)
    threads = _threads()
    key = request.args.get('thread', '')
    selected = _selected(threads, key)
    csrf_token = session.setdefault('client_inquiry_csrf', secrets.token_urlsafe(32))
    search = request.args.get('q', '').strip()[:200]
    view = request.args.get('view', 'all')
    if view not in ('all', 'general', 'mine', 'attention', 'inquiry', 'deal'):
        view = 'all'
    notice, error, draft, status = None, None, '', 200
    if request.method == 'POST':
        try:
            notice, next_view = _post(selected, csrf_token)
        except HTTPException as exc:
            if _wants_json() or exc.code != 400 or exc.description != 'Write a message of up to 4,000 characters.':
                raise
            error, draft, status = exc.description, request.form.get('body', '').strip(), 400
        else:
            if not _wants_json():
                flash(notice, 'success')
                return redirect(url_for('.inbox', thread=key, view=next_view or view, q=search))
            view = next_view or view
            org_context(current_user.organization_id)
            threads = _threads()
            selected = _selected(threads, key)
    messages = _messages(selected, mark_read=request.method == 'GET' and request.args.get('mark_read') != '0')
    attention_count = sum(row['attention'] for row in threads)
    if search:
        threads = [row for row in threads if search.casefold() in
                   ' '.join((row['name'], row['email'], row['subject'], row['preview'])).casefold()]
    if view == 'general':
        threads = [row for row in threads if row.get('unassigned')]
    elif view == 'mine':
        threads = [row for row in threads if row['kind'] == 'deal' or row['row'].agent_id == current_user.id]
    elif view == 'attention':
        threads = [row for row in threads if row['attention']]
    elif view in ('inquiry', 'deal'):
        threads = [row for row in threads if row['kind'] == view]
    context = dict(threads=threads, selected=selected, messages=messages, csrf_token=csrf_token,
                   search=search, view=view, attention_count=attention_count, draft=draft, error=error,
                   is_inbox_admin=inbox_admin(current_user), request_id=str(uuid.uuid4()),
                   agents=User.query.filter_by(organization_id=current_user.organization_id).order_by(User.first_name, User.id).all()
                   if selected and selected['kind'] == 'inquiry' and inbox_admin(current_user) else [])
    if _wants_json():
        response = jsonify(ok=True, threads_html=render_template('client_messages/_threads.html', **context),
                       conversation_html=render_template('client_messages/_conversation.html', **context),
                       history_html=render_template('client_messages/_history.html', **context),
                       selected_key=selected['key'] if selected else '', attention_count=attention_count,
                       csrf_token=csrf_token, request_id=context['request_id'], view=view, search=search,
                       can_reply=bool(selected and selected['can_reply']), notice=notice,
                       conversation_version=_conversation_version(selected, messages),
                       url=url_for('.inbox', thread=key or None, view=view, q=search or None))
    else:
        response = render_template('client_messages/inbox.html', **context)
    if selected and selected.get('_marked_read'):
        db.session.commit()
    return response, status
