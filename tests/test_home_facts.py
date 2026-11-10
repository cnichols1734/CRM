"""Seller answers survive retries; private data and version approvals stay scoped."""
from datetime import datetime,timedelta
from io import BytesIO
import base64
import json
import time
from uuid import uuid4

import pytest
from pypdf import PdfReader

from models import (db,User,Organization,Transaction,TransactionType,TransactionParticipant,ClientPortalAccess,
    HomeFactsResponse,HomeFactsRevision,HomeFactsMutation,HomeFactsGrant,HomeFactsArtifact,HomeFactsJob,HomeFactsEvent,
    TransactionCommunication,CommunicationDeliveryAttempt)
from services.home_facts import responses as hf
from services.home_facts.access import create_grant
from services.home_facts.schema import fields,manifest
from services.home_facts.rendering import questionnaire_pdf,report_pdf
from services.client_portal_auth import issue_client_jwt
from services.agent_auth import issue_agent_jwt


@pytest.fixture
def home(app,seed,monkeypatch):
    monkeypatch.setattr('jobs.home_facts.kick',lambda org_id:None)
    with app.app_context():
        original_flags={}
        for org_id in (seed['org_a'],seed['org_b']):
            org=db.session.get(Organization,org_id);original_flags[org_id]=org.feature_flags
            org.feature_flags={**(org.feature_flags or {}),'TRANSACTIONS':True}
        kind=TransactionType.query.filter_by(organization_id=seed['org_a'],name='seller').first()
        tx=Transaction(organization_id=seed['org_a'],created_by_id=seed['owner_a'],transaction_type_id=kind.id,street_address='123 Synthetic Home Facts Lane')
        db.session.add(tx);db.session.flush()
        participant=TransactionParticipant(organization_id=seed['org_a'],transaction_id=tx.id,role='seller',name='Test Seller',email='seller@example.com')
        db.session.add(participant);db.session.flush()
        access=ClientPortalAccess(organization_id=seed['org_a'],transaction_id=tx.id,participant_id=participant.id,token=ClientPortalAccess.generate_token(),session_version=1,is_active=True)
        db.session.add(access);db.session.flush()
        response=hf.response_for_transaction(tx,create=True)
        grant,token=create_grant(response,audience='seller',email=participant.email,participant_id=participant.id)
        db.session.commit()
        case={'id':response.id,'org':seed['org_a'],'tx':tx.id,'participant':participant.id,'grant':grant.id,'token':token,
            'client_headers':{'Authorization':'Bearer '+issue_client_jwt(access)},
            'agent_headers':{'Authorization':'Bearer '+issue_agent_jwt(db.session.get(User,seed['owner_a']))},
            'foreign_headers':{'Authorization':'Bearer '+issue_agent_jwt(db.session.get(User,seed['owner_b']))}}
    yield case
    with app.app_context():
        for org_id,flags in original_flags.items():db.session.get(Organization,org_id).feature_flags=flags
        comm_ids=[c.id for c in TransactionCommunication.query.filter_by(transaction_id=case['tx'])]
        CommunicationDeliveryAttempt.query.filter(CommunicationDeliveryAttempt.communication_id.in_(comm_ids)).delete(synchronize_session=False)
        TransactionCommunication.query.filter_by(transaction_id=case['tx']).delete(synchronize_session=False)
        for model in (HomeFactsJob,HomeFactsEvent,HomeFactsMutation,HomeFactsArtifact,HomeFactsGrant,HomeFactsRevision):
            model.query.filter_by(response_id=case['id']).delete(synchronize_session=False)
        HomeFactsResponse.query.filter_by(id=case['id']).delete(synchronize_session=False)
        ClientPortalAccess.query.filter_by(transaction_id=case['tx']).delete(synchronize_session=False)
        TransactionParticipant.query.filter_by(transaction_id=case['tx']).delete(synchronize_session=False)
        Transaction.query.filter_by(id=case['tx']).delete(synchronize_session=False)
        db.session.commit()


def test_save_retry_conflict_clear_and_partial_submission(app,home):
    client=app.test_client();url=f'/api/client/v1/home-facts/{home["id"]}'
    payload={'expected_revision':0,'mutation_id':str(uuid4()),'changes':{'system_roof_year':'approx. 2019','living_love':'Full answer '*200}}
    first=client.patch(url,headers=home['client_headers'],json=payload)
    assert first.status_code==200
    assert first.json['revision']==1
    assert client.patch(url,headers=home['client_headers'],json=payload).json['revision']==1
    stale={**payload,'mutation_id':str(uuid4()),'changes':{'system_roof_year':'2020'}}
    assert client.patch(url,headers=home['client_headers'],json=stale).status_code==409
    invalid={**stale,'expected_revision':1,'changes':{'hoa':['Yes','No']}}
    assert client.patch(url,headers=home['client_headers'],json=invalid).status_code==422
    cleared=client.patch(url,headers=home['client_headers'],json={'expected_revision':1,'mutation_id':str(uuid4()),'changes':{'system_roof_year':None}})
    assert cleared.status_code==200 and 'system_roof_year' not in cleared.json['answers']
    submit={'expected_revision':2,'mutation_id':str(uuid4()),'acknowledged':True}
    assert client.post(url+'/submit',headers=home['client_headers'],json=submit).status_code==200
    assert client.post(url+'/submit',headers=home['client_headers'],json=submit).status_code==200
    with app.app_context():
        snapshot=HomeFactsRevision.query.filter_by(response_id=home['id']).one()
        assert snapshot.answers=={'living_love':'Full answer '*200}
        assert HomeFactsJob.query.filter_by(response_id=home['id'],kind='generate').count()==1


