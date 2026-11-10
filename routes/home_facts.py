"""Home Facts agent, participant and restricted-link interfaces."""
from io import BytesIO
from functools import wraps
import base64
import hmac
import secrets

from flask import Blueprint, jsonify, request, session, render_template, redirect, send_file, url_for
from flask_login import login_required, current_user

from models import db, HomeFactsGrant
from routes.agent_api import agent_jwt_required, transactions_flag_required
from routes.transactions.decorators import transactions_required
from routes.client_api import client_jwt_required
from services.transaction_auth import get_transaction_for_user,CAP_VIEW,CAP_EDIT,CAP_SEND_COMMS
from services.home_facts import responses as hf
from services.home_facts.access import activate,session_grant,set_org
from services.home_facts.schema import ROOT,manifest
from services.home_facts.commands import execute
from services.home_facts.files import upload,read_artifact,MAX_UPLOAD

home_facts_bp=Blueprint('home_facts',__name__)


@home_facts_bp.errorhandler(hf.HomeFactsError)
def handle_error(error):
    db.session.rollback()
    return jsonify({'error':str(error),'code':error.code}),error.status


def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):
        raise hf.HomeFactsError('Send a JSON object.')
    return data


def csrf():
    value=session.setdefault('home_facts_csrf',secrets.token_urlsafe(32))
    if request.method not in ('GET','HEAD','OPTIONS'):
        supplied=request.headers.get('X-CSRF-Token') or request.form.get('csrf_token','')
        if not hmac.compare_digest(value,supplied):
            raise hf.HomeFactsError('Reload this page before saving. Your device draft is safe.','csrf',403)
    return value


def finish(response,agent=False):
    org_id=response.organization_id
    data=hf.serialize(response,agent=agent)
    data['template_url']=url_for('home_facts.template',version=response.schema_version,_external=True)
    db.session.commit()
    if request.method not in ('GET','HEAD'):
        from jobs.home_facts import kick
        kick(org_id)
    return jsonify(data)


def agent_response(user,transaction_id,operation=None):
    capability=CAP_VIEW if request.method=='GET' else CAP_SEND_COMMS if operation in ('invite','request_review','share','revoke','email_preview') else CAP_EDIT
    tx,decision=get_transaction_for_user(transaction_id,user,capability)
    if not tx:
        raise hf.HomeFactsError('This transaction is not available.','not_found',404)
    # Creating a blank response is an explicit POST, never a GET side effect.
    response=hf.response_for_transaction(tx,create=request.method=='POST' and operation is None)
    if not response:
        if request.method=='GET':return jsonify({'available':False,'transaction_id':tx.id})
        raise hf.HomeFactsError('Set up Home Facts first.','not_found',404)
    if operation=='email_preview':
        from models import Organization
        from services.home_facts.mail import render_email, valid_email
        values=body()
        recipients=values.get('recipients')
        message=values.get('message','')
        if not isinstance(recipients,list) or not 1<=len(recipients)<=20:
            raise hf.HomeFactsError('Add between one and 20 recipients.')
        recipients=[valid_email(value) for value in recipients]
        if not isinstance(message,str) or len(message)>5000:
            raise hf.HomeFactsError('Keep the message under 5,000 characters.')
        return jsonify(render_email(response,user,db.session.get(Organization,response.organization_id),
            recipient=recipients[0],link='about:blank',kind='share',custom_message=message))
    if operation:
        response=execute(response,user,operation,body())
    return finish(response,agent=True)


@home_facts_bp.route('/api/agent/v1/transactions/<int:transaction_id>/home-facts',methods=['GET','POST'])
@agent_jwt_required
@transactions_flag_required
def agent_api(user,transaction_id):
    return agent_response(user,transaction_id)


@home_facts_bp.route('/api/agent/v1/transactions/<int:transaction_id>/home-facts/<operation>',methods=['POST'])
@agent_jwt_required
@transactions_flag_required
def agent_command(user,transaction_id,operation):
    return agent_response(user,transaction_id,operation)


@home_facts_bp.route('/transactions/<int:transaction_id>/home-facts')
@login_required
@transactions_required
def agent_workspace(transaction_id):
    tx,decision=get_transaction_for_user(transaction_id,current_user,CAP_VIEW)
    if not tx:raise hf.HomeFactsError('This transaction is not available.','not_found',404)
    return render_template('home_facts/agent.html',transaction=tx,csrf_token=csrf())


