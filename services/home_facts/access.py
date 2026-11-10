"""Home Facts grants never authorize the full client portal or agent APIs."""
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import text

from models import db, HomeFactsGrant, Organization, TransactionParticipant
from services.home_facts.responses import HomeFactsError, identifier, event, load_response


def set_org(org_id):
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {'org':str(org_id)})


def serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='home-facts-grant-v1')


def active(grant):
    if not grant or grant.revoked_at or grant.expires_at <= datetime.utcnow():
        raise HomeFactsError('This link has expired or was revoked. Ask your agent for a new link.', 'access_expired', 401)
    org = db.session.get(Organization,grant.organization_id)
    if not org or org.status != 'active':
        raise HomeFactsError('This link is no longer available.','access_expired',401)
    response=load_response(grant.response_id,grant.organization_id)
    if grant.audience=='seller':
        participant=TransactionParticipant.query.filter_by(id=grant.participant_id,
            organization_id=grant.organization_id,transaction_id=response.transaction_id).first()
        if not participant or participant.role not in ('seller','co_seller'):
            raise HomeFactsError('This invitation is no longer active.','access_expired',401)
    return grant


def create_grant(response, *, audience, email, participant_id=None, artifacts=None):
    if audience not in ('seller','recipient'):
        raise HomeFactsError('Unknown access type.')
    secret=secrets.token_urlsafe(32)
    grant=HomeFactsGrant(id=identifier(),organization_id=response.organization_id,response_id=response.id,
        participant_id=participant_id,audience=audience,email=email,
        token_hash=hashlib.sha256(secret.encode()).hexdigest(),
        artifacts=artifacts or [],expires_at=datetime.utcnow()+timedelta(days=30))
    db.session.add(grant); db.session.flush()
    token=serializer().dumps({'id':grant.id,'org':grant.organization_id,'secret':secret,'version':grant.session_version})
    return grant,token


def activate(token):
    try:
        data=serializer().loads(token,max_age=30*86400)
        org=int(data['org'])
        set_org(org)
        grant=HomeFactsGrant.query.filter_by(id=data['id'],organization_id=org).with_for_update().first()
        active(grant)
        expected=hashlib.sha256(data['secret'].encode()).hexdigest()
        if not hmac.compare_digest(grant.token_hash,expected) or grant.session_version!=data['version']:
            raise ValueError()
    except (BadSignature,KeyError,ValueError,TypeError,AttributeError):
        raise HomeFactsError('This link is no longer available. Ask your agent for a new link.','access_expired',401)
    grant.token_used_at=datetime.utcnow()
    event(load_response(grant.response_id,org),'opened',grant.audience,grant.participant_id or grant.id)
    return grant


def session_grant(claims):
    try:
        org=int(claims['org'])
        set_org(org)
        grant=HomeFactsGrant.query.filter_by(id=claims['id'],organization_id=org).first()
        active(grant)
        if grant.session_version!=claims['version']:
            raise ValueError()
        return grant
    except (KeyError,TypeError,ValueError):
        raise HomeFactsError('Open the invitation from your email to continue.','access_expired',401)
