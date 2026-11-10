"""Durable Home Facts jobs. RQ wakes work; database rows survive queue outages."""
from datetime import datetime, timedelta
import logging

from sqlalchemy import or_

from models import (db, HomeFactsJob, HomeFactsRevision, HomeFactsGrant,
    TransactionCommunication, CommunicationDeliveryAttempt, Organization, User,
    Notification, DeviceToken, TransactionParticipant, ClientPortalAccess)
from services.home_facts import responses as hf
from services.home_facts.access import set_org, active, serializer

log=logging.getLogger(__name__)


def kick(org_id):
    try:
        from redis import Redis
        from rq import Queue
        from config import Config
        Queue('home_facts',connection=Redis.from_url(Config.REDIS_URL,socket_connect_timeout=2,socket_timeout=2)).enqueue(
            'jobs.home_facts.process_org',org_id=org_id,job_timeout=300)
    except Exception:
        log.warning('Home Facts wake-up unavailable; durable worker will recover organization %s',org_id)


def process_org(org_id):
    from app import app
    with app.app_context():
        return drain(org_id)


def drain(org_id,limit=30):
    set_org(org_id)
    org=db.session.get(Organization,org_id)
    if not org or org.status!='active':
        return 0
    now=datetime.utcnow()
    stale=HomeFactsJob.query.filter(HomeFactsJob.organization_id==org_id,
        HomeFactsJob.state=='processing',HomeFactsJob.claimed_at<now-timedelta(minutes=10)).all()
    for row in stale:
        row.state='ambiguous' if row.kind=='email' else 'queued'
        row.last_error='Delivery unconfirmed. Review before sending again.' if row.kind=='email' else 'Worker interrupted; retrying.'
        if row.kind=='email':
            _ambiguous_email(row,org_id)
    db.session.commit();set_org(org_id)
    processed=0
    while processed<limit:
        ids=[j.id for j in HomeFactsJob.query.filter(HomeFactsJob.organization_id==org_id,
            HomeFactsJob.state=='queued',HomeFactsJob.available_at<=datetime.utcnow()).order_by(HomeFactsJob.created_at).limit(limit-processed)]
        if not ids:
            break
        for ident in ids:
            process_one(org_id,ident)
        processed+=len(ids)
    return processed



def process_one(org_id,ident):
    set_org(org_id)
    claimed=HomeFactsJob.query.filter_by(id=ident,organization_id=org_id,state='queued').update({
        'state':'processing','claimed_at':datetime.utcnow(),'attempts':HomeFactsJob.attempts+1},synchronize_session=False)
    db.session.commit();set_org(org_id)
    if not claimed:
        return
    job=HomeFactsJob.query.filter_by(id=ident,organization_id=org_id).one()
    try:
        response=hf.load_response(job.response_id,org_id)
        {'generate':_generate,'email':_email,'push':_push}[job.kind](job,response)
        if job.state=='processing':
            job.state='done';job.completed_at=datetime.utcnow();job.last_error=None
        db.session.commit()
    except Exception:
        kind=job.kind
        log.exception('Home Facts %s job failed: %s',kind,ident)
        db.session.rollback();set_org(org_id)
        job=HomeFactsJob.query.filter_by(id=ident,organization_id=org_id).one()
        job.state='ambiguous' if kind=='email' else 'failed' if job.attempts>=5 else 'queued'
        job.available_at=datetime.utcnow()+timedelta(seconds=min(3600,30*2**job.attempts))
        job.last_error='Delivery unconfirmed. Review before sending again.' if kind=='email' else 'Could not finish. Your saved response is safe.'
        if kind=='email':
            _ambiguous_email(job,org_id)
        db.session.commit()
    finally:
        set_org(org_id)


def _ambiguous_email(job,org_id):
    comm=TransactionCommunication.query.filter_by(id=job.payload.get('communication_id'),organization_id=org_id).first()
    if comm and comm.status!='sent':
        comm.status='ambiguous';comm.last_error=job.last_error
        CommunicationDeliveryAttempt.query.filter_by(organization_id=org_id,communication_id=comm.id,status='sending').update(
            {'status':'ambiguous','finished_at':datetime.utcnow()},synchronize_session=False)