@home_facts_bp.route('/transactions/<int:transaction_id>/home-facts/data',methods=['GET','POST'])
@login_required
@transactions_required
def agent_web_data(transaction_id):
    csrf()
    return agent_response(current_user,transaction_id)


@home_facts_bp.route('/transactions/<int:transaction_id>/home-facts/actions/<operation>',methods=['POST'])
@login_required
@transactions_required
def agent_web_command(transaction_id,operation):
    csrf()
    return agent_response(current_user,transaction_id,operation)


def client_response(access,response_id=None):
    if access.participant.role not in ('seller','co_seller'):
        raise hf.HomeFactsError('This questionnaire is not available.','not_found',404)
    response=hf.response_for_transaction(access.transaction)
    if not response or (response_id and response.id!=response_id):
        raise hf.HomeFactsError('This questionnaire is not available.','not_found',404)
    from datetime import datetime
    grant=HomeFactsGrant.query.filter_by(organization_id=access.organization_id,response_id=response.id,
        participant_id=access.participant_id,audience='seller',revoked_at=None).filter(
            HomeFactsGrant.expires_at>datetime.utcnow()).order_by(HomeFactsGrant.created_at.desc()).first()
    if grant is None:
        raise hf.HomeFactsError('Your agent has not invited you to this questionnaire.','not_found',404)
    from services.home_facts.access import active
    active(grant)
    return response


@home_facts_bp.route('/api/client/v1/home-facts',methods=['GET'])
@client_jwt_required
def client_current(access):
    try:response=client_response(access)
    except hf.HomeFactsError as error:
        if error.status==404:return jsonify({'available':False})
        raise
    return finish(response)


@home_facts_bp.route('/api/client/v1/home-facts/template')
@client_jwt_required
def client_template(access):
    response=client_response(access)
    return template(response.schema_version)


def client_action(response,participant_id,operation):
    data=body()
    if operation=='save':
        response=hf.save(response,actor_kind='participant',actor_id=participant_id,
            changes=data.get('changes'),expected_revision=data.get('expected_revision'),mutation_id=data.get('mutation_id'))
    elif operation=='submit':
        response=hf.submit(response,participant_id=participant_id,expected_revision=data.get('expected_revision'),
            mutation_id=data.get('mutation_id'),acknowledged=data.get('acknowledged'))
    elif operation=='review':
        hf.review(response,actor_kind='participant',actor_id=participant_id,revision=data.get('revision'),artifact_hash=data.get('artifact_hash'))
    else:raise hf.HomeFactsError('Unknown questionnaire action.')
    return finish(response)


@home_facts_bp.route('/api/client/v1/home-facts/<response_id>',methods=['GET','PATCH'])
@client_jwt_required
def client_api(access,response_id):
    response=client_response(access,response_id)
    if request.method=='PATCH':return client_action(response,access.participant_id,'save')
    return finish(response)


@home_facts_bp.route('/api/client/v1/home-facts/<response_id>/<operation>',methods=['POST'])
@client_jwt_required
def client_command(access,response_id,operation):
    response=client_response(access,response_id)
    if operation=='attachments':return attachment_upload(response,'participant',access.participant_id)
    return client_action(response,access.participant_id,operation)


@home_facts_bp.route('/home-facts/template/<int:version>.pdf')
def template(version):
    if version!=1:raise hf.HomeFactsError('This template is not available.','not_found',404)
    manifest(version)
    return send_file(ROOT/f'v{version}'/'questionnaire.pdf',mimetype='application/pdf',max_age=86400)


@home_facts_bp.route('/home-facts/open',methods=['GET','POST'])
def open_link():
    token=csrf()
    if request.method in ('GET','HEAD'):
        return render_template('home_facts/open.html',csrf_token=token)
    grant=activate(request.form.get('token',''))
    claims={'id':grant.id,'org':grant.organization_id,'version':grant.session_version}
    response_id=grant.response_id
    grants=dict(session.get('home_facts_grants',{}));grants[response_id]=claims
    # Bound the signed session cookie while preserving the most recent moves.
    session['home_facts_grants']=dict(list(grants.items())[-4:])
    db.session.commit()
    return redirect(url_for('home_facts.client_workspace',response_id=response_id))


def web_grant(response_id,seller=False):
    claims=(session.get('home_facts_grants') or {}).get(response_id)
    grant=session_grant(claims)
    if grant.response_id!=response_id or (seller and grant.audience!='seller'):
        raise hf.HomeFactsError('This page is not available.','not_found',404)
    return grant


