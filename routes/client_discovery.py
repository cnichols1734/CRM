"""Public brokerage profiles and browsing accounts, independent of deal grants."""
from datetime import datetime
from functools import wraps
import os
from ipaddress import ip_address
import hashlib
import hmac
import re
import secrets
from html import escape
import time
import uuid

from flask import Blueprint, current_app, jsonify, request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import text, func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

from models import (db, Organization, User, Contact, Interaction, UserTodo,
                    ClientBrowseAccount, ClientBrowseInquiry, ClientBrowseRateLimit, TransactionParticipant, ClientPortalAccess, Transaction)
from services.client_portal_auth import org_branding
from services import repliers_listings

client_discovery_bp = Blueprint('client_discovery', __name__, url_prefix='/api/client/v1/discovery')
TTL = 30 * 24 * 60 * 60
DUMMY_HASH = generate_password_hash('unused-password-for-constant-work')


def error(message, status=400):
    return jsonify(error=message), status


def org_context(org_id):
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text("SELECT set_config('app.current_org_id', :id, true)"), {'id': str(org_id)})


def serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='client-browse-v1')


def client_ip():
    # Railway overwrites X-Real-IP at its HTTP edge. Trust it only there.
    if os.environ.get('RAILWAY_ENVIRONMENT_ID'):
        try:
            return str(ip_address(request.headers.get('X-Real-IP', '')))
        except ValueError:
            pass
    return request.remote_addr


def limited(scope, limit, seconds, identity=None):
    now = int(time.time())
    raw = f'{scope}:{identity or client_ip()}:{now // seconds}'
    key = hmac.new(str(current_app.config['SECRET_KEY']).encode(), raw.encode(), hashlib.sha256).hexdigest()
    # Database counters apply across workers. Store no raw IP or email.
    db.session.query(ClientBrowseRateLimit).filter(ClientBrowseRateLimit.expires_at < now).delete()
    if db.engine.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(ClientBrowseRateLimit).values(key=key, hits=1, expires_at=now + seconds)
    db.session.execute(stmt.on_conflict_do_update(index_elements=['key'], set_={'hits': ClientBrowseRateLimit.hits + 1}))
    count = db.session.get(ClientBrowseRateLimit, key).hits
    db.session.commit()
    return count > limit


def public_org(slug):
    org = Organization.query.filter_by(slug=slug, status='active').first()
    if not org:
        return None
    settings = org.client_app_settings or {}
    return org if settings.get('enabled', org.slug == 'origen-realty') else None


def agent_for(org, supplied=None):
    settings = org.client_app_settings or {}
    selected = supplied or settings.get('agent_id')
    query = User.query.filter_by(organization_id=org.id)
    if selected:
        return query.filter_by(id=selected).first()
    return query.filter_by(org_role='owner').order_by(User.id).first()


def profile(org, agent):
    brand = org_branding(org)
    if org.slug == 'origen-realty' and not org.brand_accent:
        brand['accent'] = '#14807b'
    brand.update(id=str(org.id), slug=org.slug)
    return {'branding': brand, 'agent': {
        'id': agent.id, 'name': f'{agent.first_name} {agent.last_name}'.strip(),
        'phone': agent.phone or '',
    } if agent else None}


def payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def field(data, name, maximum):
    value = data.get(name, '')
    return value.strip() if isinstance(value, str) and len(value) <= maximum else ''


def account_payload(account):
    return {'id': account.id, 'name': account.name, 'email': account.email, 'saved_ids': account.saved_ids,
            'agent_id': account.agent_id, 'email_verified': account.email_verified_at is not None}