def _generate(job,response):
    from services.home_facts.rendering import questionnaire_pdf,report_pdf
    from services.home_facts.files import store
    snapshot=HomeFactsRevision.query.filter_by(organization_id=response.organization_id,
        response_id=response.id,revision=job.payload['revision']).one()
    existing={a.kind for a in hf.artifacts_for(response,snapshot.revision)}
    titles={'questionnaire':'Completed Home Facts','appraiser':'Appraiser Property Brief','buyer':'Buyer Home Guide'}
    for kind,title in titles.items():
        if kind in existing: continue
        content=(questionnaire_pdf(snapshot.answers,response.schema_version) if kind=='questionnaire' else
            report_pdf(snapshot.answers,kind,address=snapshot.answers.get('property_address') or response.transaction.street_address,revision=snapshot.revision))
        store(response,kind=kind,revision=snapshot.revision,filename=f'{title} - v{snapshot.revision}.pdf',
            content_type='application/pdf',content=content,generation_key=f'{kind}:{snapshot.revision}:v1')
    hf.event(response,'reports_ready','system',revision=snapshot.revision)
    hf.enqueue(response,'push',f'ready:{response.id}:{snapshot.revision}',{'audience':'agent','event':'reports_ready'})


def _email(job,response):
    org_id=response.organization_id
    from config import Config
    from services.email_send_guard import skip_outbound_send
    from services.offer_summary_email import resolve_sender,_gmail_integration,_send_via_gmail,_from_name
    from services.transaction_comms import TransactionCommsService
    comm=TransactionCommunication.query.filter_by(id=job.payload['communication_id'],organization_id=response.organization_id,
        transaction_id=response.transaction_id).with_for_update().one()
    if comm.status=='sent': return
    if comm.status!='queued':
        job.state='ambiguous';return
    grant=HomeFactsGrant.query.filter_by(id=job.payload['grant_id'],organization_id=response.organization_id,response_id=response.id).first()
    try: active(grant)
    except hf.HomeFactsError:
        comm.status='cancelled';job.state='cancelled';return
    expected=TransactionCommsService.compute_payload_hash(channel=comm.channel,subject=comm.subject,body=comm.body,
        recipients=comm.recipients,cc=comm.cc,attachment_refs=comm.attachment_refs)
    if expected!=comm.approved_payload_hash:
        raise RuntimeError('Approved email changed')
    if skip_outbound_send(grant.email):
        comm.status='cancelled';job.state='cancelled';job.last_error='Email sending is disabled in this environment.';return
    agent=User.query.filter_by(id=comm.approved_by_user_id,organization_id=response.organization_id).first()
    if not agent: raise RuntimeError('Approving agent unavailable')
    from services.transaction_auth import require_transaction_access,CAP_SEND_COMMS
    if not require_transaction_access(response.transaction,CAP_SEND_COMMS,agent).allowed:
        comm.status='cancelled';job.state='cancelled';return
    sender=resolve_sender(agent)
    attempt=CommunicationDeliveryAttempt(organization_id=response.organization_id,communication_id=comm.id,
        status='sending',provider=sender['via'],attempt_number=1)
    db.session.add(attempt);comm.status='sending'
    db.session.commit();set_org(org_id)
    if sender['via']=='gmail':
        message_id=_send_via_gmail(_gmail_integration(agent),html=comm.body,text=comm.communication_metadata['text'],
            subject=comm.subject,recipients=comm.recipients,copies=[],transaction_id=response.transaction_id)
    else:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail,Email,To,CustomArg,TrackingSettings,ClickTracking
        if not Config.SENDGRID_API_KEY: raise RuntimeError('Email provider unavailable')
        organization=db.session.get(Organization,response.organization_id)
        message=Mail(from_email=Email(Config.CLIENT_EMAIL_FROM_EMAIL,_from_name(agent,organization)),
            to_emails=[To(grant.email)],subject=comm.subject,html_content=comm.body,
            plain_text_content=comm.communication_metadata['text'])
        if agent.email: message.reply_to=Email(agent.email)
        message.add_custom_arg(CustomArg('home_facts_receipt',serializer().dumps({'org':response.organization_id,'communication':comm.id})))
        message.tracking_settings=TrackingSettings(click_tracking=ClickTracking(enable=False,enable_text=False))
        result=SendGridAPIClient(Config.SENDGRID_API_KEY).send(message)
        if result.status_code not in (200,201,202): raise RuntimeError('Email provider rejected request')
        headers=result.headers or {}
        message_id=headers.get('X-Message-Id') or headers.get('X-Message-ID')
    comm.status='sent';comm.provider_message_id=message_id
    attempt.status='sent';attempt.provider_message_id=message_id;attempt.finished_at=datetime.utcnow()
    hf.event(response,'email_accepted','system',recipient=grant.email,purpose=comm.purpose,
        communication_id=comm.id,provider=sender['via'])


