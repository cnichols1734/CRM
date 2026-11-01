"""Authenticated attachment uploads and fresh download URLs."""
from flask import jsonify, request

from models import db, MobileChatAttachment, PortalMessage, ClientBrowseInquiry, ClientInquiryMessage, ClientPortalAccess
from services import mobile_attachments as files


def row_for(identifier, org_id):
    return MobileChatAttachment.query.filter_by(id=identifier, organization_id=org_id).first()


def inquiry_for(row):
    if row.message_kind == 'initial':
        return ClientBrowseInquiry.query.filter_by(id=row.message_id, organization_id=row.organization_id).first()
    if row.message_kind == 'inquiry':
        message = ClientInquiryMessage.query.filter_by(id=row.message_id, organization_id=row.organization_id).first()
        return ClientBrowseInquiry.query.filter_by(id=message.inquiry_id, organization_id=row.organization_id).first() if message else None
    if row.target_kind == 'inquiry':
        return ClientBrowseInquiry.query.filter_by(id=row.target_id, organization_id=row.organization_id).first()
    return None


def deal_message_exists(row):
    return row.message_kind == 'portal' and PortalMessage.query.filter_by(id=row.message_id,
        organization_id=row.organization_id, participant_id=row.target_id).first() is not None


def respond(row, owner_kind, owner_id):
    if request.method == 'GET' and row.message_id is None and row.created_at < files.datetime.utcnow() - files.DRAFT_LIFETIME:
        return jsonify(error='That upload expired. Upload the file again.'), 410
    if request.method == 'DELETE':
        return files.discard(row, owner_kind, owner_id)
    result = files.attachment_payload(row)
    if result['url'] is None:
        return jsonify(error='This file is temporarily unavailable. Try again.'), 503
    return jsonify(attachment=result)


def register_agent(bp):
    from routes.agent_api import agent_jwt_required, transactions_flag_required, _conversation_access
    from routes.agent_inquiries import locked_inquiry, visible

    @bp.post('/conversations/<int:participant_id>/attachments')
    @agent_jwt_required
    @transactions_flag_required
    def upload_deal_attachment(user, participant_id):
        tx, participant, error = _conversation_access(user, participant_id)
        if error:
            return error
        return files.upload(user, 'agent', 'deal', participant.id)

    @bp.post('/inquiries/<int:inquiry_id>/attachments')
    @agent_jwt_required
    def upload_inquiry_attachment(user, inquiry_id):
        account, inquiry = locked_inquiry(user, inquiry_id)
        if account is None or inquiry is None or inquiry.agent_id != user.id or account.agent_id != user.id:
            return jsonify(error='Claim this client before attaching a file.'), 403
        return files.upload(user, 'agent', 'inquiry', inquiry.id)

    @bp.route('/attachments/<identifier>', methods=['GET', 'DELETE'])
    @agent_jwt_required
    def agent_attachment(user, identifier):
        row = row_for(identifier, user.organization_id)
        if row is None:
            return jsonify(error='Attachment not found.'), 404
        draft = row.message_id is None
        if draft and (row.owner_kind, row.owner_id) != ('agent', user.id):
            return jsonify(error='Attachment not found.'), 404
        if row.target_kind == 'deal':
            _, _, error = _conversation_access(user, row.target_id)
            if error or (not draft and not deal_message_exists(row)):
                return jsonify(error='Attachment not found.'), 404
        else:
            inquiry = inquiry_for(row)
            if inquiry is None or visible(user).filter_by(id=inquiry.id).first() is None:
                return jsonify(error='Attachment not found.'), 404
        return respond(row, 'agent', user.id)


def register_client(bp):
    from routes.client_api import client_jwt_required

    @bp.post('/attachments')
    @client_jwt_required
    def upload_client_attachment(access):
        version = access.session_version
        access = ClientPortalAccess.query.filter_by(id=access.id, organization_id=access.organization_id).with_for_update().populate_existing().first()
        if access is None or not access.is_active or access.session_version != version:
            return jsonify(error='Sign in with your invite code again.'), 401
        return files.upload(access.participant, 'participant', 'deal', access.participant_id)

    @bp.route('/attachments/<identifier>', methods=['GET', 'DELETE'])
    @client_jwt_required
    def client_attachment(access, identifier):
        row = row_for(identifier, access.organization_id)
        if row is None or (row.target_kind, row.target_id) != ('deal', access.participant_id):
            return jsonify(error='Attachment not found.'), 404
        if row.message_id is None:
            if (row.owner_kind, row.owner_id) != ('participant', access.participant_id):
                return jsonify(error='Attachment not found.'), 404
        elif not deal_message_exists(row):
            return jsonify(error='Attachment not found.'), 404
        return respond(row, 'participant', access.participant_id)


def register_discovery(bp):
    from routes.client_discovery import authenticated, lock_browse_session

    @bp.post('/attachments')
    @bp.post('/inquiries/<int:inquiry_id>/attachments')
    @authenticated
    def upload_browse_attachment(account, org, inquiry_id=None):
        account = lock_browse_session(account, org)
        if account is None:
            return jsonify(error='Sign in to your home-search account again.'), 401
        if inquiry_id is not None:
            inquiry = ClientBrowseInquiry.query.filter_by(id=inquiry_id, account_id=account.id, organization_id=org.id).first()
            if inquiry is None:
                return jsonify(error='Conversation not found.'), 404
        return files.upload(account, 'browse', 'inquiry' if inquiry_id is not None else 'new_inquiry', inquiry_id or account.id)

    @bp.route('/attachments/<identifier>', methods=['GET', 'DELETE'])
    @authenticated
    def browse_attachment(account, org, identifier):
        row = row_for(identifier, org.id)
        if row is None or row.target_kind == 'deal':
            return jsonify(error='Attachment not found.'), 404
        if row.message_id is None:
            if (row.owner_kind, row.owner_id) != ('browse', account.id):
                return jsonify(error='Attachment not found.'), 404
        else:
            inquiry = inquiry_for(row)
            if inquiry is None or inquiry.account_id != account.id:
                return jsonify(error='Attachment not found.'), 404
        return respond(row, 'browse', account.id)
