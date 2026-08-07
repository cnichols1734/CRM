"""Multi-PDF inbox batch: wait for identities, pick listing as primary."""

from types import SimpleNamespace

from models import ContractBootstrapSession
from services.contract_bootstrap import (
    batch_item_phase,
    batch_sessions_ready,
    build_batch_status_payload,
    select_primary_bootstrap_session,
)


def _session(session_id, *, kind, status, confidence=0.9):
    return SimpleNamespace(
        id=session_id,
        status=status,
        original_filename=f'{kind}-{session_id}.pdf',
        classification={
            'document_identity': {
                'kind': kind,
                'confidence': confidence,
                'label': kind,
            },
            'upload_batch_id': 'batch-1',
        },
    )


def test_select_primary_prefers_listing_over_supporting_docs():
    sessions = [
        _session(1, kind='disclosure', status=ContractBootstrapSession.STATUS_AWAITING_REVIEW),
        _session(2, kind='other', status=ContractBootstrapSession.STATUS_AWAITING_REVIEW),
        _session(
            3,
            kind='listing_agreement',
            status=ContractBootstrapSession.STATUS_AWAITING_REVIEW,
        ),
        _session(4, kind='addendum', status=ContractBootstrapSession.STATUS_AWAITING_REVIEW),
    ]
    primary = select_primary_bootstrap_session(sessions)
    assert primary.id == 3
    assert primary.classification['document_identity']['kind'] == 'listing_agreement'


def test_select_primary_prefers_purchase_when_no_listing():
    sessions = [
        _session(10, kind='disclosure', status=ContractBootstrapSession.STATUS_AWAITING_REVIEW),
        _session(
            11,
            kind='purchase_contract',
            status=ContractBootstrapSession.STATUS_AWAITING_REVIEW,
        ),
    ]
    primary = select_primary_bootstrap_session(sessions)
    assert primary.id == 11


def test_batch_not_ready_while_processing():
    sessions = [
        _session(
            1,
            kind='listing_agreement',
            status=ContractBootstrapSession.STATUS_AWAITING_REVIEW,
        ),
        _session(2, kind='unknown', status=ContractBootstrapSession.STATUS_PROCESSING),
    ]
    assert batch_sessions_ready(sessions) is False


def test_batch_not_ready_while_queued():
    sessions = [
        _session(
            1,
            kind='listing_agreement',
            status=ContractBootstrapSession.STATUS_PROCESSING,
        ),
        _session(2, kind='disclosure', status=ContractBootstrapSession.STATUS_UPLOADED),
    ]
    assert batch_sessions_ready(sessions) is False


def test_batch_ready_when_all_identified():
    sessions = [
        _session(
            1,
            kind='listing_agreement',
            status=ContractBootstrapSession.STATUS_AWAITING_REVIEW,
        ),
        _session(2, kind='disclosure', status=ContractBootstrapSession.STATUS_AWAITING_MATCH),
    ]
    assert batch_sessions_ready(sessions) is True


def test_batch_item_phases_progress_one_at_a_time():
    assert batch_item_phase(ContractBootstrapSession.STATUS_UPLOADED) == 'queued'
    assert batch_item_phase(ContractBootstrapSession.STATUS_PROCESSING) == 'reading'
    assert batch_item_phase(ContractBootstrapSession.STATUS_AWAITING_REVIEW) == 'identified'
    assert batch_item_phase(ContractBootstrapSession.STATUS_FAILED) == 'failed'

    sessions = [
        _session(1, kind='disclosure', status=ContractBootstrapSession.STATUS_AWAITING_REVIEW),
        _session(2, kind='addendum', status=ContractBootstrapSession.STATUS_PROCESSING),
        _session(3, kind='listing_agreement', status=ContractBootstrapSession.STATUS_UPLOADED),
    ]
    payload = build_batch_status_payload(sessions=sessions, batch_id='batch-1')
    phases = [item['phase'] for item in payload['items']]
    assert phases == ['identified', 'reading', 'queued']
    assert payload['identified_count'] == 1
    assert payload['reading_count'] == 1
    assert payload['ready'] is False