def _push(job,response):
    org_id=response.organization_id
    from services.apns_client import send_payload
    from services.transaction_auth import can_view_transaction
    from services.notification_service import is_channel_enabled
    from jobs.apns_push import apns_configured
    audience=job.payload['audience']; kind=job.payload['event']
    title={'invite':'Your agent requested Home Facts','request_review':'Review your home reports',
        'submitted':'Home Facts submitted','reports_ready':'Home Facts reports are ready',
        'changes_requested':'Your agent requested a Home Facts update'}[kind]
    payload=dict(job.payload)
    if audience=='agent':
        users=User.query.filter_by(organization_id=response.organization_id).all()
        users=[u for u in users if can_view_transaction(response.transaction,u).allowed]
        if not payload.get('in_app_recorded'):
            for user in users:
                if is_channel_enabled(user.id,'document_review','in_app'):
                    db.session.add(Notification(organization_id=response.organization_id,user_id=user.id,
                        category='document_review',title=title,body=response.transaction.street_address,
                        action_url=f'/transactions/{response.transaction_id}/home-facts'))
            payload['in_app_recorded']=True;job.payload=payload
            db.session.commit();set_org(org_id)
        devices=DeviceToken.query.filter(DeviceToken.organization_id==response.organization_id,
            DeviceToken.audience=='agent',DeviceToken.user_id.in_([u.id for u in users])).all()
    else:
        grants=HomeFactsGrant.query.filter_by(organization_id=response.organization_id,response_id=response.id,audience='seller',revoked_at=None).all()
        ids={g.participant_id for g in grants if g.expires_at>datetime.utcnow()}
        if payload.get('participant_ids'):ids &= set(payload['participant_ids'])
        active_ids={a.participant_id for a in ClientPortalAccess.query.filter(ClientPortalAccess.organization_id==response.organization_id,
            ClientPortalAccess.transaction_id==response.transaction_id,ClientPortalAccess.is_active.is_(True),ClientPortalAccess.participant_id.in_(ids)).all()}
        active_ids &= {p.id for p in TransactionParticipant.query.filter(
            TransactionParticipant.organization_id==response.organization_id,TransactionParticipant.transaction_id==response.transaction_id,
            TransactionParticipant.id.in_(active_ids),TransactionParticipant.role.in_(('seller','co_seller'))).all()}
        devices=DeviceToken.query.filter(DeviceToken.organization_id==response.organization_id,
            DeviceToken.audience=='client',DeviceToken.participant_id.in_(active_ids)).all()
    devices=[d for d in devices if not d.expires_at or d.expires_at>datetime.utcnow()]
    if not devices:return
    if not apns_configured():raise RuntimeError('Push provider unavailable')
    delivered=set(payload.get('delivered_devices',[]))
    failed=False
    for device in devices:
        if device.id in delivered:continue
        notification={'aps':{'alert':{'title':title,'body':response.transaction.street_address},'sound':'default'},
            'kind':'home_facts','home_facts_id':response.id,'transaction_id':response.transaction_id,
            'participant_id':device.participant_id}
        if send_payload(device.token,notification,audience=audience,environment=device.environment):
            delivered.add(device.id);payload['delivered_devices']=sorted(delivered);job.payload=dict(payload)
            db.session.commit();set_org(org_id)
        else:failed=True
    if failed:raise RuntimeError('Some Home Facts notifications need retry')
