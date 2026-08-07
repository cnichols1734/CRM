"""Document review workspace: PDF proxy, field pane, resolve."""

from models import (
    DocumentReviewReport,
    TransactionChangeProposal,
    TransactionDocument,
    db,
)
from routes.transactions.document_review import build_workspace_fields


def _cleanup_reports(seed, report_ids=None):
    query = DocumentReviewReport.query.filter_by(
        organization_id=seed['org_a'],
        transaction_id=seed['tx_a'],
    )
    if report_ids:
        query = query.filter(DocumentReviewReport.id.in_(report_ids))
    query.delete(synchronize_session=False)
    db.session.commit()


def _cleanup_proposals(proposal_ids):
    if not proposal_ids:
        return
    TransactionChangeProposal.query.filter(
        TransactionChangeProposal.id.in_(proposal_ids),
    ).delete(synchronize_session=False)
    db.session.commit()


def test_workspace_route_renders_fields_and_findings(app, seed, owner_a_client):
    report_id = None
    original_field_data = None
    original_filename = None
    try:
        with app.app_context():
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            original_field_data = doc.field_data
            original_filename = doc.signed_original_filename
            doc.signed_original_filename = '6004-Lakeside-Executed.pdf'
            doc.field_data = {
                'sales_price': '450000',
                'closing_date': '2026-09-15',
                'document_summary': 'Purchase contract for 6004 Lakeside.',
                '_meta': {
                    'sales_price': {'page': 1, 'quote': 'Sales Price $450,000'},
                    'closing_date': {'page': 2, 'quote': 'on or before September 15'},
                },
            }
            report = DocumentReviewReport(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                document_id=doc.id,
                severity='attention',
                status='open',
                title='Review 6004 Lakeside',
                summary='Closing date differs from CRM.',
                findings=[{
                    'code': 'date_conflict',
                    'severity': 'attention',
                    'message': 'The closing date on page 2 is September 15.',
                    'field_key': 'closing_date',
                    'page': 2,
                }],
                field_count=2,
                toast_required=False,
            )
            db.session.add(report)
            db.session.commit()
            report_id = report.id
            doc_id = doc.id
            tx_id = seed['tx_a']

        response = owner_a_client.get(f'/transactions/{tx_id}/documents/{doc_id}/review')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert '6004-Lakeside-Executed.pdf' in html
        assert 'Sales price' in html
        assert 'Closing date' in html
        assert 'The closing date on page 2 is September 15.' in html
        assert 'Purchase contract for 6004 Lakeside.' in html
        assert 'Document review' in html
    finally:
        with app.app_context():
            if report_id:
                _cleanup_reports(seed, [report_id])
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            if doc is not None:
                doc.field_data = original_field_data
                doc.signed_original_filename = original_filename
                db.session.commit()


def test_workspace_route_forbidden_for_other_org(app, seed, owner_b_client):
    from models import Organization

    with app.app_context():
        org_b = db.session.get(Organization, seed['org_b'])
        original_tier = org_b.subscription_tier
        # Free tier redirects before auth; promote so we hit the org gate.
        org_b.subscription_tier = 'pro'
        db.session.commit()

    try:
        response = owner_b_client.get(
            f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/review',
        )
        assert response.status_code in (403, 404)
    finally:
        with app.app_context():
            org_b = db.session.get(Organization, seed['org_b'])
            org_b.subscription_tier = original_tier
            db.session.commit()


def test_pdf_proxy_404_without_stored_file(app, seed, owner_a_client):
    with app.app_context():
        doc = db.session.get(TransactionDocument, seed['doc_a'])
        original_source = doc.source_file_path
        original_signed = doc.signed_file_path
        doc.source_file_path = None
        doc.signed_file_path = None
        db.session.commit()

    try:
        response = owner_a_client.get(
            f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/pdf',
        )
        assert response.status_code == 404
    finally:
        with app.app_context():
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            doc.source_file_path = original_source
            doc.signed_file_path = original_signed
            db.session.commit()


