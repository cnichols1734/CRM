"""Document review findings + alert copy regressions."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from models import DocumentReviewReport
from services.document_review import build_findings, _compose_summary


def _tx(**kwargs):
    defaults = dict(
        id=1,
        organization_id=7,
        street_address='123 Main St',
        expected_close_date=date(2026, 9, 12),
        created_by_id=10,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _doc(**kwargs):
    defaults = dict(
        id=99,
        template_slug='accepted-contract',
        template_name='Accepted Contract',
        field_data={},
        extraction_error=None,
        parent_document_id=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_closing_date_conflict_wording():
    with patch('services.document_review.TransactionParticipant') as TP, \
         patch('services.document_review.TransactionDocument') as TD:
        TP.query.filter_by.return_value.all.return_value = []
        TD.query.filter.return_value.all.return_value = []
        TD.query.filter_by.return_value.filter.return_value.count.return_value = 0
        findings, count = build_findings(
            transaction=_tx(),
            document=_doc(),
            field_data={'close_date': '2026-09-19', 'sales_price': '500000'},
        )
    assert count >= 1
    msgs = ' '.join(f['message'] for f in findings)
    assert 'September' in msgs or '2026-09-19' in msgs
    assert 'CRM currently says' in msgs or '2026-09-12' in msgs
    assert 'invalid' not in msgs.lower()


def test_signature_wording_is_careful():
    with patch('services.document_review.TransactionParticipant') as TP, \
         patch('services.document_review.TransactionDocument') as TD:
        TP.query.filter_by.return_value.all.return_value = []
        TD.query.filter.return_value.all.return_value = []
        TD.query.filter_by.return_value.filter.return_value.count.return_value = 0
        findings, _ = build_findings(
            transaction=_tx(),
            document=_doc(),
            field_data={'buyer_signature_detected': False},
        )
    sig = [f for f in findings if f['code'] == 'signature_unconfirmed']
    assert sig
    assert 'could not confirm' in sig[0]['message'].lower()
    assert 'invalid' not in sig[0]['message'].lower()


def _seller_tx(**kwargs):
    return _tx(
        transaction_type=SimpleNamespace(name='seller'),
        **kwargs,
    )


def _review_query_patches():
    return (
        patch('services.document_review.TransactionParticipant'),
        patch('services.document_review.TransactionDocument'),
    )


def test_listing_disclosure_does_not_flag_missing_buyer_signature():
    tp_patch, td_patch = _review_query_patches()
    with tp_patch as TP, td_patch as TD:
        TP.query.filter_by.return_value.all.return_value = []
        TD.query.filter.return_value.all.return_value = []
        TD.query.filter_by.return_value.filter.return_value.count.return_value = 0
        findings, _ = build_findings(
            transaction=_seller_tx(),
            document=_doc(
                template_slug='sellers-disclosure',
                template_name="Seller's Disclosure Notice",
            ),
            field_data={
                'buyer_signature_detected': False,
                'seller_signature_detected': True,
                'sanity_flags': [{
                    'code': 'missing_buyer_signature',
                    'severity': 'attention',
                    'message': 'Buyer acknowledgement signature is blank on page 5.',
                    'field_key': 'buyer_signature_detected',
                    'page': 5,
                }],
            },
        )
    assert not any(f['code'] == 'signature_unconfirmed' for f in findings)
    assert not any('buyer' in (f.get('message') or '').lower() for f in findings)


def test_listing_agreement_does_not_flag_missing_buyer_signature():
    tp_patch, td_patch = _review_query_patches()
    with tp_patch as TP, td_patch as TD:
        TP.query.filter_by.return_value.all.return_value = []
        TD.query.filter.return_value.all.return_value = []
        TD.query.filter_by.return_value.filter.return_value.count.return_value = 0
        findings, _ = build_findings(
            transaction=_seller_tx(),
            document=_doc(
                template_slug='listing-agreement',
                template_name='Listing Agreement',
            ),
            field_data={'buyer_signature_detected': False},
        )
    assert not any(f['code'] == 'signature_unconfirmed' for f in findings)


def test_listing_disclosure_still_flags_missing_seller_signature():
    tp_patch, td_patch = _review_query_patches()
    with tp_patch as TP, td_patch as TD:
        TP.query.filter_by.return_value.all.return_value = []
        TD.query.filter.return_value.all.return_value = []
        TD.query.filter_by.return_value.filter.return_value.count.return_value = 0
        findings, _ = build_findings(
            transaction=_seller_tx(),
            document=_doc(
                template_slug='sellers-disclosure',
                template_name="Seller's Disclosure Notice",
            ),
            field_data={
                'buyer_signature_detected': False,
                'seller_signature_detected': False,
            },
        )
    sig = [f for f in findings if f['code'] == 'signature_unconfirmed']
    assert len(sig) == 1
    assert 'seller' in sig[0]['message'].lower()
    assert 'buyer' not in sig[0]['message'].lower()


def test_offer_disclosure_flags_missing_buyer_signature():
    tp_patch, td_patch = _review_query_patches()
    with tp_patch as TP, td_patch as TD, \
            patch(
                'services.document_signature_policy.SellerOfferDocument'
            ) as SOD, \
            patch(
                'services.document_signature_policy.SellerContractDocument'
            ) as SCD, \
            patch(
                'services.document_review.SellerOfferDocument'
            ) as ReviewSOD:
        TP.query.filter_by.return_value.all.return_value = []
        TD.query.filter.return_value.all.return_value = []
        TD.query.filter_by.return_value.filter.return_value.count.return_value = 0
        SOD.query.filter_by.return_value.first.return_value = object()
        SCD.query.filter_by.return_value.first.return_value = None
        ReviewSOD.query.filter_by.return_value.first.return_value = object()
        findings, _ = build_findings(
            transaction=_seller_tx(),
            document=_doc(
                template_slug='sellers-disclosure',
                template_name="Seller's Disclosure Notice",
            ),
            field_data={'buyer_signature_detected': False},
        )
    sig = [f for f in findings if f['code'] == 'signature_unconfirmed']
    assert sig
    assert 'buyer' in sig[0]['message'].lower()


def test_extraction_context_listing_slot_skips_buyer_signature():
    from services.document_signature_policy import extraction_review_context

    text = extraction_review_context(
        _seller_tx(),
        _doc(
            template_slug='sellers-disclosure',
            template_name="Seller's Disclosure Notice",
        ),
    )
    assert 'LISTING-STAGE' in text
    assert 'buyer_signature_detected must be null' in text
    assert 'seller signature' in text.lower()


def test_ok_summary_never_claims_legal_sufficiency():
    title, summary = _compose_summary(
        address='123 Main',
        findings=[],
        field_count=14,
        severity=DocumentReviewReport.SEVERITY_OK,
    )
    assert 'no obvious crm conflicts' in summary.lower()
    assert 'still need approval' in summary
    assert 'correct' not in summary.lower()
    assert 'legally' not in summary.lower()
    assert 'attention' not in title.lower()


def test_attention_summary_states_nothing_changed():
    findings = [{
        'code': 'date_conflict',
        'severity': DocumentReviewReport.SEVERITY_ATTENTION,
        'message': 'The closing date on page 8 is September 19. The CRM currently says September 12.',
    }]
    title, summary = _compose_summary(
        address='123 Main',
        findings=findings,
        field_count=11,
        severity=DocumentReviewReport.SEVERITY_ATTENTION,
    )
    assert title == 'Review 123 Main'
    assert 'No transaction changes applied' in summary
    assert '11 extracted fields' in summary


def test_unknown_document_uses_universal_texas_transaction_review_schema():
    from services.document_extractor import get_extraction_schema

    schema = get_extraction_schema('custom-title-commitment-package')

    assert 'document_classification' in schema['fields']
    assert 'authoritative_deadlines' in schema['fields']
    assert 'sanity_flags' in schema['fields']
    assert 'never the filename' in schema['system_prompt'].lower()
    assert 'legally sufficient' in schema['system_prompt'].lower()


def test_ai_sanity_flags_become_page_cited_operational_findings():
    with patch('services.document_review.TransactionParticipant') as TP, \
         patch('services.document_review.TransactionDocument') as TD:
        TP.query.filter_by.return_value.all.return_value = []
        TD.query.filter.return_value.all.return_value = []
        findings, count = build_findings(
            transaction=_tx(),
            document=_doc(template_slug='custom-upload'),
            field_data={
                'document_classification': 'title_commitment',
                'document_summary': 'Title commitment for the property.',
                'sanity_flags': [{
                    'code': 'unreadable_page',
                    'severity': 'attention',
                    'message': 'Page 4 is not readable enough to confirm Schedule C.',
                    'page': 4,
                }],
            },
        )

    assert count == 0
    sanity = [finding for finding in findings if finding['code'] == 'unreadable_page']
    assert sanity
    assert sanity[0]['page'] == 4
    assert not any(f['code'] == 'missing_required_documents' for f in findings)