def authenticated(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        try:
            if not header.startswith('Bearer '):
                raise BadSignature('missing')
            claims = serializer().loads(header[7:], max_age=TTL)
            if not isinstance(claims, dict) or claims.get('type') != 'browse':
                raise BadSignature('type')
            org = db.session.get(Organization, int(claims['org']))
            if not org or not public_org(org.slug):
                raise BadSignature('org')
            org_context(org.id)
            account = ClientBrowseAccount.query.filter_by(id=claims['id'], organization_id=org.id).first()
            if not account or account.session_version != claims['version']:
                raise BadSignature('account')
        except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
            return error('Sign in to your home-search account again.', 401)
        return view(account, org, *args, **kwargs)
    return wrapped


@client_discovery_bp.before_request
def bounded_request():
    if request.content_length and request.content_length > 32768:
        return error('This request is too large.', 413)


@client_discovery_bp.after_request
def private_responses(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@client_discovery_bp.get('/brokerages/<slug>')
def brokerage(slug):
    org = public_org(slug)
    if not org:
        return error('This brokerage link is unavailable.', 404)
    org_context(org.id)
    agent_id = request.args.get('agent', type=int)
    if 'agent' in request.args and (not agent_id or agent_id < 1):
        return error('This agent link is invalid.')
    agent = agent_for(org, agent_id)
    if not agent:
        return error('This agent link is unavailable.', 404)
    return jsonify(profile(org, agent))


@client_discovery_bp.post('/accounts')
@client_discovery_bp.post('/session')
def sign_in():
    if limited('auth', 30, 900):
        return error('Too many attempts. Try again in 15 minutes.', 429)
    data = payload()
    org = public_org(field(data, 'brokerage', 100))
    if not org:
        return error('This brokerage link is unavailable.', 404)
    org_context(org.id)
    email = field(data, 'email', 120).lower()
    password = data.get('password', '')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or not isinstance(password, str) or not 10 <= len(password) <= 128:
        return error('Enter an email and a password with 10 to 128 characters.')
    account = ClientBrowseAccount.query.filter_by(organization_id=org.id, email=email).first()
    creating = request.path.endswith('/accounts')
    if creating:
        if account:
            return error('An account already uses this email. Sign in instead.', 409)
        name = field(data, 'name', 160)
        agent_id = data.get('agent_id')
        if agent_id is not None and (type(agent_id) is not int or agent_id < 1):
            return error('Choose a valid agent link.')
        agent = agent_for(org, agent_id)
        if not name or not agent:
            return error('Enter your name and use a valid brokerage link.')
        account = ClientBrowseAccount(organization_id=org.id, email=email, name=name,
            password_hash=generate_password_hash(password), agent_id=agent.id)
        db.session.add(account)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            return error('An account already uses this email. Sign in instead.', 409)
    elif not check_password_hash(account.password_hash if account else DUMMY_HASH, password) or account is None:
        return error('Email or password is incorrect.', 401)
    token = serializer().dumps({'type': 'browse', 'id': account.id, 'org': org.id, 'version': account.session_version})
    result = {'token': token, 'account': account_payload(account), **profile(org, agent_for(org, account.agent_id))}
    db.session.commit()
    return jsonify(result), 201 if creating else 200


@client_discovery_bp.get('/account')
@authenticated
def account_get(account, org):
    return jsonify(account=account_payload(account), **profile(org, agent_for(org, account.agent_id)))


@client_discovery_bp.delete('/session')
@authenticated
def sign_out(account, org):
    account.session_version += 1
    db.session.commit()
    return jsonify(ok=True)


@client_discovery_bp.delete('/account')
@authenticated
def delete_account(account, org):
    password = payload().get('password', '')
    if not isinstance(password, str) or len(password) > 128 or not check_password_hash(account.password_hash, password):
        return error('Password is incorrect.', 403)
    ClientBrowseInquiry.query.filter_by(organization_id=org.id, account_id=account.id).delete()
    db.session.delete(account)
    db.session.commit()
    return jsonify(ok=True)


@client_discovery_bp.put('/saved')
@authenticated
def save_homes(account, org):
    data = payload()
    changes = data.get('changes')
    if not isinstance(changes, dict) or len(changes) > 500 or any(
        not isinstance(i, str) or not (repliers_listings.valid_id(i) or re.fullmatch(r'h(?:0[1-9]|1[0-5])', i)) or type(wanted) is not bool
        for i, wanted in changes.items()
    ):
        return error('Choose valid homes from the app.')
    account = ClientBrowseAccount.query.filter_by(id=account.id, organization_id=org.id).with_for_update().populate_existing().one()
    ids = list(account.saved_ids or [])
    for listing_id, wanted in changes.items():
        ids = [i for i in ids if i != listing_id]
        if wanted:
            ids.insert(0, listing_id)
    if len(ids) > 500:
        return error('Your saved collection can hold up to 500 homes.')
    account.saved_ids = ids
    result = account_payload(account)
    db.session.commit()
    return jsonify(account=result)


@client_discovery_bp.get('/inquiries')
@authenticated
def inquiries(account, org):
    rows = ClientBrowseInquiry.query.filter_by(organization_id=org.id, account_id=account.id).order_by(ClientBrowseInquiry.id.desc()).limit(100).all()
    return jsonify(inquiries=[{'id': r.id, 'kind': r.kind, 'body': r.body, 'reply': r.reply,
        'listing_id': r.listing_id, 'listing_snapshot': r.listing_snapshot,
        'created_at': r.created_at.isoformat() + 'Z'} for r in rows])


@client_discovery_bp.post('/inquiries')
@authenticated
def send_inquiry(account, org):
    data = payload()
    request_id = field(data, 'request_id', 36)
    try:
        uuid.UUID(request_id)
    except ValueError:
        return error('A request ID is required.')
    prior = ClientBrowseInquiry.query.filter_by(organization_id=org.id, account_id=account.id, request_id=request_id).first()
    if prior:
        return jsonify(id=prior.id), 200
    account_id = account.id
    if limited('inquiry', 20, 86400, str(account_id)):
        return error('You have reached today’s inquiry limit. Try again tomorrow.', 429)
    org_context(org.id)
    account = ClientBrowseAccount.query.filter_by(id=account_id, organization_id=org.id).with_for_update().populate_existing().one()
    prior = ClientBrowseInquiry.query.filter_by(organization_id=org.id, account_id=account.id, request_id=request_id).first()
    if prior:
        return jsonify(id=prior.id), 200
    kind = field(data, 'kind', 20)
    listing_id = field(data, 'listing_id', 80) or None
    body = field(data, 'body', 4000)
    phone = field(data, 'phone', 20)
    if kind not in ('question', 'showing', 'contact') or not body or data.get('consent') is not True:
        return error('Add a message and confirm the brokerage may contact you.')
    listing_snapshot = None
    if listing_id and repliers_listings.valid_id(listing_id):
        try:
            listing = repliers_listings.get_listing(listing_id)
        except repliers_listings.ListingError as exc:
            return error(str(exc), exc.status)
        listing_snapshot = {key: listing[key] for key in ('id', 'street', 'city', 'state', 'zip', 'price', 'mls_number', 'source')}
    elif listing_id and not re.fullmatch(r'h(?:0[1-9]|1[0-5])', listing_id):
        return error('Choose a home from the app.')
    agent = agent_for(org, account.agent_id)
    if not agent:
        return error('Your agent is unavailable. Contact your brokerage.', 409)
    # Never attach an existing CRM contact by a self-entered email address.
    contact = Contact.query.filter_by(id=account.contact_id, organization_id=org.id).first() if account.contact_id else None
    if contact and contact.user_id != agent.id:
        if account.owns_contact:
            contact.user_id = agent.id
        else:
            contact = None
    if not contact:
        if org.is_at_contact_limit:
            return error('The brokerage cannot receive new inquiries right now.', 409)
        parts = account.name.split(' ', 1)
        contact = Contact(organization_id=org.id, user_id=agent.id, created_by_id=agent.id,
            first_name=parts[0][:80], last_name=(parts[1] if len(parts) > 1 else '')[:80], email=account.email,
            phone=phone, notes='AgentFlow app inquiry. Email provided by client, not verified.')
        db.session.add(contact)
        db.session.flush()
        account.contact_id = contact.id
        account.owns_contact = True
    elif phone:
        contact.phone = phone
    context = (f"Repliers sample: {listing_snapshot['street']}, {listing_snapshot['city']}, {listing_snapshot['state']} · {listing_id}"
               if listing_snapshot else f'SAMPLE home {listing_id}')
    notes = f'AgentFlow {kind} inquiry' + (f' about {context}' if listing_id else '') + f'\n{body}'
    inquiry = ClientBrowseInquiry(organization_id=org.id, account_id=account.id, agent_id=agent.id,
        request_id=request_id, kind=kind, listing_id=listing_id, listing_snapshot=listing_snapshot, body=body)
    db.session.add(inquiry)
    db.session.add(Interaction(organization_id=org.id, contact_id=contact.id, user_id=agent.id,
        type='Note', notes=notes, date=datetime.utcnow()))
    db.session.add(UserTodo(organization_id=org.id, user_id=agent.id,
        text=f'Follow up with {account.name}: {kind} inquiry from AgentFlow. Contact #{contact.id}.'[:500]))
    try:
        db.session.flush()
        inquiry_id = inquiry.id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        org_context(org.id)
        prior = ClientBrowseInquiry.query.filter_by(organization_id=org.id, account_id=account_id, request_id=request_id).first()
        if prior:
            return jsonify(id=prior.id), 200
        raise
    return jsonify(id=inquiry_id), 201


@client_discovery_bp.post('/connection')
@authenticated
def connect_deal(account, org):
    from services.client_portal_auth import load_access_from_jwt
    from services.portal_service import CLIENT_PORTAL_ROLES
    access, failure = load_access_from_jwt(field(payload(), 'deal_token', 4096))
    if failure or not access or access.organization_id != org.id:
        return error('This deal belongs to a different account or brokerage.', 403)
    tx, participant = access.transaction, access.participant
    if (not tx or tx.organization_id != org.id or not participant
            or participant.transaction_id != tx.id or participant.role not in CLIENT_PORTAL_ROLES):
        return error('This deal connection is unavailable.', 403)
    agent = agent_for(org, tx.created_by_id)
    if not agent:
        return error('This agent is unavailable.', 409)
    account.agent_id = agent.id
    if account.contact_id and account.owns_contact:
        contact = Contact.query.filter_by(id=account.contact_id, organization_id=org.id).first()
        if contact:
            contact.user_id = agent.id
    account.linked_access_ids = list(set((account.linked_access_ids or []) + [access.id]))
    result = {'account': account_payload(account), **profile(org, agent)}
    db.session.commit()
    return jsonify(result)


def verification_digest(account, code):
    return hmac.new(str(current_app.config['SECRET_KEY']).encode(),
                    f'client-email:{account.id}:{account.email}:{code}'.encode(), hashlib.sha256).hexdigest()


def send_verification_email(account, code, org):
    from services.sendgrid_outbound import _send_html
    return _send_html(account.email, 'Verify your AgentFlow email',
        f'<p>Verify your email for {escape(org.name)} in AgentFlow.</p>'
        f'<p style="font-size:28px;letter-spacing:6px"><strong>{code}</strong></p>'
        '<p>This code expires in 15 minutes. Enter it in the app to connect your deals.</p>'
        '<p>If you did not request this code, you can ignore this email.</p>')


@client_discovery_bp.post('/email/verification')
@authenticated
def request_verification(account, org):
    if account.email_verified_at:
        return jsonify(account=account_payload(account))
    aid, oid = account.id, org.id
    if limited('verification', 5, 3600, str(aid)):
        return error('Too many codes requested. Try again in an hour.', 429)
    org_context(oid)
    account = ClientBrowseAccount.query.filter_by(id=aid, organization_id=oid).with_for_update().populate_existing().one()
    code = f'{secrets.randbelow(1000000):06d}'
    account.verification_hash = verification_digest(account, code)
    account.verification_expires_at = int(time.time()) + 900
    account.verification_attempts = 0
    if not send_verification_email(account, code, org):
        db.session.rollback()
        return error('We could not send your verification email. Try again shortly.', 503)
    db.session.commit()
    return jsonify(ok=True)


@client_discovery_bp.post('/email/verify')
@authenticated
def verify_email(account, org):
    account = ClientBrowseAccount.query.filter_by(id=account.id, organization_id=org.id).with_for_update().populate_existing().one()
    code = field(payload(), 'code', 6)
    if account.email_verified_at:
        return jsonify(account=account_payload(account))
    if (not account.verification_hash or (account.verification_expires_at or 0) <= int(time.time())
            or account.verification_attempts >= 5):
        return error('Request a new verification code.', 422)
    account.verification_attempts += 1
    if not hmac.compare_digest(account.verification_hash, verification_digest(account, code)):
        db.session.commit()
        return error('That code is incorrect. Check the email and try again.', 422)
    account.email_verified_at = datetime.utcnow()
    account.verification_hash = None
    account.verification_expires_at = None
    contacts = Contact.query.filter(Contact.organization_id == org.id,
        func.lower(func.trim(Contact.email)) == account.email).limit(2).all()
    if len(contacts) == 1:
        account.contact_id = contacts[0].id
        account.owns_contact = False
        if agent_for(org, contacts[0].user_id):
            account.agent_id = contacts[0].user_id
    result = {'account': account_payload(account), **profile(org, agent_for(org, account.agent_id))}
    db.session.commit()
    return jsonify(result)


def eligible_deals(account, org):
    from services.portal_service import CLIENT_PORTAL_ROLES
    participants = {}
    explicit = ClientPortalAccess.query.filter(ClientPortalAccess.organization_id == org.id,
        ClientPortalAccess.id.in_(account.linked_access_ids or []), ClientPortalAccess.is_active.is_(True)).all()
    for access in explicit:
        participants[access.participant_id] = (access.participant, access, 'invite')
    if account.email_verified_at:
        matches = TransactionParticipant.query.outerjoin(Contact, TransactionParticipant.contact_id == Contact.id).filter(
            TransactionParticipant.organization_id == org.id,
            TransactionParticipant.role.in_(CLIENT_PORTAL_ROLES), TransactionParticipant.user_id.is_(None),
            (TransactionParticipant.contact_id.is_(None)) | (Contact.organization_id == org.id),
            func.lower(func.trim(func.coalesce(func.nullif(Contact.email, ''), TransactionParticipant.email))) == account.email).all()
        for participant in matches:
            if participant.id in participants:
                continue
            access = ClientPortalAccess.query.filter_by(organization_id=org.id, participant_id=participant.id,
                transaction_id=participant.transaction_id).order_by(ClientPortalAccess.id.desc()).first()
            # A revoked grant stays revoked even when the email still matches.
            if access and not access.is_active:
                continue
            participants[participant.id] = (participant, access, 'email')
    return [(p, a, mode) for p, a, mode in participants.values()
        if p and p.organization_id == org.id and p.role in CLIENT_PORTAL_ROLES
        and p.transaction and p.transaction.organization_id == org.id
        and (not a or a.transaction_id == p.transaction_id)]


@client_discovery_bp.get('/deals')
@authenticated
def matched_deals(account, org):
    return jsonify(deals=[{'id': p.id, 'address': p.transaction.street_address,
        'city': p.transaction.city or '', 'status': p.transaction.status, 'role': p.role}
        for p, _, _ in eligible_deals(account, org)])


@client_discovery_bp.post('/deals/<int:participant_id>/session')
@authenticated
def open_matched_deal(account, org, participant_id):
    from services.client_portal_auth import issue_client_jwt
    account = ClientBrowseAccount.query.filter_by(id=account.id, organization_id=org.id).with_for_update().populate_existing().one()
    match = next((row for row in eligible_deals(account, org) if row[0].id == participant_id), None)
    if not match:
        return error('This deal is no longer connected to your account.', 404)
    participant, access, mode = match
    if not access:
        access = ClientPortalAccess(organization_id=org.id, transaction_id=participant.transaction_id,
            participant_id=participant.id, token=ClientPortalAccess.generate_token())
        db.session.add(access)
        db.session.flush()
    token = issue_client_jwt(access, browse_account=account, link_mode=mode)
    result = {'deal_session': {'token': token,
        'participant_first_name': participant.display_name.split(' ')[0],
        'role': 'buyer' if participant.role in ('buyer', 'co_buyer') else 'seller'}}
    db.session.commit()
    return jsonify(result)


@client_discovery_bp.get('/brokerages/<slug>/listings')
def search_listings(slug):
    if not public_org(slug):
        return error('Brokerage not found.', 404)
    if limited('listing-search', 90, 60):
        return error('Please wait a moment before searching again.', 429)
    try:
        ids = request.args.get('ids')
        if ids is not None:
            return jsonify(listings=repliers_listings.get_many(ids.split(',')))
        return jsonify(repliers_listings.search(request.args))
    except repliers_listings.ListingError as exc:
        return error(str(exc), exc.status)


@client_discovery_bp.get('/brokerages/<slug>/listings/<listing_id>')
def listing_detail(slug, listing_id):
    if not public_org(slug):
        return error('Brokerage not found.', 404)
    if limited('listing-detail', 90, 60):
        return error('Please wait a moment before loading another home.', 429)
    try:
        return jsonify(listing=repliers_listings.get_listing(listing_id))
    except repliers_listings.ListingError as exc:
        return error(str(exc), exc.status)
