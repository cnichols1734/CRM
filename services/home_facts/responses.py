"""Transactional answer saving and version-specific report review.

Callers authorize the transaction or participant first. Every query still
includes the organization and response identifiers. Commits belong to routes.
"""
from datetime import datetime
import hashlib
import json
from uuid import UUID, uuid4

from models import (db, HomeFactsResponse, HomeFactsRevision, HomeFactsMutation,
                    HomeFactsEvent, HomeFactsJob, HomeFactsArtifact, HomeFactsGrant)
from services.home_facts.schema import manifest, validate_answers


class HomeFactsError(ValueError):
    def __init__(self, message, code='invalid', status=422):
        super().__init__(message)
        self.code, self.status = code, status


def identifier():
    return str(uuid4())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def event(response, kind, actor_kind, actor_id=None, **details):
    row = HomeFactsEvent(id=identifier(), organization_id=response.organization_id,
        response_id=response.id, kind=kind, actor_kind=actor_kind,
        actor_id=str(actor_id) if actor_id is not None else None, details=details)
    db.session.add(row)
    return row


def enqueue(response, kind, key, payload):
    existing = HomeFactsJob.query.filter_by(organization_id=response.organization_id, dedupe_key=key).first()
    if existing:
        return existing
    row = HomeFactsJob(id=identifier(), organization_id=response.organization_id,
        response_id=response.id, kind=kind, dedupe_key=key, payload=payload)
    db.session.add(row)
    return row


def response_for_transaction(transaction, create=False):
    if create:
        from models import Transaction
        Transaction.query.filter_by(id=transaction.id, organization_id=transaction.organization_id).with_for_update().one()
    row = HomeFactsResponse.query.filter_by(organization_id=transaction.organization_id, transaction_id=transaction.id).first()
    if not row and create:
        if transaction.transaction_type.name.lower() != 'seller':
            raise HomeFactsError('Home Facts is available for seller transactions.')
        row = HomeFactsResponse(id=identifier(), organization_id=transaction.organization_id,
            transaction_id=transaction.id, answers={}, provenance={})
        db.session.add(row)
        db.session.flush()
        event(row, 'created', 'agent', transaction.created_by_id)
    return row


def load_response(response_id, org_id, *, lock=False):
    query = HomeFactsResponse.query.filter_by(id=response_id, organization_id=org_id)
    if lock:
        query = query.populate_existing().with_for_update()
    row = query.first()
    if row is None:
        raise HomeFactsError('This questionnaire is not available.', 'not_found', 404)
    return row


def _mutation(response, actor, mutation_id, payload):
    try:
        if str(UUID(mutation_id)) != mutation_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HomeFactsError('A valid save identifier is required.')
    previous = HomeFactsMutation.query.filter_by(organization_id=response.organization_id,
        response_id=response.id, actor=actor, mutation_id=mutation_id).first()
    if previous and previous.payload_hash != digest(payload):
        raise HomeFactsError('This save identifier was already used for different changes.', 'mutation_conflict', 409)
    return previous


def _record_mutation(response, actor, mutation_id, payload):
    db.session.add(HomeFactsMutation(id=identifier(), organization_id=response.organization_id,
        response_id=response.id, actor=actor, mutation_id=mutation_id,
        payload_hash=digest(payload), resulting_revision=response.revision))


def _expected(response, expected_revision):
    if type(expected_revision) is not int or expected_revision != response.revision:
        raise HomeFactsError('Answers changed on another device. Review those changes before saving.', 'revision_conflict', 409)


