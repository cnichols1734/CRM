"""Native agent access to the brokerage inquiry inbox."""
import uuid

from flask import jsonify, request
from sqlalchemy import func

from models import db, ClientBrowseAccount, ClientBrowseInquiry, ClientInquiryMessage
from routes.agent_api import agent_jwt_required
from routes.client_discovery import org_context
from services.client_conversations import assign_client, inquiry_payload
from services.device_push import enqueue_inquiry_push


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
                  agent_id=row.agent_id, last_preview=history[-1]['body'],
                  last_at=history[-1]['created_at'] or result['created_at'])
    return result


@agent_jwt_required
def inbox(user):
    org_context(user.organization_id)
    latest = db.session.query(func.max(ClientInquiryMessage.created_at)).filter(
        ClientInquiryMessage.organization_id == user.organization_id,
        ClientInquiryMessage.inquiry_id == ClientBrowseInquiry.id).correlate(ClientBrowseInquiry).scalar_subquery()
    rows = visible(user).order_by(func.coalesce(latest, ClientBrowseInquiry.created_at).desc(), ClientBrowseInquiry.id.desc()).all()
    return jsonify(inquiries=[native_payload(row) for row in rows])


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
    body, request_id = data.get('body'), data.get('request_id')
    if not isinstance(body, str) or not body.strip() or len(body) > 4000:
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
    db.session.commit()
    if message_id:
        enqueue_inquiry_push(inquiry_id=row.id, org_id=user.organization_id, message_id=message_id)
    org_context(user.organization_id)
    db.session.expire(row, ['messages'])
    return jsonify(inquiry=native_payload(row)), 201 if message_id else 200
