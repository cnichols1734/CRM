"""Native agent access to the brokerage inquiry inbox."""
import uuid
from datetime import datetime

from flask import jsonify, request, current_app
from sqlalchemy import func, and_, or_
from itsdangerous import URLSafeSerializer, BadSignature

from models import db, User, ClientBrowseAccount, ClientBrowseInquiry, ClientInquiryMessage
from routes.agent_api import agent_jwt_required
from routes.client_discovery import org_context
from services.client_conversations import assign_client, inquiry_payload
from services.device_push import enqueue_inquiry_push
from services.profile_photos import avatar_url


def register(bp):
    bp.add_url_rule('/inquiries', 'agent_inquiries', inbox, methods=['GET'])
    bp.add_url_rule('/inquiries/<int:inquiry_id>', 'agent_inquiry', detail, methods=['GET'])
    bp.add_url_rule('/inquiries/<int:inquiry_id>/claim', 'agent_inquiry_claim', claim, methods=['POST'])
    bp.add_url_rule('/inquiries/<int:inquiry_id>/messages', 'agent_inquiry_reply', reply, methods=['POST'])


def visible(user):
    return ClientBrowseInquiry.query.filter(
        ClientBrowseInquiry.organization_id == user.organization_id,
        (ClientBrowseInquiry.agent_id == user.id) | ClientBrowseInquiry.agent_id.is_(None))


def native_payload(row):
    account = ClientBrowseAccount.query.filter_by(id=row.account_id, organization_id=row.organization_id).one()
    result = inquiry_payload(row)
    history = result['messages']
    result.update(account_id=account.id, name=account.name, email=account.email,
                  agent_id=row.agent_id, last_preview=history[-1]['body'] or 'Attachment',
                  last_at=history[-1]['created_at'] or result['created_at'])
    return result


@agent_jwt_required
def inbox(user):
    org_context(user.organization_id)
    page_size = min(max(request.args.get('limit', 100, type=int), 1), 100)
    signer = URLSafeSerializer(current_app.config['SECRET_KEY'], salt='agent-inquiry-page')
    latest = db.session.query(ClientInquiryMessage.inquiry_id,
        func.max(ClientInquiryMessage.id).label('message_id')).filter(
        ClientInquiryMessage.organization_id == user.organization_id).group_by(ClientInquiryMessage.inquiry_id).subquery()
    at = func.coalesce(ClientInquiryMessage.created_at, ClientBrowseInquiry.created_at)
    query = visible(user).add_columns(ClientBrowseAccount, User, ClientInquiryMessage).join(
        ClientBrowseAccount, and_(ClientBrowseAccount.id == ClientBrowseInquiry.account_id,
            ClientBrowseAccount.organization_id == user.organization_id)).outerjoin(
        User, and_(User.id == ClientBrowseInquiry.agent_id, User.organization_id == user.organization_id)).outerjoin(
        latest, latest.c.inquiry_id == ClientBrowseInquiry.id).outerjoin(
        ClientInquiryMessage, ClientInquiryMessage.id == latest.c.message_id)
    cursor = request.args.get('cursor')
    if cursor:
        try:
            scope_org, scope_user, last_at, last_id = signer.loads(cursor)
            if (scope_org, scope_user) != (user.organization_id, user.id):
                raise ValueError()
            cutoff = datetime.fromisoformat(last_at)
            query = query.filter(or_(at < cutoff, and_(at == cutoff, ClientBrowseInquiry.id < int(last_id))))
        except (BadSignature, TypeError, ValueError):
            return jsonify(error='Refresh the inbox to continue.'), 400
    rows = query.order_by(at.desc(), ClientBrowseInquiry.id.desc()).limit(page_size + 1).all()
    result = []
    for row, account, agent, message in rows[:page_size]:
        timestamp = message.created_at if message else row.created_at
        result.append(dict(id=row.id, account_id=account.id, name=account.name, email=account.email,
            kind=row.kind, assigned=row.agent_id is not None, agent_id=row.agent_id,
            client_avatar_url=avatar_url(account), agent_avatar_url=avatar_url(agent),
            agent_name=f'{agent.first_name} {agent.last_name}'.strip() if agent else None,
            created_at=row.created_at.isoformat() + 'Z', last_at=timestamp.isoformat() + 'Z',
            last_preview=(message.body if message else row.reply or row.body)[:240] or 'Attachment', messages=[]))
    next_cursor = None
    if len(rows) > page_size:
        row, _, _, message = rows[page_size - 1]
        timestamp = message.created_at if message else row.created_at
        next_cursor = signer.dumps([user.organization_id, user.id, timestamp.isoformat(), row.id])
    return jsonify(inquiries=result, next_cursor=next_cursor)


