"""Agent workspace for client inquiries and transaction conversations."""
import hmac
import secrets
import uuid
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload

from feature_flags import can_access_transactions
from models import db, User, Organization, ClientBrowseAccount, ClientBrowseInquiry, PortalMessage, Transaction, TransactionParticipant
from routes.client_discovery import org_context
from services.device_push import enqueue_portal_push
from services.client_conversations import inbox_admin, agent_name, events, assign_client, append_reply
from services.portal_service import CLIENT_PORTAL_ROLES
from services.transaction_auth import CAP_SEND_COMMS, has_capability, transactions_visible_query


client_messages_bp = Blueprint('client_messages', __name__)


def _threads():
    org_id = current_user.organization_id
    inquiries = db.session.query(ClientBrowseInquiry, ClientBrowseAccount).join(
        ClientBrowseAccount, ClientBrowseAccount.id == ClientBrowseInquiry.account_id,
    ).filter(ClientBrowseInquiry.organization_id == org_id,
             ClientBrowseAccount.organization_id == org_id)
    if not inbox_admin(current_user):
        inquiries = inquiries.filter((ClientBrowseInquiry.agent_id == current_user.id) | ClientBrowseInquiry.agent_id.is_(None))
    rows = []
    for inquiry, account in inquiries.all():
        snapshot = inquiry.listing_snapshot or {}
        history = events(inquiry)
        last = history[-1]
        assigned = User.query.filter_by(id=inquiry.agent_id, organization_id=org_id).first() if inquiry.agent_id else None
        rows.append(dict(key=f'inquiry-{inquiry.id}', kind='inquiry', name=account.name,
                         email=account.email, subject=(f"{snapshot['street']}, {snapshot.get('city', '')} · Repliers sample" if snapshot.get('street') else f'Sample home {inquiry.listing_id}'
                         if inquiry.listing_id else 'General inquiry'),
                         label='Showing request' if inquiry.kind == 'showing' else 'Home inquiry',
                         preview=last['body'], at=last['created_at'] or inquiry.created_at,
                         attention=last['sender'] == 'client', row=inquiry, account=account,
                         agent_name=agent_name(assigned), unassigned=inquiry.agent_id is None,
                         can_reply=inquiry.agent_id is not None and (inbox_admin(current_user) or inquiry.agent_id == current_user.id)))
    if can_access_transactions(current_user):
        txs = transactions_visible_query(current_user)
        if inbox_admin(current_user):
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
                             row=participant, transaction=tx))
    return sorted(rows, key=lambda row: row['at'] or datetime.min, reverse=True)


@client_messages_bp.route('/messages', methods=['GET', 'POST'])
@login_required
def inbox():
    # Request hooks can commit, which clears PostgreSQL's transaction-local context.
    org_context(current_user.organization_id)
    threads = _threads()
    key = request.args.get('thread', '')
    selected = next((row for row in threads if row['key'] == key), None)
    if key and selected is None:
        abort(404)
    if selected and selected['kind'] == 'deal':
        selected['can_reply'] = has_capability(
            selected['transaction'], CAP_SEND_COMMS, current_user).allowed
    csrf_token = session.setdefault('client_inquiry_csrf', secrets.token_urlsafe(32))
    draft = ''
    error = None
    status = 200
    if request.method == 'POST':
        if not selected:
            abort(404)
        if not hmac.compare_digest(csrf_token, request.form.get('csrf_token', '')):
            abort(400)
        if selected['kind'] == 'inquiry':
            account = ClientBrowseAccount.query.filter_by(id=selected['account'].id,
                organization_id=current_user.organization_id).with_for_update().populate_existing().one()
            row = ClientBrowseInquiry.query.filter_by(id=selected['row'].id,
                organization_id=current_user.organization_id).populate_existing().one()
            action = request.form.get('action', 'reply')
            if action in ('claim', 'assign'):
                if action == 'assign' and not inbox_admin(current_user):
                    abort(403)
                if action == 'claim' and (account.agent_id is not None or row.agent_id is not None):
                    abort(409, 'This client has already been assigned. Refresh the inbox.')
                if action == 'assign' and str(account.agent_id or '') != request.form.get('expected_agent', ''):
                    abort(409, 'The assignment changed. Refresh before assigning again.')
                target_id = current_user.id if action == 'claim' else request.form.get('agent_id', type=int)
                target = User.query.filter_by(id=target_id, organization_id=current_user.organization_id).first() if target_id else None
                if target_id and not target:
                    abort(400)
                try:
                    assign_client(account, db.session.get(Organization, current_user.organization_id), target, current_user)
                except ValueError as exc:
                    db.session.rollback()
                    flash(str(exc), 'error')
                    return redirect(url_for('.inbox', thread=key))
                db.session.commit()
                flash('Client assigned.' if target else 'Client returned to the general inbox.', 'success')
                return redirect(url_for('.inbox', thread=key, view='mine' if target_id == current_user.id else 'all'))
            if action != 'reply' or row.agent_id is None or (row.agent_id != current_user.id and not inbox_admin(current_user)):
                abort(403)
        if not selected['can_reply']:
            abort(403)
        draft = request.form.get('body', '').strip()
        if not draft or len(draft) > 4000:
            error = 'Write a message of up to 4,000 characters.'
            status = 400
        else:
            if selected['kind'] == 'inquiry':
                request_id = request.form.get('request_id', str(uuid.uuid4()))
                try:
                    uuid.UUID(request_id)
                except ValueError:
                    abort(400)
                append_reply(selected['row'], draft, current_user, request_id)
                message = None
            else:
                message = PortalMessage(organization_id=current_user.organization_id,
                    transaction_id=selected['transaction'].id, participant_id=selected['row'].id,
                    sender='agent', kind='message', body=draft, author_user_id=current_user.id)
                db.session.add(message)
            db.session.commit()
            if message:
                try:
                    enqueue_portal_push(message)
                except Exception:
                    from flask import current_app
                    current_app.logger.exception('Could not enqueue client message push')
            flash('Reply sent to the client app.', 'success')
            return redirect(url_for('.inbox', thread=key))
    messages = events(selected['row']) if selected and selected['kind'] == 'inquiry' else []
    if selected and selected['kind'] == 'deal':
        messages = PortalMessage.query.filter_by(organization_id=current_user.organization_id,
            transaction_id=selected['transaction'].id, participant_id=selected['row'].id).options(
                joinedload(PortalMessage.author)).order_by(PortalMessage.created_at, PortalMessage.id).all()
        # Only the conversation opened by the agent is marked read.
        if request.method == 'GET':
            now = datetime.utcnow()
            changed = False
            for message in messages:
                if message.sender == 'client' and message.read_by_agent_at is None:
                    message.read_by_agent_at = now
                    changed = True
            if changed:
                db.session.commit()
                org_context(current_user.organization_id)
            selected['attention'] = False
    attention_count = sum(row['attention'] for row in threads)
    search = request.args.get('q', '').strip()[:200]
    view = request.args.get('view', 'all')
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
    return render_template('client_messages/inbox.html', threads=threads, selected=selected,
                           messages=messages, csrf_token=csrf_token, search=search, view=view,
                           attention_count=attention_count, draft=draft, error=error,
                           is_inbox_admin=inbox_admin(current_user), request_id=str(uuid.uuid4()),
                           agents=User.query.filter_by(organization_id=current_user.organization_id).order_by(User.first_name, User.id).all()), status
