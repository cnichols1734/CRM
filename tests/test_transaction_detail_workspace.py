"""Regression coverage for the transaction detail workspace hierarchy."""

from datetime import datetime, timedelta

from models import DocumentReviewReport, TransactionDocument, TransactionRequirement, db


def _clear_review_reports(seed):
    DocumentReviewReport.query.filter_by(
        transaction_id=seed['tx_a'],
        organization_id=seed['org_a'],
    ).delete(synchronize_session=False)
    db.session.commit()


def _clear_requirements(seed):
    TransactionRequirement.query.filter_by(
        transaction_id=seed['tx_a'],
        organization_id=seed['org_a'],
    ).delete(synchronize_session=False)
    db.session.commit()


def test_detail_prioritizes_five_open_requirements(app, seed, owner_a_client):
    created_ids = []
    with app.app_context():
        _clear_review_reports(seed)
        _clear_requirements(seed)
        now = datetime.utcnow()
        requirements = [
            TransactionRequirement(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                package_key='seller_ctc',
                phase_key='under_contract',
                requirement_key=f'detail_workspace_{index}',
                title=f'Workspace requirement {index}',
                work_status='completed' if index == 7 else 'pending',
                due_at=now + timedelta(days=index),
            )
            for index in range(1, 8)
        ]
        db.session.add_all(requirements)
        db.session.commit()
        created_ids = [requirement.id for requirement in requirements]

    try:
        response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert response.status_code == 200

        html = response.get_data(as_text=True)
        assert 'id="transaction-checklist"' in html
        checklist = html.split('id="transaction-checklist"', 1)[1]
        checklist = checklist.split('id="transaction-assignments"', 1)[0]

        for index in range(1, 8):
            assert f'Workspace requirement {index}' in checklist

        assert 'Manage coordination team' in html
        # Seller packages put documents in the workspace; other modes keep
        # #listing-documents / questionnaire anchors for deep-links.
        assert (
            'id="listing-documents"' in html
            or 'id="seller-workspace"' in html
            or 'id="transaction-documents-card"' in html
        )
        assert 'href="#listing-documents"' in html or 'href="#seller-workspace"' in html
    finally:
        with app.app_context():
            TransactionRequirement.query.filter(
                TransactionRequirement.id.in_(created_ids)
            ).delete(synchronize_session=False)
            db.session.commit()


def test_empty_detail_hides_inactive_bob_review(app, seed, owner_a_client):
    with app.app_context():
        _clear_review_reports(seed)
    response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert 'id="document-review-inbox" class="mb-6 scroll-mt-20 hidden"' in html
    assert 'Review BOB suggestion' not in html
    assert 'Upload document' in html


def test_document_finding_is_prioritized_in_next_up_and_can_be_resolved(
    app, seed, owner_a_client,
):
    report_id = None
    with app.app_context():
        _clear_review_reports(seed)
        report = DocumentReviewReport(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            document_id=seed['doc_a'],
            severity='critical',
            status='open',
            title='Document review needs attention',
            summary='The property address does not match. Nothing has been changed.',
            findings=[{
                'code': 'address_mismatch',
                'severity': 'critical',
                'message': 'The property address in the document does not match this transaction.',
                'page': 1,
            }],
            field_count=2,
            toast_required=False,
        )
        db.session.add(report)
        db.session.commit()
        report_id = report.id

    try:
        response = owner_a_client.get(f'/transactions/{seed["tx_a"]}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        next_up = html.split('<ol class="divide-y divide-slate-100">', 1)[1]
        next_up = next_up.split('</ol>', 1)[0]
        assert 'The property address in the document does not match this transaction.' in next_up
        assert f'openDocumentReview({report_id})' in next_up

        resolved = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/document-reviews/{report_id}/resolve',
            headers={'Accept': 'application/json'},
        )
        assert resolved.status_code == 200
        with app.app_context():
            assert db.session.get(DocumentReviewReport, report_id).status == 'resolved'
    finally:
        with app.app_context():
            DocumentReviewReport.query.filter_by(id=report_id).delete()
            db.session.commit()


def test_live_document_review_payload_leads_with_file_identity(
    app, seed, owner_a_client,
):
    report_id = None
    with app.app_context():
        _clear_review_reports(seed)
        doc = db.session.get(TransactionDocument, seed['doc_a'])
        doc.signed_original_filename = '4327 Sunrise seller estimate.pdf'
        doc.field_data = {
            'document_classification': 'seller_estimate',
            'form_identifier': 'Seller estimate worksheet',
        }
        report = DocumentReviewReport(
            organization_id=seed['org_a'],
            transaction_id=seed['tx_a'],
            document_id=doc.id,
            severity='attention',
            status='open',
            title='Review 4327 Sunrise seller estimate.pdf',
            summary='The seller name differs from the transaction.',
            findings=[{
                'code': 'party_mismatch',
                'severity': 'attention',
                'message': 'The seller name differs from the transaction.',
                'page': 1,
            }],
            field_count=1,
            toast_required=True,
        )
        db.session.add(report)
        db.session.commit()
        report_id = report.id

    try:
        response = owner_a_client.get(
            f'/transactions/{seed["tx_a"]}/document-reviews',
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['pending_toasts'][0]['document_name'] == (
            '4327 Sunrise seller estimate.pdf'
        )
        assert payload['pending_toasts'][0]['document_type'] == 'Seller Estimate'
        assert payload['pending_toasts'][0]['transaction_address'] == '100 Main St'
        assert '4327 Sunrise seller estimate.pdf' in payload['html']
        assert 'Document type:' in payload['html']
        assert 'Seller Estimate' in payload['html']
    finally:
        with app.app_context():
            DocumentReviewReport.query.filter_by(id=report_id).delete()
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            doc.signed_original_filename = None
            doc.field_data = {}
            db.session.commit()