@agent_jwt_required
def detail(user, inquiry_id):
    org_context(user.organization_id)
    row = visible(user).filter_by(id=inquiry_id).first()
    if not row:
        return jsonify(error='Conversation not found.'), 404
    return jsonify(inquiry=native_payload(row))


def locked_inquiry(user, inquiry_id):
    org_context(user.organization_id)
    row = ClientBrowseInquiry.query.filter_by(id=inquiry_id, organization_id=user.organization_id).first()
    if row is None:
        return None, None
    account = ClientBrowseAccount.query.filter_by(id=row.account_id,
        organization_id=user.organization_id).with_for_update().populate_existing().first()
    row = ClientBrowseInquiry.query.filter_by(id=inquiry_id,
        organization_id=user.organization_id).populate_existing().first()
    return account, row


@agent_jwt_required
def claim(user, inquiry_id):
    account, row = locked_inquiry(user, inquiry_id)
    if account is None or row is None:
        return jsonify(error='Conversation not found.'), 404
    if account.agent_id == user.id and row.agent_id == user.id:
        return jsonify(inquiry=native_payload(row))
    if account.agent_id is not None or row.agent_id is not None:
        return jsonify(error='Another agent already accepted this client.'), 409
    try:
        notice = assign_client(account, user.organization, user, user)
        db.session.flush()
        notice_id = notice.id if notice else None
        notice_inquiry_id = notice.inquiry_id if notice else None
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 409
    if notice_id:
        enqueue_inquiry_push(inquiry_id=notice_inquiry_id, org_id=user.organization_id,
                             message_id=notice_id, assignment=True)
    org_context(user.organization_id)
    return jsonify(inquiry=native_payload(row))


@agent_jwt_required
def reply(user, inquiry_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error='A message is required.'), 400
    from services.mobile_attachments import attachment_ids, bind_attachments, AttachmentError
    body, request_id = data.get('body') or '', data.get('request_id')
    try:
        ids = attachment_ids(data)
    except AttachmentError as exc:
        return jsonify(error=str(exc)), exc.status
    if not isinstance(body, str) or (not body.strip() and not ids) or len(body) > 4000:
        return jsonify(error='Write a message of up to 4,000 characters.'), 400
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError):
        return jsonify(error='A message request ID is required.'), 400
    body = body.strip()
    account, row = locked_inquiry(user, inquiry_id)
    if account is None or row is None or row.agent_id != user.id or account.agent_id != user.id:
        return jsonify(error='Claim this client before replying.'), 403
    prior = ClientInquiryMessage.query.filter_by(organization_id=user.organization_id,
        inquiry_id=row.id, sender='agent', request_id=request_id).first()
    if prior and (prior.body != body or prior.author_user_id != user.id):
        return jsonify(error='This request was already used for another message.'), 409
    message_id = None
    if prior is None:
        message = ClientInquiryMessage(organization_id=user.organization_id, inquiry_id=row.id,
            sender='agent', body=body, request_id=request_id, author_user_id=user.id)
        db.session.add(message)
        db.session.flush()
        message_id = message.id
    try:
        bind_attachments(ids, org_id=user.organization_id, owner_kind='agent', owner_id=user.id,
            target_kind='inquiry', target_id=row.id, message_kind='inquiry', message_id=prior.id if prior else message_id,
            replay=prior is not None)
    except AttachmentError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), exc.status
    db.session.commit()
    if message_id:
        enqueue_inquiry_push(inquiry_id=row.id, org_id=user.organization_id, message_id=message_id)
    org_context(user.organization_id)
    db.session.expire(row, ['messages'])
    return jsonify(inquiry=native_payload(row)), 201 if message_id else 200