@home_facts_bp.route('/home-facts/r/<response_id>')
def client_workspace(response_id):
    grant=web_grant(response_id)
    response=hf.load_response(response_id,grant.organization_id)
    return render_template('home_facts/client.html',response=response,recipient=grant.audience=='recipient',
        draft_scope=f'{grant.organization_id}:{grant.participant_id or grant.id}',csrf_token=csrf())


@home_facts_bp.route('/home-facts/r/<response_id>/data',methods=['GET','PATCH'])
def client_web_data(response_id):
    csrf();grant=web_grant(response_id)
    response=hf.load_response(response_id,grant.organization_id)
    if grant.audience=='recipient':
        if request.method!='GET':raise hf.HomeFactsError('This link only allows viewing documents.','forbidden',403)
        ids={a['id'] for a in grant.artifacts}
        return jsonify({'id':response.id,'property_address':response.transaction.street_address,
            'artifacts':[{'id':a.id,'kind':a.kind,'filename':a.filename,'size':a.size}
                for a in hf.artifacts_for(response) if a.id in ids]})
    if request.method=='PATCH':return client_action(response,grant.participant_id,'save')
    return finish(response)


@home_facts_bp.route('/home-facts/r/<response_id>/actions/<operation>',methods=['POST'])
def client_web_command(response_id,operation):
    csrf();grant=web_grant(response_id,seller=True)
    response=hf.load_response(response_id,grant.organization_id)
    if operation=='attachments':return attachment_upload(response,'participant',grant.participant_id)
    return client_action(response,grant.participant_id,operation)


def attachment_upload(response,actor_kind,actor_id):
    if request.content_length and request.content_length>MAX_UPLOAD*1.4:
        raise hf.HomeFactsError('Choose a file smaller than 15 MB.')
    if request.is_json:
        data=body()
        try:content=base64.b64decode(data.get('data',''),validate=True)
        except (ValueError,TypeError):raise hf.HomeFactsError('The upload could not be read.')
        filename=data.get('filename');mutation_id=data.get('mutation_id')
    else:
        file=request.files.get('file')
        if file is None:raise hf.HomeFactsError('Choose a PDF or photo.')
        content=file.read(MAX_UPLOAD+1);filename=file.filename;mutation_id=request.form.get('mutation_id')
    upload(response,content,filename,mutation_id,actor_kind,actor_id)
    return finish(response,agent=actor_kind=='agent')


def artifact_result(response,artifact_id,grant=None):
    artifact,content=read_artifact(response,artifact_id,grant)
    content_type, filename = artifact.content_type, artifact.filename
    if grant:
        hf.event(response,'download_requested',grant.audience,grant.id,artifact_id=artifact.id)
        db.session.commit()
    return send_file(BytesIO(content),mimetype=content_type,download_name=filename,
        as_attachment=request.args.get('download')=='1')


@home_facts_bp.route('/home-facts/r/<response_id>/files/<artifact_id>')
def web_file(response_id,artifact_id):
    grant=web_grant(response_id)
    return artifact_result(hf.load_response(response_id,grant.organization_id),artifact_id,grant)


@home_facts_bp.route('/api/client/v1/home-facts/<response_id>/files/<artifact_id>')
@client_jwt_required
def client_file(access,response_id,artifact_id):
    return artifact_result(client_response(access,response_id),artifact_id)


@home_facts_bp.route('/api/agent/v1/transactions/<int:transaction_id>/home-facts/files/<artifact_id>')
@agent_jwt_required
@transactions_flag_required
def agent_file(user,transaction_id,artifact_id):
    tx,_=get_transaction_for_user(transaction_id,user,CAP_VIEW)
    if not tx:raise hf.HomeFactsError('This transaction is not available.','not_found',404)
    response=hf.response_for_transaction(tx)
    if not response:raise hf.HomeFactsError('This document is not available.','not_found',404)
    return artifact_result(response,artifact_id)


@home_facts_bp.route('/transactions/<int:transaction_id>/home-facts/files/<artifact_id>')
@login_required
@transactions_required
def agent_web_file(transaction_id,artifact_id):
    return agent_file.__wrapped__.__wrapped__(current_user,transaction_id,artifact_id)


@home_facts_bp.after_request
def privacy_headers(response):
    response.headers['Cache-Control']='private, no-store'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Robots-Tag']='noindex, nofollow'
    response.headers['X-Content-Type-Options']='nosniff'
    if request.path.startswith('/home-facts/'):
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' blob:; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
    return response
