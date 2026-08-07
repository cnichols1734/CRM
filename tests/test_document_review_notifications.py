"""§34A Document-review notification acceptance (DN8, DN9)."""

from unittest.mock import patch

from models import (
    DocumentReviewReport,
    NotificationEvent,
    Transaction,
    TransactionDocument,
    db,
)
from services.document_review import _compose_summary, finalize_document_review


def test_dn8_clean_review_summary_avoids_legal_claims():
    """
    DN8: Given no CRM conflicts and N fields extracted,
    When the clean (OK) summary is composed,
    Then copy says “no obvious CRM conflicts”, cites fields awaiting approval,
    and never claims valid / legally sufficient / error-free.
    """
    title, summary = _compose_summary(
        address='100 Main St',
        findings=[],
        field_count=9,
        severity=DocumentReviewReport.SEVERITY_OK,
    )
    combined = f'{title}\n{summary}'.lower()
    assert 'no obvious crm conflicts' in combined
    assert '9 extracted fields still need' in combined
    assert 'approval' in combined
    for banned in (
        'valid',
        'legally sufficient',
        'error-free',
        'error free',
        'legally',
    ):
        assert banned not in combined


def test_dn8_finalize_emits_document_review_completed(app, seed):
    """
    DN8 (finalize path): clean review produces document_review_completed
    with safe operational wording in the event payload.
    """
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='hoa-resale-certificate',
            template_name='HOA Resale Certificate',
            signed_original_filename='Oak Ridge HOA resale packet.pdf',
            status='signed',
            field_data={
                'document_classification': 'hoa_resale_certificate',
                'street_address': tx.street_address,
                'buyer_name': 'Jane Doe',
                'seller_name': 'John Smith',
                'notes': 'standard packet',
            },
        )
        db.session.add(doc)
        db.session.commit()

        run_id = 88001
        with patch('services.document_review.create_notification', return_value=None), \
             patch('services.messaging.outbound.notify', create=True):
            report = finalize_document_review(
                document_id=doc.id,
                org_id=seed['org_a'],
                extraction_run_id=run_id,
            )

        assert report is not None
        assert report.severity == DocumentReviewReport.SEVERITY_OK
        summary_l = (report.summary or '').lower()
        assert 'no obvious crm conflicts' in summary_l
        assert 'legally' not in summary_l
        assert 'error-free' not in summary_l
        assert 'valid' not in summary_l

        events = NotificationEvent.query.filter_by(
            organization_id=seed['org_a'],
            dedupe_key=f'extraction_run:{run_id}',
            event_type='document_review_completed',
        ).all()
        assert events
        for event in events:
            payload_text = str(event.payload).lower()
            assert 'no obvious crm conflicts' in payload_text
            assert 'legally sufficient' not in payload_text
            assert event.payload['document_name'] == 'Oak Ridge HOA resale packet.pdf'
            assert event.payload['document_type'] == 'HOA Resale Certificate'
            assert '100 Main St' in event.payload['body']


def test_dn9_finalize_twice_dedupes_notification_event_per_user(app, seed):
    """
    DN9: Given the same extraction_run_id finalized twice,
    When the second notify runs,
    Then one NotificationEvent remains per user (dedupe_key + bucket).
    """
    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='supporting-disclosure',
            template_name='Disclosure',
            status='signed',
            field_data={
                'street_address': tx.street_address,
                'seller_name': 'Owner',
                'buyer_name': 'Buyer',
            },
        )
        db.session.add(doc)
        db.session.commit()

        run_id = 88009
        dedupe_key = f'extraction_run:{run_id}'

        with patch('services.document_review.create_notification', return_value=None), \
             patch('services.messaging.outbound.notify', create=True):
            finalize_document_review(
                document_id=doc.id,
                org_id=seed['org_a'],
                extraction_run_id=run_id,
            )
            finalize_document_review(
                document_id=doc.id,
                org_id=seed['org_a'],
                extraction_run_id=run_id,
            )

        events = NotificationEvent.query.filter_by(
            organization_id=seed['org_a'],
            dedupe_key=dedupe_key,
            dedupe_bucket=str(run_id),
        ).all()
        # One event per notified user (creator at minimum)
        assert events
        by_user = {}
        for event in events:
            by_user.setdefault(event.user_id, []).append(event)
        for user_id, user_events in by_user.items():
            assert len(user_events) == 1, (
                f'Expected one NotificationEvent for user {user_id}, '
                f'got {len(user_events)}'
            )
