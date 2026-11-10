"""Private files always require the response or exact recipient grant."""
from io import BytesIO
import hashlib
import re

from models import db, HomeFactsArtifact
from services.home_facts.responses import identifier, HomeFactsError, event, load_response, _mutation, _record_mutation

MAX_UPLOAD=15*1024*1024


def store(response,*,kind,revision,filename,content_type,content,generation_key):
    from services.supabase_storage import upload_file,TRANSACTION_DOCUMENTS_BUCKET
    existing=HomeFactsArtifact.query.filter_by(organization_id=response.organization_id,response_id=response.id,generation_key=generation_key).first()
    if existing:
        return existing
    ident=identifier()
    path=f'organizations/{response.organization_id}/transactions/{response.transaction_id}/home-facts/{response.id}/{ident}'
    upload_file(TRANSACTION_DOCUMENTS_BUCKET,path,content,filename,content_type)
    row=HomeFactsArtifact(id=ident,organization_id=response.organization_id,response_id=response.id,revision=revision,
        kind=kind,filename=filename,storage_path=path,content_type=content_type,
        digest=hashlib.sha256(content).hexdigest(),size=len(content),generation_key=generation_key)
    db.session.add(row); db.session.flush()
    return row


def upload(response,content,filename,mutation_id,actor_kind,actor_id):
    response=load_response(response.id,response.organization_id,lock=True)
    if not content or len(content)>MAX_UPLOAD:
        raise HomeFactsError('Choose a PDF or photo smaller than 15 MB.')
    actor=f'{actor_kind}:{actor_id}'
    payload={'operation':'upload','source_digest':hashlib.sha256(content).hexdigest(),'filename':filename}
    key=f'upload:{actor}:{mutation_id}'
    if _mutation(response,actor,mutation_id,payload):
        return HomeFactsArtifact.query.filter_by(organization_id=response.organization_id,response_id=response.id,generation_key=key).one()
    if response.state!='draft':
        raise HomeFactsError('Ask your agent to reopen this response before adding files.','submitted',409)
    filename=re.sub(r'[^\w .()-]','_',filename or 'Document')[:160]
    if content.startswith(b'%PDF-'):
        import fitz
        try:
            pdf=fitz.open(stream=content,filetype='pdf')
            if pdf.is_encrypted or not 1<=len(pdf)<=100:
                raise ValueError()
            pdf.scrub()
            content=pdf.tobytes(garbage=4,deflate=True)
        except Exception:
            raise HomeFactsError('Choose an unlocked PDF with at most 100 pages.')
        content_type='application/pdf'
        if not filename.lower().endswith('.pdf'): filename+='.pdf'
    else:
        from PIL import Image,ImageOps,UnidentifiedImageError
        try:
            image=Image.open(BytesIO(content))
            if image.width*image.height>40_000_000:
                raise ValueError()
            image=ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((3000,3000))
            buffer=BytesIO();image.save(buffer,format='JPEG',quality=90)
            content=buffer.getvalue(); content_type='image/jpeg'
            filename=filename.rsplit('.',1)[0]+'.jpg'
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
            raise HomeFactsError('Choose a PDF, JPEG or PNG photo.')
    artifact=store(response,kind='attachment',revision=response.revision,filename=filename,
        content_type=content_type,content=content,generation_key=key)
    _record_mutation(response,actor,mutation_id,payload)
    event(response,'attachment_uploaded',actor_kind,actor_id,artifact_id=artifact.id,filename=filename)
    return artifact


def read_artifact(response,artifact_id,grant=None):
    from services.supabase_storage import download_file,TRANSACTION_DOCUMENTS_BUCKET
    artifact=HomeFactsArtifact.query.filter_by(id=artifact_id,organization_id=response.organization_id,response_id=response.id).first()
    if not artifact:
        raise HomeFactsError('This document is not available.','not_found',404)
    if grant and grant.audience=='recipient' and not any(a['id']==artifact.id and a['digest']==artifact.digest for a in grant.artifacts):
        raise HomeFactsError('This document is not available.','not_found',404)
    content=download_file(TRANSACTION_DOCUMENTS_BUCKET,artifact.storage_path)
    if hashlib.sha256(content).hexdigest()!=artifact.digest:
        raise HomeFactsError('This document needs to be restored. Please contact your agent.','file_changed',409)
    return artifact,content
