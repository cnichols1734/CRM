"""Browsing conversations and ownership, independent of transaction access."""
from datetime import datetime

from models import (db, User, Contact, Interaction, UserTodo, AuditEvent,
                    ClientBrowseInquiry, ClientInquiryMessage)


def inbox_admin(user):
    return user.org_role in ('owner', 'admin') or bool(user.is_super_admin)


def agent_name(agent):
    return f'{agent.first_name} {agent.last_name}'.strip() if agent else None


def events(inquiry):
    result = [dict(id=f'initial-{inquiry.id}', sender='client', body=inquiry.body,
                   created_at=inquiry.created_at, author_name=None)]
    # Keep old replies without inventing a send time that was never recorded.
    if inquiry.reply:
        result.append(dict(id=f'legacy-{inquiry.id}', sender='agent', body=inquiry.reply,
                           created_at=None, author_name=None))
    for message in inquiry.messages:
        result.append(dict(id=f'message-{message.id}', sender=message.sender, body=message.body,
                           created_at=message.created_at, author_name=agent_name(message.author)))
    return result


def inquiry_payload(inquiry):
    history = events(inquiry)
    agent = User.query.filter_by(id=inquiry.agent_id, organization_id=inquiry.organization_id).first() if inquiry.agent_id else None
    return dict(id=inquiry.id, kind=inquiry.kind, body=inquiry.body,
                reply=next((m['body'] for m in reversed(history) if m['sender'] == 'agent'), None),
                listing_id=inquiry.listing_id, listing_snapshot=inquiry.listing_snapshot,
                created_at=inquiry.created_at.isoformat() + 'Z',
                agent_name=agent_name(agent), assigned=inquiry.agent_id is not None,
                messages=[{**m, 'created_at': m['created_at'].isoformat() + 'Z' if m['created_at'] else None} for m in history])


def ensure_contact(account, org, agent, phone=''):
    contact = Contact.query.filter_by(id=account.contact_id, organization_id=org.id).first() if account.contact_id else None
    if contact and contact.user_id != agent.id:
        if account.owns_contact:
            contact.user_id = agent.id
        else:
            contact = None
    if not contact:
        if org.is_at_contact_limit:
            raise ValueError('The brokerage contact limit has been reached. This client remains in the inbox.')
        parts = account.name.split(' ', 1)
        contact = Contact(organization_id=org.id, user_id=agent.id, created_by_id=agent.id,
            first_name=parts[0][:80], last_name=(parts[1] if len(parts) > 1 else '')[:80], email=account.email,
            phone=phone, notes='AgentFlow app inquiry. Email ' + ('verified.' if account.email_verified_at else 'provided by client, not verified.'))
        db.session.add(contact)
        db.session.flush()
        account.contact_id = contact.id
        account.owns_contact = True
    elif phone and account.owns_contact:
        contact.phone = phone
    return contact


def record_inquiry(inquiry, account, contact):
    snapshot = inquiry.listing_snapshot or {}
    address = snapshot.get('street') or inquiry.listing_id
    context = f' about {address}' if address else ''
    first_record = inquiry.crm_recorded_at is None
    if first_record:
        db.session.add(Interaction(organization_id=inquiry.organization_id, contact_id=contact.id,
            user_id=inquiry.agent_id, type='Note', notes=f'AgentFlow {inquiry.kind} inquiry{context}\n{inquiry.body}', date=datetime.utcnow()))
        inquiry.crm_recorded_at = datetime.utcnow()
    todo = UserTodo.query.filter_by(id=inquiry.followup_todo_id, organization_id=inquiry.organization_id).first() if inquiry.followup_todo_id else None
    if todo:
        if not todo.completed:
            todo.user_id = inquiry.agent_id
    elif first_record:
        todo = UserTodo(organization_id=inquiry.organization_id, user_id=inquiry.agent_id,
            text=f'Follow up with {account.name}: {inquiry.kind} inquiry from AgentFlow. Contact #{contact.id}.'[:500])
        db.session.add(todo)
        db.session.flush()
        inquiry.followup_todo_id = todo.id


def assign_client(account, org, agent, actor):
    """Caller holds the account lock, also used by inquiry creation and claims."""
    previous = account.agent_id
    rows = ClientBrowseInquiry.query.filter_by(organization_id=org.id, account_id=account.id).order_by(ClientBrowseInquiry.id).all()
    contact = None
    if agent and rows:
        phone = next((r.phone for r in reversed(rows) if r.phone), '')
        contact = ensure_contact(account, org, agent, phone)
    account.agent_id = agent.id if agent else None
    for row in rows:
        row.agent_id = account.agent_id
        if agent:
            record_inquiry(row, account, contact)
        elif row.followup_todo_id:
            todo = UserTodo.query.filter_by(id=row.followup_todo_id, organization_id=org.id, completed=False).first()
            if todo:
                row.followup_todo_id = None
                db.session.delete(todo)
    db.session.add(AuditEvent(organization_id=org.id, actor_id=actor.id if actor else None,
        event_type='client_inquiry_assignment', description='Changed app client inbox assignment.',
        event_data={'account_id': account.id, 'from_agent_id': previous, 'to_agent_id': account.agent_id}))


def append_reply(inquiry, body, author, request_id):
    prior = ClientInquiryMessage.query.filter_by(organization_id=inquiry.organization_id,
        inquiry_id=inquiry.id, sender='agent', request_id=request_id).first()
    if not prior:
        db.session.add(ClientInquiryMessage(organization_id=inquiry.organization_id,
            inquiry_id=inquiry.id, sender='agent', body=body, author_user_id=author.id, request_id=request_id))