def test_agent_and_client_boundaries_and_revocation(app,home,owner_a_client):
    client=app.test_client();url=f'/api/client/v1/home-facts/{home["id"]}'
    assert owner_a_client.get(url).status_code==401
    agent_url=f'/api/agent/v1/transactions/{home["tx"]}/home-facts'
    assert client.get(agent_url,headers=home['foreign_headers']).status_code==404
    assert client.get(agent_url,headers=home['client_headers']).status_code==401
    assert client.get(url,headers=home['client_headers']).status_code==200
    with app.app_context():
        org=db.session.get(Organization,home['org']);org.feature_flags={**org.feature_flags,'TRANSACTIONS':False};db.session.commit()
    assert client.get(agent_url,headers=home['agent_headers']).status_code==403
    assert client.post(agent_url+'/invite',headers=home['agent_headers'],json={'mutation_id':str(uuid4()),'participant_ids':[home['participant']]}).status_code==403
    assert owner_a_client.get(f'/transactions/{home["tx"]}/home-facts').status_code==302
    assert client.get(url,headers=home['client_headers']).status_code==200
    with app.app_context():
        grant=db.session.get(HomeFactsGrant,home['grant']);grant.revoked_at=datetime.utcnow();db.session.commit()
    assert client.get(url,headers=home['client_headers']).status_code==404
    assert client.patch(url,headers=home['client_headers'],json={'expected_revision':0,'mutation_id':str(uuid4()),'changes':{'hoa':'Yes'}}).status_code==404
    with app.app_context():assert db.session.get(HomeFactsResponse,home['id']).answers=={}


def test_agent_email_preview_uses_send_template_without_sending(app,home):
    client=app.test_client()
    url=f"/api/agent/v1/transactions/{home['tx']}/home-facts/email_preview"
    payload={'recipients':['recipient@example.com'],'message':'Please review <private> details.'}
    with app.app_context():
        before=HomeFactsGrant.query.count(),HomeFactsJob.query.count(),TransactionCommunication.query.count()
    assert client.post(url,headers=home['foreign_headers'],json=payload).status_code==404
    result=client.post(url,headers=home['agent_headers'],json=payload)
    assert result.status_code==200
    preview=result.get_json()
    assert 'Your property documents are ready' in preview['subject']
    assert 'Open the secure link below' in preview['html']
    assert 'Please review &lt;private&gt; details.' in preview['html']
    assert 'View property documents' in preview['html']
    with app.app_context():
        assert (HomeFactsGrant.query.count(),HomeFactsJob.query.count(),TransactionCommunication.query.count())==before


def test_email_scanner_get_does_not_consume_and_web_csrf_is_required(app,home):
    client=app.test_client()
    assert client.get('/home-facts/open').status_code==200
    assert client.head('/home-facts/open').status_code==200
    with app.app_context():assert db.session.get(HomeFactsGrant,home['grant']).token_used_at is None
    assert client.post('/home-facts/open',data={'token':home['token']}).status_code==403
    with client.session_transaction() as session:csrf=session['home_facts_csrf']
    opened=client.post('/home-facts/open',data={'token':home['token'],'csrf_token':csrf})
    assert opened.status_code==302
    url=f'/home-facts/r/{home["id"]}'
    assert client.get(url).status_code==200
    assert client.post('/home-facts/open',data={'token':home['token'],'csrf_token':csrf}).status_code==302
    assert client.patch(url+'/data',json={'changes':{'hoa':'Yes'}}).status_code==403
    assert client.get(url+'/data').json['answers']=={}
    stranger=app.test_client()
    assert stranger.get(url+'/data').status_code==401


