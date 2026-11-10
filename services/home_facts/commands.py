"""Agent commands shared by CRM and the native agent app."""
from datetime import datetime

from models import db, TransactionParticipant, HomeFactsGrant, HomeFactsRevision, HomeFactsJob
from services.home_facts import responses as hf
from services.home_facts.access import create_grant
from services.home_facts.mail import valid_email, queue_mail


def execute(response,user,operation,data):
    response=hf.load_response(response.id,response.organization_id,lock=True)
    request_id=data.get('mutation_id')
    actor=f'agent:{user.id}'
    payload={'operation':operation,'data':data}
    if hf._mutation(response,actor,request_id,payload):
        return response
    if operation in ('invite','request_review'):
        if operation=='request_review':
            hf.report_digest(response,response.submitted_revision)
        participant_ids=data.get('participant_ids')
        if not isinstance(participant_ids,list) or not participant_ids or len(participant_ids)>10:
            raise hf.HomeFactsError('Choose at least one seller.')
        if any(type(i) is not int for i in participant_ids):
            raise hf.HomeFactsError('Choose a seller from this transaction.')
        participants=TransactionParticipant.query.filter(
            TransactionParticipant.id.in_(participant_ids),TransactionParticipant.organization_id==response.organization_id,
            TransactionParticipant.transaction_id==response.transaction_id,
            TransactionParticipant.role.in_(('seller','co_seller'))).all()
        if len(participants)!=len(set(participant_ids)):
            raise hf.HomeFactsError('Choose sellers from this transaction.')
        for participant in participants:
            email=valid_email(participant.display_email)
            grant,token=create_grant(response,audience='seller',email=email,participant_id=participant.id)
            queue_mail(response,user,grant=grant,token=token,kind='invitation' if operation=='invite' else 'review',request_id=request_id)
        hf.enqueue(response,'push',f'{operation}:{request_id}',{'audience':'client','event':operation,'participant_ids':participant_ids})
    elif operation=='reopen':
        hf.reopen(response,user.id,data.get('note'))
    elif operation=='review':
        hf.review(response,actor_kind='agent',actor_id=user.id,revision=data.get('revision'),
            artifact_hash=data.get('artifact_hash'),offline_note=data.get('offline_note'))
    elif operation=='share':
        if response.state!='submitted':
            raise hf.HomeFactsError('Submit and review the current response before sharing.')
        snapshot=HomeFactsRevision.query.filter_by(organization_id=response.organization_id,response_id=response.id,
            revision=response.submitted_revision).first()
        report_hash=hf.report_digest(response,response.submitted_revision)
        if not snapshot or not snapshot.agent_reviewed_at or not snapshot.seller_reviewed_at or snapshot.reviewed_artifact_hash!=report_hash:
            raise hf.HomeFactsError('Review both reports with the seller and approve this version before sharing.','review_required',409)
        if data.get('artifact_hash')!=report_hash:
            raise hf.HomeFactsError('Open the current reports before sending.','stale_review',409)
        ids=data.get('artifact_ids')
        if not isinstance(ids,list) or not ids or len(ids)>30 or any(not isinstance(v,str) for v in ids):
            raise hf.HomeFactsError('Select the documents to send.')
        attachment_ids={a['id'] for a in snapshot.attachments}
        artifacts={a.id:a for a in hf.artifacts_for(response)
            if a.revision==response.submitted_revision or a.id in attachment_ids}
        selected=[]
        for ident in dict.fromkeys(ids):
            artifact=artifacts.get(ident)
            if not artifact or artifact.kind not in ('buyer','appraiser','attachment'):
                raise hf.HomeFactsError('Choose current reports or reviewed attachments. The private questionnaire cannot be shared.')
            selected.append({'id':artifact.id,'digest':artifact.digest})
        if any(artifacts[a['id']].kind=='attachment' for a in selected) and data.get('attachments_reviewed') is not True:
            raise hf.HomeFactsError('Review attachments for private information and confirm they can be shared.')
        recipients=data.get('recipients')
        if not isinstance(recipients,list) or not 1<=len(recipients)<=20:
            raise hf.HomeFactsError('Add between one and 20 recipients.')
        message=data.get('message','')
        if not isinstance(message,str) or len(message)>5000:
            raise hf.HomeFactsError('Keep the message under 5,000 characters.')
        for email in dict.fromkeys(valid_email(v) for v in recipients):
            grant,token=create_grant(response,audience='recipient',email=email,artifacts=selected)
            queue_mail(response,user,grant=grant,token=token,kind='share',request_id=request_id,custom_message=message)
        hf.event(response,'sharing_approved','agent',user.id,revision=response.submitted_revision,artifacts=selected,
            attachments_reviewed=data.get('attachments_reviewed') is True)
    elif operation=='revoke':
        grant=HomeFactsGrant.query.filter_by(id=data.get('grant_id'),response_id=response.id,organization_id=response.organization_id).first()
        if not grant:
            raise hf.HomeFactsError('This invitation is not available.','not_found',404)
        grant.revoked_at=datetime.utcnow(); grant.session_version+=1
        hf.event(response,'access_revoked','agent',user.id,grant_id=grant.id)
    elif operation=='retry_generation':
        if response.submitted_revision is None:
            raise hf.HomeFactsError('The seller has not submitted a response yet.')
        job=HomeFactsJob.query.filter_by(organization_id=response.organization_id,response_id=response.id,
            dedupe_key=f'generate:{response.id}:{response.submitted_revision}').with_for_update().first()
        if job and job.state=='failed':
            job.state='queued'; job.available_at=datetime.utcnow(); job.last_error=None
    else:
        raise hf.HomeFactsError('Unknown Home Facts action.')
    hf._record_mutation(response,actor,request_id,payload)
    return response
