"""
Replace / delete actions on a filed transaction document.

Split children are slices of their parent's upload, so they must not survive
the upload being deleted or swapped out.
"""

from unittest.mock import patch

from models import db, TransactionDocument


def _packet_with_child(seed, *, parent_status='signed', parent_source='completed'):
    parent = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=seed['tx_a'],
        template_slug='listing-agreement',
        template_name='Listing Agreement',
        status=parent_status,
        document_source=parent_source,
        signed_file_path='org/tx/listing-trimmed.pdf',
        source_file_path='org/tx/packet.pdf',
        is_placeholder=False,
    )
    db.session.add(parent)
    db.session.flush()

    child = TransactionDocument(
        organization_id=seed['org_a'],
        transaction_id=seed['tx_a'],
        template_slug='iabs',
        template_name='Information About Brokerage Services',
        status='signed',
        document_source='completed',
        signed_file_path='org/tx/iabs.pdf',
        parent_document_id=parent.id,
        page_start=13,
        page_end=13,
        split_source='fingerprint',
        is_placeholder=False,
    )
    db.session.add(child)
    db.session.commit()
    return parent.id, child.id


def test_delete_removes_split_children_and_their_files(app, owner_a_client, seed):
    with app.app_context():
        parent_id, child_id = _packet_with_child(seed)

    with patch('services.supabase_storage.delete_transaction_document') as delete_storage:
        response = owner_a_client.delete(
            f'/transactions/{seed["tx_a"]}/documents/{parent_id}'
        )

    assert response.status_code == 200
    assert response.get_json()['success'] is True

    with app.app_context():
        assert TransactionDocument.query.get(parent_id) is None
        assert TransactionDocument.query.get(child_id) is None

    discarded = {call.args[0] for call in delete_storage.call_args_list}
    assert discarded == {
        'org/tx/listing-trimmed.pdf',
        'org/tx/packet.pdf',
        'org/tx/iabs.pdf',
    }


def test_delete_refuses_docuseal_signed_documents(app, owner_a_client, seed):
    with app.app_context():
        parent_id, child_id = _packet_with_child(
            seed, parent_status='signed', parent_source='docuseal',
        )

    response = owner_a_client.delete(
        f'/transactions/{seed["tx_a"]}/documents/{parent_id}'
    )

    assert response.status_code == 400
    assert 'DocuSeal' in response.get_json()['error']

    with app.app_context():
        assert TransactionDocument.query.get(parent_id) is not None
        assert TransactionDocument.query.get(child_id) is not None


def test_replace_clears_split_lineage_from_the_previous_upload(app, owner_a_client, seed):
    import io

    with app.app_context():
        parent_id, child_id = _packet_with_child(seed)

    with patch('threading.Thread.start'), \
         patch('services.supabase_storage.delete_transaction_document'), \
         patch('services.supabase_storage.get_supabase_client'), \
         patch(
             'services.supabase_storage.upload_external_document',
             return_value={'path': 'org/tx/replacement.pdf'},
         ):
        response = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/documents/{parent_id}/fulfill',
            data={'file': (io.BytesIO(b'%PDF-1.4 replacement'), 'listing.pdf')},
            content_type='multipart/form-data',
        )

    assert response.status_code == 200, response.get_data(as_text=True)

    with app.app_context():
        parent = TransactionDocument.query.get(parent_id)
        assert parent.signed_file_path == 'org/tx/replacement.pdf'
        assert parent.source_file_path is None
        assert parent.page_start is None
        assert parent.split_source is None
        assert TransactionDocument.query.get(child_id) is None