def test_exact_pdf_values_and_report_privacy():
    answers={}
    for key,field in fields().items():
        answers[key]=('Test '+key if field['kind']=='text' else field['options'][0] if field['kind']=='choice' else field['options'][:1])
    answers.update({'living_love':'Full detailed answer about storage and daylight. '*180,
        'showing_notes':'PRIVATE-GATE-9281','seller_email':'PRIVATE-EMAIL-9281','improvement_1_cost':'987654321'})
    result=questionnaire_pdf(answers)
    reader=PdfReader(BytesIO(result));canonical=reader.get_fields()
    assert len(canonical)==791 and len(reader.pages)>11
    for key,field in fields().items():
        for widget in field['widgets']:
            if field['kind']=='text':expected=answers[key]
            else:expected='/Yes' if widget['option'] in (answers[key] if isinstance(answers[key],list) else [answers[key]]) else '/Off'
            assert canonical[widget['name']]['/V']==expected
    for page in reader.pages[:11]:
        for ref in page['/Annots']:
            widget=ref.get_object()
            assert widget['/V']==canonical[widget['/T']]['/V']
            assert widget.get('/AP') and widget['/AP'].get('/N')
    for kind in ('appraiser','buyer'):
        pdf=PdfReader(BytesIO(report_pdf(answers,kind,address='123 Synthetic Lane',revision=4)))
        text=''.join(page.extract_text() for page in pdf.pages)
        assert 'PRIVATE-GATE-9281' not in text and 'PRIVATE-EMAIL-9281' not in text
        assert ('987654321' in text)==(kind=='appraiser')
        assert 'Sample Ridge' not in text


def test_report_review_cannot_apply_to_an_old_revision(app,home):
    with app.app_context():
        response=hf.load_response(home['id'],home['org'])
        hf.submit(response,participant_id=home['participant'],expected_revision=0,mutation_id=str(uuid4()),acknowledged=True)
        for kind in ('buyer','appraiser','questionnaire'):
            db.session.add(HomeFactsArtifact(id=str(uuid4()),organization_id=home['org'],response_id=home['id'],revision=0,
                kind=kind,generation_key=kind,filename=kind+'.pdf',storage_path='synthetic/'+kind,content_type='application/pdf',digest='0'*64,size=10))
        db.session.flush();hash_value=hf.report_digest(response,0)
        hf.review(response,actor_kind='participant',actor_id=home['participant'],revision=0,artifact_hash=hash_value)
        hf.reopen(response,1,'Please check the roof year.')
        assert hf.serialize(response)['correction_note']=='Please check the roof year.'
        with pytest.raises(hf.HomeFactsError,match='current submitted'):
            hf.review(response,actor_kind='agent',actor_id=1,revision=0,artifact_hash=hash_value)
        db.session.rollback()


