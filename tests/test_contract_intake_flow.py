"""Agent-first contract intake route and UI acceptance tests."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from models import ContractBootstrapSession, db

def test_contract_intake_is_hidden_without_pilot(owner_a_client):
    response = owner_a_client.get('/transactions/bootstrap/inbox')
    assert response.status_code == 404


def test_contract_intake_uploads_without_asking_representation(app, seed, owner_a_client):
    """The form usually says which side you are on, so we don't ask up front."""
    with patch('routes.transactions.decorators.org_has_feature', return_value=True):
        response = owner_a_client.get('/transactions/bootstrap/inbox')

    assert response.status_code == 200
    assert b'Upload and identify' in response.data
    assert b'Who do you represent?' not in response.data
    assert b'id="representation-side" value=""' in response.data


def test_contract_upload_queues_processing_instead_of_extracting_in_request(
    app,
    seed,
    owner_a_client,
):
    fake_session = SimpleNamespace(id=412)
    with patch(
        'routes.transactions.decorators.org_has_feature',
        return_value=True,
    ), patch(
        'routes.transactions.bootstrap.contract_bootstrap.process_inbox_upload',
        return_value=fake_session,
    ) as process_upload, patch(
        'routes.transactions.bootstrap.contract_bootstrap.enqueue_bootstrap_processing',
    ) as enqueue:
        response = owner_a_client.post(
            '/transactions/bootstrap/inbox',
            data={
                'side': 'buyer',
                'file': (BytesIO(b'%PDF-1.4 agent-first-flow'), 'executed.pdf'),
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 302
    assert '/transactions/bootstrap/batch/' in response.headers['Location']
    assert process_upload.call_args.kwargs['run_extraction'] is False
    assert process_upload.call_args.kwargs['confirmed_side'] == 'buyer'
    assert process_upload.call_args.kwargs['upload_batch_id']
    enqueue.assert_called_once_with(session_id=412, org_id=seed['org_a'])


def test_contract_upload_without_side_lets_bob_decide(app, seed, owner_a_client):
    """No side chosen is fine — inference runs and review asks only if needed."""
    fake_session = SimpleNamespace(id=413)
    with patch(
        'routes.transactions.decorators.org_has_feature',
        return_value=True,
    ), patch(
        'routes.transactions.bootstrap.contract_bootstrap.process_inbox_upload',
        return_value=fake_session,
    ) as process_upload, patch(
        'routes.transactions.bootstrap.contract_bootstrap.enqueue_bootstrap_processing',
    ):
        response = owner_a_client.post(
            '/transactions/bootstrap/inbox',
            data={
                'file': (BytesIO(b'%PDF-1.4 missing-side'), 'executed.pdf'),
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 302
    assert '/transactions/bootstrap/batch/' in response.headers['Location']
    assert process_upload.call_args.kwargs['confirmed_side'] is None
    assert process_upload.call_args.kwargs['upload_batch_id']


def test_contract_inbox_accepts_multiple_pdfs(app, seed, owner_a_client):
    sessions = [SimpleNamespace(id=501), SimpleNamespace(id=502)]
    with patch(
        'routes.transactions.decorators.org_has_feature',
        return_value=True,
    ), patch(
        'routes.transactions.bootstrap.contract_bootstrap.process_inbox_upload',
        side_effect=sessions,
    ) as process_upload, patch(
        'routes.transactions.bootstrap.contract_bootstrap.enqueue_bootstrap_processing',
    ) as enqueue:
        response = owner_a_client.post(
            '/transactions/bootstrap/inbox',
            data={
                'files': [
                    (BytesIO(b'%PDF-1.4 listing'), 'listing.pdf'),
                    (BytesIO(b'%PDF-1.4 hoa'), 'hoa.pdf'),
                ],
            },
            content_type='multipart/form-data',
        )

    assert response.status_code == 302
    assert '/transactions/bootstrap/batch/' in response.headers['Location']
    assert process_upload.call_count == 2
    assert enqueue.call_count == 2
    # Shared batch id so review waits for the whole package.
    batch_ids = {
        call.kwargs.get('upload_batch_id')
        for call in process_upload.call_args_list
    }
    assert len(batch_ids) == 1
    assert None not in batch_ids


def test_contract_inbox_ui_allows_multiple_files(app, seed, owner_a_client):
    with patch('routes.transactions.decorators.org_has_feature', return_value=True):
        response = owner_a_client.get('/transactions/bootstrap/inbox')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'multiple' in html
    assert 'name="files"' in html
    assert 'Drop PDFs here' in html


def test_applied_contract_opens_prefilled_file_setup_confirmation(
    app, seed, owner_a_client,
):
    with app.app_context():
        session = ContractBootstrapSession(
            organization_id=seed['org_a'],
            uploader_user_id=seed['owner_a'],
            document_id=seed['doc_a'],
            matched_transaction_id=seed['tx_a'],
            status=ContractBootstrapSession.STATUS_APPLIED,
            match_status=ContractBootstrapSession.MATCH_MATCHED,
            original_filename='executed-contract.pdf',
            extracted_candidates={
                'hoa_applicable': {'value': True},
                'built_before_1978': {'value': False},
            },
        )
        db.session.add(session)
        db.session.commit()
        session_id = session.id

    try:
        response = owner_a_client.get(
            f'/transactions/{seed["tx_a"]}/intake?bootstrap_session_id={session_id}',
        )
        assert response.status_code == 200, response.location
        assert b'Finish file setup' in response.data
        assert b'Contract reviewed and attached' in response.data
        assert b'BOB</span> filled' in response.data
        assert b'Confirm file setup' in response.data
    finally:
        with app.app_context():
            ContractBootstrapSession.query.filter_by(id=session_id).delete()
            db.session.commit()
