"""Agent workspace for client inquiries and transaction conversations."""
import hmac
import secrets
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload

from feature_flags import can_access_transactions
from models import db, ClientBrowseAccount, ClientBrowseInquiry, PortalMessage, Transaction, TransactionParticipant
from routes.client_discovery import org_context
from services.device_push import enqueue_portal_push
from services.portal_service import CLIENT_PORTAL_ROLES
from services.transaction_auth import CAP_SEND_COMMS, has_capability, transactions_visible_query


client_messages_bp = Blueprint('client_messages', __name__)


def _threads():
    org_id = current_user.organization_id
    inquiries = db.session.query(ClientBrowseInquiry, ClientBrowseAccount).join(
        ClientBrowseAccount, ClientBrowseAccount.id == ClientBrowseInquiry.account_id,
    ).filter(ClientBrowseInquiry.organization_id == org_id,
             ClientBrowseAccount.organization_id == org_id)
    if current_user.org_role not in ('owner', 'admin'):
        inquiries = inquiries.filter(ClientBrowseInquiry.agent_id == current_user.id)
    rows = []
    for inquiry, account in inquiries.all():
        rows.append(dict(key=f'inquiry-{inquiry.id}', kind='inquiry', name=account.name,
                         email=account.email, subject=(f'Sample home {inquiry.listing_id}'
                         if inquiry.listing_id else 'General inquiry'),
                         label='Showing request' if inquiry.kind == 'showing' else 'Home inquiry',
                         preview=inquiry.reply or inquiry.body, at=inquiry.created_at,
                         attention=not bool(inquiry.reply), row=inquiry, account=account,
                         can_reply=True))
    if can_access_transactions(current_user):
        txs = transactions_visible_query(current_user)
        if current_user.org_role in ('owner', 'admin'):
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
        if not selected['can_reply']:
            abort(403)
        draft = request.form.get('body', '').strip()
        if not draft or len(draft) > 4000:
            error = 'Write a message of up to 4,000 characters.'
            status = 400
        else:
            if selected['kind'] == 'inquiry':
                selected['row'].reply = draft
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
    messages = []
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
    if view == 'attention':
        threads = [row for row in threads if row['attention']]
    elif view in ('inquiry', 'deal'):
        threads = [row for row in threads if row['kind'] == view]
    return render_template('client_messages/inbox.html', threads=threads, selected=selected,
                           messages=messages, csrf_token=csrf_token, search=search, view=view,
                           attention_count=attention_count, draft=draft, error=error), status
