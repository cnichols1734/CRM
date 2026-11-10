"""Branded, reviewed transaction mail. Each recipient receives a private link."""
from datetime import datetime
from email_validator import validate_email, EmailNotValidError
from flask import current_app, render_template

from models import db, TransactionCommunication
from services.email_chrome import brand_assets, masthead_html, footer_html
from services.home_facts.responses import HomeFactsError, digest, enqueue, event


def valid_email(value):
    try:
        return validate_email(value,check_deliverability=False).normalized
    except (EmailNotValidError,TypeError):
        raise HomeFactsError('Enter a valid recipient email address.')


def render_email(response,agent,organization,*,recipient,link,kind,custom_message=''):
    brand=brand_assets(organization)
    title={'invitation':'Tell us about your home','review':'Your home reports are ready to review','share':'Your property documents are ready'}[kind]
    action={'invitation':'Complete Home Facts','review':'Review your home reports','share':'View property documents'}[kind]
    detail={'invitation':'Share what you know about your home, its systems and improvements. Every question is optional. You can save, leave and come back whenever you need.',
        'review':'Your agent has prepared your Appraiser Property Brief and Buyer Home Guide. Review both and confirm they reflect what you shared before your agent sends them out.',
        'share':'Open the secure link below to view and download the documents your agent selected for this property.'}[kind]
    agent_name=' '.join(filter(None,[getattr(agent,'first_name',''),getattr(agent,'last_name','')])) or 'Your agent'
    context=dict(title=title,action=action,detail=detail,property_address=response.transaction.street_address,
        agent_name=agent_name,reply_to=getattr(agent,'email',''),link=link,kind=kind,custom_message=custom_message,
        masthead=masthead_html(name=brand['name'],mark_url=brand['mark_url'],eyebrow='Home Facts'),
        footer=footer_html(name=brand['name'],wordmark_url=brand['wordmark_url'],license_number=brand['license'],
            address=brand['address'],reason_line='Sent for your property transaction.',year=datetime.utcnow().year))
    html=render_template('email/home_facts.html',**context)
    text=f"{title}\n{response.transaction.street_address}\n\n{detail}\n\n{custom_message}\n\n{action}: {link}\n\n"
    if kind!='share':
        text+='Use this link in any browser, with no app download required. In ClientFlow, open your move and choose Home Facts. Both use the same saved answers.\n\n'
    text+=f'Questions? Reply to {agent_name}. Keep this private link to yourself.'
    return {'subject':f'{title} | {response.transaction.street_address}', 'html':html,'text':text,'recipient':recipient}


def queue_mail(response,agent,*,grant,token,kind,request_id,custom_message=''):
    from models import Organization
    from services.transaction_comms import TransactionCommsService
    base=current_app.config.get('APP_BASE_URL') or 'https://agentflow.origentechnolog.com'
    link=base.rstrip('/')+'/home-facts/open#token='+token
    email=render_email(response,agent,db.session.get(Organization,response.organization_id),
        recipient=grant.email,link=link,kind=kind,custom_message=custom_message)
    key=f'home-facts:{kind}:{request_id}:{digest(grant.email)}'
    comm=TransactionCommsService.queue_approved_send(transaction_id=response.transaction_id,
        organization_id=response.organization_id,channel='email',body=email['html'],subject=email['subject'],
        recipients=[grant.email],approved_by_user_id=agent.id,approved_payload_hash='',
        client_idempotency_key=key,communication_type='home_facts',purpose=kind,participant_id=grant.participant_id,
        communication_metadata={'home_facts_id':response.id,'grant_id':grant.id,'text':email['text'],'adapter':'home_facts'},process_now=False)
    enqueue(response,'email',key,{'communication_id':comm.id,'grant_id':grant.id})
    event(response,'email_queued','agent',agent.id,recipient=grant.email,purpose=kind,communication_id=comm.id)
    return comm