def test_pdf_proxy_streams_inline_pdf(app, seed, owner_a_client, monkeypatch):
    pdf_bytes = b'%PDF-1.4 fake content'

    def fake_download(path):
        assert path == 'org/docs/contract.pdf'
        return pdf_bytes

    # Imported inside the view; patch the module attribute it binds from.
    monkeypatch.setattr(
        'services.supabase_storage.download_document',
        fake_download,
    )

    with app.app_context():
        doc = db.session.get(TransactionDocument, seed['doc_a'])
        original_source = doc.source_file_path
        original_filename = doc.signed_original_filename
        doc.source_file_path = 'org/docs/contract.pdf'
        doc.signed_original_filename = 'Offer.pdf'
        db.session.commit()

    try:
        response = owner_a_client.get(
            f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/pdf',
        )
        assert response.status_code == 200
        assert response.mimetype == 'application/pdf'
        assert response.data == pdf_bytes
        disposition = response.headers.get('Content-Disposition', '')
        assert 'inline' in disposition
        assert 'Offer.pdf' in disposition
        assert response.headers.get('Cache-Control') == 'private, max-age=300'
    finally:
        with app.app_context():
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            doc.source_file_path = original_source
            doc.signed_original_filename = original_filename
            db.session.commit()


def test_workspace_resolve_marks_report_resolved(app, seed, owner_a_client):
    report_id = None
    try:
        with app.app_context():
            report = DocumentReviewReport(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                document_id=seed['doc_a'],
                severity='attention',
                status='open',
                title='Review doc',
                summary='Needs a look.',
                findings=[{
                    'code': 'party_mismatch',
                    'severity': 'attention',
                    'message': 'Seller name differs.',
                }],
                field_count=1,
                toast_required=True,
            )
            db.session.add(report)
            db.session.commit()
            report_id = report.id

        response = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/review/resolve',
            headers={'Accept': 'application/json'},
        )
        assert response.status_code == 200
        assert response.get_json().get('ok') is True

        with app.app_context():
            report = db.session.get(DocumentReviewReport, report_id)
            assert report.status == DocumentReviewReport.STATUS_RESOLVED
            assert report.toast_dismissed_at is not None
    finally:
        with app.app_context():
            if report_id:
                _cleanup_reports(seed, [report_id])


def test_fields_ordering_puts_proposed_first():
    class FakeProposal:
        proposed_changes = {'zip_code': '78701', 'sales_price': '400000'}

    fields, general, _by_field, summary = build_workspace_fields(
        field_data={
            'document_summary': 'A clean contract summary.',
            'alpha_field': 'zzz',
            'sales_price': '400000',
            'zip_code': '78701',
            'earnest_money': '5000',
            '_meta': {
                'earnest_money': {'page': 1},
                'sales_price': {'page': 3},
                'zip_code': {'page': 2},
                'alpha_field': {'page': 1},
            },
        },
        findings=[{
            'code': 'amount_missing_context',
            'severity': 'attention',
            'message': 'Earnest money looks high.',
            'field_key': 'earnest_money',
        }, {
            'code': 'general_note',
            'severity': 'attention',
            'message': 'Check exhibits.',
        }],
        proposal=FakeProposal(),
    )

    keys = [field['key'] for field in fields]
    assert keys[0] in ('sales_price', 'zip_code')
    assert keys[1] in ('sales_price', 'zip_code')
    assert 'earnest_money' in keys
    assert keys.index('earnest_money') < keys.index('alpha_field')
    assert fields[keys.index('sales_price')]['proposed'] is True
    assert summary == 'A clean contract summary.'
    assert len(general) == 1
    assert general[0]['code'] == 'general_note'


def test_workspace_shows_proposed_badge(app, seed, owner_a_client):
    proposal_id = None
    original_field_data = None
    try:
        with app.app_context():
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            original_field_data = doc.field_data
            doc.field_data = {
                'sales_price': '412000',
                'buyer_name': 'Ada Lovelace',
            }
            proposal = TransactionChangeProposal(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                change_type='update_extracted_fields',
                proposed_changes={'sales_price': '412000'},
                source_document_id=doc.id,
                status='pending',
                rationale='From uploaded contract',
            )
            db.session.add(proposal)
            db.session.commit()
            proposal_id = proposal.id

        response = owner_a_client.get(
            f'/transactions/{seed["tx_a"]}/documents/{seed["doc_a"]}/review',
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Proposed' in html
        assert 'data-proposed="true"' in html
        assert 'sales_price' in html
    finally:
        with app.app_context():
            _cleanup_proposals([proposal_id] if proposal_id else [])
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            doc.field_data = original_field_data
            db.session.commit()
