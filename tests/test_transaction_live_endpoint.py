"""Coverage for the unified transaction live-status endpoint."""

from models import DocumentReviewReport, TransactionDocument, db


def _clear_review_reports(seed):
    DocumentReviewReport.query.filter_by(
        transaction_id=seed['tx_a'],
        organization_id=seed['org_a'],
    ).delete(synchronize_session=False)
    db.session.commit()


def _set_extraction_status(seed, status):
    doc = db.session.get(TransactionDocument, seed['doc_a'])
    doc.extraction_status = status
    db.session.commit()


def test_live_returns_stable_version_and_idle_state(app, seed, owner_a_client):
    with app.app_context():
        _clear_review_reports(seed)
        _set_extraction_status(seed, 'complete')

    first = owner_a_client.get(f'/transactions/{seed["tx_a"]}/live')
    assert first.status_code == 200
    payload = first.get_json()

    assert set(payload) == {
        'version', 'in_flight', 'reviews', 'proposals', 'extraction', 'offers',
    }
    assert isinstance(payload['version'], str) and len(payload['version']) == 40
    assert isinstance(payload['in_flight'], bool)

    second = owner_a_client.get(f'/transactions/{seed["tx_a"]}/live')
    assert second.get_json()['version'] == payload['version']


def test_live_reports_in_flight_while_extraction_pending(app, seed, owner_a_client):
    try:
        with app.app_context():
            _clear_review_reports(seed)
            _set_extraction_status(seed, 'processing')

        payload = owner_a_client.get(f'/transactions/{seed["tx_a"]}/live').get_json()
        assert payload['in_flight'] is True
        assert payload['extraction']['listing_status'] == 'processing'
        assert payload['extraction']['processing_count'] >= 1

        with app.app_context():
            _set_extraction_status(seed, 'complete')

        after = owner_a_client.get(f'/transactions/{seed["tx_a"]}/live').get_json()
        assert after['extraction']['listing_status'] == 'complete'
        assert after['version'] != payload['version']
    finally:
        with app.app_context():
            _set_extraction_status(seed, None)


def test_live_version_changes_when_report_is_resolved(app, seed, owner_a_client):
    report_id = None
    try:
        with app.app_context():
            _clear_review_reports(seed)
            report = DocumentReviewReport(
                organization_id=seed['org_a'],
                transaction_id=seed['tx_a'],
                document_id=seed['doc_a'],
                severity='attention',
                status='open',
                title='Check the closing date',
                summary='BOB found a date conflict.',
                findings=[{
                    'code': 'date_conflict',
                    'severity': 'attention',
                    'message': 'Closing date does not match the contract.',
                }],
                toast_required=False,
            )
            db.session.add(report)
            db.session.commit()
            report_id = report.id

        before = owner_a_client.get(f'/transactions/{seed["tx_a"]}/live').get_json()
        assert before['reviews']['report_count'] == 1
        assert before['reviews']['attention_count'] == 1
        assert 'Closing date does not match the contract.' in before['reviews']['html']

        resolve = owner_a_client.post(
            f'/transactions/{seed["tx_a"]}/document-reviews/{report_id}/resolve',
            headers={'Accept': 'application/json'},
        )
        assert resolve.status_code == 200

        after = owner_a_client.get(f'/transactions/{seed["tx_a"]}/live').get_json()
        assert after['version'] != before['version']
        assert after['reviews']['report_count'] == 0
    finally:
        with app.app_context():
            if report_id:
                DocumentReviewReport.query.filter_by(id=report_id).delete(
                    synchronize_session=False,
                )
                db.session.commit()


def test_live_is_not_readable_across_organizations(seed, owner_b_client):
    response = owner_b_client.get(f'/transactions/{seed["tx_a"]}/live')
    assert response.status_code != 200
