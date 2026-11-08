from flask import jsonify, request

from models import AuditEvent, ChatConversation, ClientBrowseAccount, User, db
from routes.agent_api import agent_jwt_required, _conversation_access, _set_org_context
from services.message_safety import identity_keys, is_blocked


def register(bp):
    bp.add_url_rule('/conversations/<int:subject_id>/block', 'block_deal_client', block_deal, methods=['PUT'])
    bp.add_url_rule('/inquiries/<int:subject_id>/block', 'block_inquiry_client', block_inquiry, methods=['PUT'])
    bp.add_url_rule('/content-reports', 'report_content', report, methods=['POST'])


def subject(user, kind, subject_id):
    if kind == 'deal':
        _, participant, failure = _conversation_access(user, subject_id)
        return {'participant': participant}, failure
    if kind == 'inquiry':
        from routes.agent_inquiries import visible
        row = visible(user).filter_by(id=subject_id).first()
        account = ClientBrowseAccount.query.filter_by(id=row.account_id,
            organization_id=user.organization_id).first() if row else None
        if account:
            return {'account': account}, None
    if kind == 'bob':
        row = ChatConversation.query.filter_by(id=subject_id, user_id=user.id,
            organization_id=user.organization_id).first()
        if row:
            return {}, None
    return {}, (jsonify(error='Conversation not found.'), 404)


def update_block(user, kind, subject_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or type(data.get('blocked')) is not bool:
        return jsonify(error='Choose whether to block this client.'), 400
    target, failure = subject(user, kind, subject_id)
    if failure:
        return failure
    owner = User.query.filter_by(id=user.id, organization_id=user.organization_id).with_for_update().populate_existing().one()
    prefs = dict(owner.notification_prefs or {})
    keys = identity_keys(**target)
    blocked = set(prefs.get('blocked_clients', []))
    blocked = blocked | keys if data['blocked'] else blocked - keys
    if len(blocked) > 10000:
        return jsonify(error='Contact support to manage additional blocked clients.'), 400
    prefs['blocked_clients'] = sorted(blocked)
    owner.notification_prefs = prefs
    db.session.commit()
    _set_org_context(user.organization_id)
    return jsonify(blocked=is_blocked(owner, **target))


@agent_jwt_required
def block_deal(user, subject_id):
    return update_block(user, 'deal', subject_id)


@agent_jwt_required
def block_inquiry(user, subject_id):
    return update_block(user, 'inquiry', subject_id)


@agent_jwt_required
def report(user):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='Describe the issue before sending.'), 400
    kind, subject_id, reason = data.get('kind'), data.get('subject_id'), data.get('reason')
    if kind not in ('deal', 'inquiry', 'bob') or type(subject_id) is not int or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
        return jsonify(error='Describe the issue in up to 2,000 characters.'), 400
    _, failure = subject(user, kind, subject_id)
    if failure:
        return failure
    from datetime import datetime, timedelta
    User.query.filter_by(id=user.id, organization_id=user.organization_id).with_for_update().one()
    recent = AuditEvent.query.filter(AuditEvent.actor_id == user.id,
        AuditEvent.organization_id == user.organization_id, AuditEvent.event_type == 'content_report',
        AuditEvent.created_at > datetime.utcnow() - timedelta(hours=24)).count()
    if recent >= 20:
        return jsonify(error='You have reached today’s report limit. Contact support for urgent help.'), 429
    entry = AuditEvent(organization_id=user.organization_id, actor_id=user.id,
        event_type='content_report', source='agent_ios', description='Content reported for review',
        event_data={'kind': kind, 'subject_id': subject_id, 'reason': reason.strip(), 'status': 'open'})
    db.session.add(entry)
    db.session.commit()
    _set_org_context(user.organization_id)
    from services.sendgrid_outbound import _send_html
    notified = _send_html('chrisnichols17@gmail.com', f'AgentFlow content report AF-{entry.id}',
        f'<p>Report AF-{entry.id} is awaiting review in the AgentFlow content report queue. No conversation content is included in this email.</p>')
    entry = AuditEvent.query.filter_by(id=entry.id, organization_id=user.organization_id,
        event_type='content_report').with_for_update().populate_existing().one()
    entry.event_data = {**entry.event_data, 'notification_sent': notified}
    db.session.commit()
    _set_org_context(user.organization_id)
    return jsonify(receipt=f'AF-{entry.id}'), 201