def save(response, *, actor_kind, actor_id, changes, expected_revision, mutation_id):
    response = load_response(response.id, response.organization_id, lock=True)
    actor = f'{actor_kind}:{actor_id}'
    payload = {'operation': 'save', 'changes': changes, 'expected_revision': expected_revision}
    if _mutation(response, actor, mutation_id, payload):
        return response
    _expected(response, expected_revision)
    if response.state != 'draft':
        raise HomeFactsError('Your response has been submitted. Ask your agent to reopen it for changes.', 'submitted', 409)
    try:
        clean = validate_answers(changes, response.schema_version)
    except ValueError as error:
        raise HomeFactsError(str(error)) from error
    answers, provenance = dict(response.answers), dict(response.provenance)
    now = datetime.utcnow()
    for key, value in clean.items():
        if value is None or value == '' or value == []:
            answers.pop(key, None)
        else:
            answers[key] = value
        provenance[key] = {'kind': actor_kind, 'id': str(actor_id), 'at': now.isoformat()+'Z'}
    count = HomeFactsResponse.query.filter_by(id=response.id, organization_id=response.organization_id,
        revision=expected_revision, state='draft').update({
            'answers': answers, 'provenance': provenance, 'revision': expected_revision+1, 'updated_at': now}, synchronize_session=False)
    if count != 1:
        raise HomeFactsError('Answers changed on another device. Review those changes before saving.', 'revision_conflict', 409)
    db.session.refresh(response)
    _record_mutation(response, actor, mutation_id, payload)
    event(response, 'saved', actor_kind, actor_id, revision=response.revision, fields=list(clean))
    return response


def submit(response, *, participant_id, expected_revision, mutation_id, acknowledged):
    response = load_response(response.id, response.organization_id, lock=True)
    actor = f'participant:{participant_id}'
    payload = {'operation': 'submit', 'expected_revision': expected_revision, 'acknowledged': acknowledged}
    if _mutation(response, actor, mutation_id, payload):
        return response
    _expected(response, expected_revision)
    if response.state != 'draft':
        raise HomeFactsError('This response is already submitted.', 'submitted', 409)
    if acknowledged is not True:
        raise HomeFactsError('Confirm the sharing acknowledgment before submitting.')
    snapshot = HomeFactsRevision(id=identifier(), organization_id=response.organization_id,
        response_id=response.id, revision=response.revision, answers=dict(response.answers),
        provenance=dict(response.provenance), participant_id=participant_id,
        attachments=[{'id': a.id, 'digest': a.digest} for a in artifacts_for(response) if a.kind == 'attachment'],
        acknowledgment=manifest(response.schema_version)['acknowledgment'])
    db.session.add(snapshot)
    response.state = 'submitted'
    response.submitted_revision = response.revision
    response.updated_at = datetime.utcnow()
    _record_mutation(response, actor, mutation_id, payload)
    event(response, 'submitted', 'participant', participant_id, revision=response.revision)
    enqueue(response, 'generate', f'generate:{response.id}:{response.revision}', {'revision': response.revision})
    enqueue(response, 'push', f'submitted:{response.id}:{response.revision}', {'audience': 'agent', 'event': 'submitted'})
    return response


def reopen(response, agent_id, note):
    response = load_response(response.id, response.organization_id, lock=True)
    if response.state == 'draft':
        return response
    if not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise HomeFactsError('Add a short note about the changes needed.')
    response.state = 'draft'
    response.revision += 1
    response.updated_at = datetime.utcnow()
    event(response, 'changes_requested', 'agent', agent_id, note=note.strip())
    enqueue(response, 'push', f'reopened:{response.id}:{response.revision}', {'audience': 'client', 'event': 'changes_requested'})
    return response


def artifacts_for(response, revision=None):
    query = HomeFactsArtifact.query.filter_by(organization_id=response.organization_id, response_id=response.id)
    if revision is not None:
        query = query.filter_by(revision=revision)
    return query.order_by(HomeFactsArtifact.created_at).all()


def report_digest(response, revision):
    reports = [a for a in artifacts_for(response, revision) if a.kind in ('appraiser','buyer','questionnaire')]
    if {a.kind for a in reports} != {'appraiser','buyer','questionnaire'}:
        raise HomeFactsError('The reports are still being prepared.', 'reports_pending', 409)
    return digest(sorted((a.id,a.digest) for a in reports))