def test_sendgrid_requires_real_ecdsa_signature(app):
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from routes.analytics_webhooks import _verify_sendgrid_signature
    private=ec.generate_private_key(ec.SECP256R1())
    key=base64.b64encode(private.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    timestamp=str(int(time.time()));payload=b'[{"event":"delivered"}]'
    signature=base64.b64encode(private.sign(timestamp.encode()+payload,ec.ECDSA(hashes.SHA256()))).decode()
    with app.app_context():
        previous=app.config.get('SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY')
        try:
            app.config['SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY']=key
            assert _verify_sendgrid_signature(payload,signature,timestamp)
            assert not _verify_sendgrid_signature(payload+b' ',signature,timestamp)
            app.config['SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY']=''
            assert not _verify_sendgrid_signature(payload,signature,timestamp)
        finally:app.config['SENDGRID_EVENT_WEBHOOK_VERIFICATION_KEY']=previous


def test_completed_reports_review_sharing_and_recipient_access(app,home,monkeypatch):
    from jobs.home_facts import process_one
    from services.home_facts.commands import execute
    storage={}
    monkeypatch.setattr('services.supabase_storage.upload_file',lambda bucket,path,content,*args:storage.__setitem__(path,content))
    monkeypatch.setattr('services.supabase_storage.download_file',lambda bucket,path:storage[path])
    with app.app_context():
        response=hf.load_response(home['id'],home['org'])
        hf.save(response,actor_kind='participant',actor_id=home['participant'],changes={'system_roof_year':'2019','seller_phone':'PRIVATE-PHONE'},expected_revision=0,mutation_id=str(uuid4()))
        hf.submit(response,participant_id=home['participant'],expected_revision=1,mutation_id=str(uuid4()),acknowledged=True)
        db.session.commit()
        job=HomeFactsJob.query.filter_by(response_id=home['id'],kind='generate').one()
        process_one(home['org'],job.id)
        process_one(home['org'],job.id)
        response=hf.load_response(home['id'],home['org'])
        reports=hf.artifacts_for(response)
        assert len(reports)==3 and len(storage)==3
        assert db.session.get(HomeFactsJob,job.id).state=='done'
        user=db.session.get(User,response.transaction.created_by_id)
        report_hash=hf.report_digest(response,1)
        buyer=next(a for a in reports if a.kind=='buyer')
        payload={'mutation_id':str(uuid4()),'recipients':['recipient@example.com'],'artifact_ids':[buyer.id],'artifact_hash':report_hash}
        with pytest.raises(hf.HomeFactsError,match='Review both'):
            execute(response,user,'share',payload)
        hf.review(response,actor_kind='agent',actor_id=user.id,revision=1,artifact_hash=report_hash)
        hf.review(response,actor_kind='participant',actor_id=home['participant'],revision=1,artifact_hash=report_hash)
        execute(response,user,'share',payload)
        execute(response,user,'share',payload)
        db.session.commit()
        assert TransactionCommunication.query.filter_by(transaction_id=home['tx']).count()==1
        assert HomeFactsGrant.query.filter_by(response_id=home['id'],audience='recipient').count()==1
        grant,token=create_grant(response,audience='recipient',email='viewer@example.com',artifacts=[{'id':buyer.id,'digest':buyer.digest}])
        db.session.commit()
        grant_id=grant.id;buyer_id=buyer.id;private_id=next(a.id for a in reports if a.kind=='questionnaire')
    viewer=app.test_client();viewer.get('/home-facts/open')
    with viewer.session_transaction() as session:csrf=session['home_facts_csrf']
    assert viewer.post('/home-facts/open',data={'token':token,'csrf_token':csrf}).status_code==302
    base=f'/home-facts/r/{home["id"]}'
    listing=viewer.get(base+'/data').json
    assert 'answers' not in listing and [a['id'] for a in listing['artifacts']]==[buyer_id]
    assert viewer.get(base+'/files/'+private_id).status_code==404
    assert viewer.get(base+'/files/'+buyer_id).data.startswith(b'%PDF')
    with app.app_context():
        grant=db.session.get(HomeFactsGrant,grant_id);grant.revoked_at=datetime.utcnow();db.session.commit()
    assert viewer.get(base+'/files/'+buyer_id).status_code==401


def test_uncertain_email_is_not_automatically_sent_again(app,home,monkeypatch):
    from jobs.home_facts import process_one
    from services.home_facts.commands import execute
    with app.app_context():
        response=hf.load_response(home['id'],home['org']);user=db.session.get(User,response.transaction.created_by_id)
        participant=db.session.get(TransactionParticipant,home['participant'])
        participant.email='seller.'+'a'*50+'@'+'b'*50+'.'+'c'*40+'.com'
        payload={'mutation_id':str(uuid4()),'participant_ids':[home['participant']]}
        execute(response,user,'invite',payload);execute(response,user,'invite',payload);db.session.commit()
        job=HomeFactsJob.query.filter_by(response_id=home['id'],kind='email').one()
        assert len(job.dedupe_key)<=150
        calls=[]
        def uncertain(job,response):
            calls.append(job.id)
            raise TimeoutError('Provider accepted but response was lost')
        monkeypatch.setattr('jobs.home_facts._email',uncertain)
        process_one(home['org'],job.id);process_one(home['org'],job.id)
        assert len(calls)==1
        assert db.session.get(HomeFactsJob,job.id).state=='ambiguous'
        comm=TransactionCommunication.query.filter_by(transaction_id=home['tx']).one()
        assert comm.status=='ambiguous'
        from services.home_facts.access import serializer
        from services.home_facts.tracking import apply_event
        comm.status='sending';db.session.commit()
        callback={'home_facts_receipt':serializer().dumps({'org':home['org'],'communication':comm.id}),
            'sg_message_id':'receipt-1.extra','sg_event_id':'callback-1','event':'delivered'}
        with pytest.raises(RuntimeError,match='still being committed'):apply_event(callback)
        comm.provider_message_id='receipt-1';comm.status='sent';db.session.commit()
        assert apply_event(callback)
        db.session.commit()
        assert HomeFactsEvent.query.filter_by(response_id=home['id'],kind='delivered').count()==1


def test_pdf_attachment_retry_preserves_one_sanitized_file(app,home,monkeypatch):
    storage={}
    monkeypatch.setattr('services.supabase_storage.upload_file',lambda bucket,path,content,*args:storage.__setitem__(path,content))
    content=report_pdf({'property_address':'Upload retry house'},'buyer',address='Upload retry house',revision=0)
    payload={'mutation_id':str(uuid4()),'filename':'Home record.pdf','data':base64.b64encode(content).decode()}
    client=app.test_client();url=f'/api/client/v1/home-facts/{home["id"]}/attachments'
    first=client.post(url,headers=home['client_headers'],json=payload)
    assert first.status_code==200
    retried=client.post(url,headers=home['client_headers'],json=payload)
    assert retried.status_code==200
    assert [a['id'] for a in retried.json['artifacts']]==[a['id'] for a in first.json['artifacts']]
    assert len(storage)==1
    changed={**payload,'filename':'Another record.pdf'}
    assert client.post(url,headers=home['client_headers'],json=changed).status_code==409