def review(response, *, actor_kind, actor_id, revision, artifact_hash, offline_note=None):
    response = load_response(response.id, response.organization_id, lock=True)
    if response.state != 'submitted' or response.submitted_revision != revision:
        raise HomeFactsError('Review the current submitted version.', 'stale_review', 409)
    expected = report_digest(response, revision)
    if artifact_hash != expected:
        raise HomeFactsError('The files changed. Open the current reports before reviewing.', 'stale_review', 409)
    snapshot = HomeFactsRevision.query.filter_by(organization_id=response.organization_id,
        response_id=response.id, revision=revision).with_for_update().one()
    now = datetime.utcnow()
    if actor_kind == 'agent':
        snapshot.agent_reviewed_by, snapshot.agent_reviewed_at = actor_id, now
        if offline_note is not None:
            if not isinstance(offline_note,str) or not offline_note.strip() or len(offline_note)>2000:
                raise HomeFactsError('Describe when and how you reviewed both reports with the seller.')
            snapshot.seller_reviewed_at = now
            snapshot.seller_reviewed_by = actor_id
            snapshot.seller_review_method = 'agent_recorded'
            snapshot.seller_review_note = offline_note.strip()
    else:
        snapshot.seller_reviewed_at = now
        snapshot.seller_reviewed_by = actor_id
        snapshot.seller_review_method = 'participant'
    snapshot.reviewed_artifact_hash = expected
    event(response,'reports_reviewed',actor_kind,actor_id,revision=revision,method=snapshot.seller_review_method)
    return snapshot


def serialize(response, *, agent=False):
    artifacts = artifacts_for(response)
    snapshot = HomeFactsRevision.query.filter_by(organization_id=response.organization_id,
        response_id=response.id, revision=response.submitted_revision).first() if response.state=='submitted' and response.submitted_revision is not None else None
    try:
        artifact_hash = report_digest(response, response.submitted_revision) if snapshot else None
    except HomeFactsError:
        artifact_hash = None
    data = {'id': response.id, 'transaction_id': response.transaction_id,
        'property_address': response.transaction.street_address, 'schema_version': response.schema_version,
        'revision': response.revision, 'state': response.state, 'answers': response.answers,
        'provenance': response.provenance, 'saved_at': response.updated_at.isoformat()+'Z',
        'submitted_revision': response.submitted_revision, 'artifact_hash': artifact_hash,
        'agent_reviewed': bool(snapshot and snapshot.agent_reviewed_at),
        'seller_reviewed': bool(snapshot and snapshot.seller_reviewed_at),
        'manifest': manifest(response.schema_version),
        'artifacts': [{'id': a.id, 'revision': a.revision, 'kind': a.kind, 'filename': a.filename,
                       'digest': a.digest, 'size': a.size, 'content_type': a.content_type} for a in artifacts
                      if a.kind=='attachment' or (snapshot and a.revision==snapshot.revision)]}
    if agent:
        data['participants'] = [{'id': p.id, 'name': p.display_name, 'email': p.display_email}
            for p in response.transaction.participants if p.role in ('seller','co_seller') and p.organization_id == response.organization_id]
        data['grants'] = [{'id': g.id, 'audience': g.audience, 'email': g.email,
                          'revoked': bool(g.revoked_at), 'expires_at': g.expires_at.isoformat()+'Z'}
            for g in HomeFactsGrant.query.filter_by(organization_id=response.organization_id,response_id=response.id).all()]
        data['activity'] = [{'kind': e.kind, 'at': e.created_at.isoformat()+'Z', 'details': e.details,
                            'actor_kind': e.actor_kind, 'actor_id': e.actor_id}
            for e in HomeFactsEvent.query.filter_by(organization_id=response.organization_id,response_id=response.id).order_by(HomeFactsEvent.created_at.desc()).limit(100)]
        data['jobs'] = [{'id':j.id,'kind':j.kind,'state':j.state,'error':j.last_error}
            for j in HomeFactsJob.query.filter_by(organization_id=response.organization_id,response_id=response.id).order_by(HomeFactsJob.created_at.desc()).limit(40)]
    return data
