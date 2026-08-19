"""Tests for the merged requirements + documents checklist."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from models import (
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    TransactionRequirementEvidence,
    TransactionType,
    db,
)
from services.checklist_service import (
    absorb_matching_placeholder,
    build_checklist,
    ensure_expected_placeholder,
)
from services.deadline_rules import DeadlineRulesService
from services.listing_prep_checklist import (
    AUTO_KEYS,
    VISIBLE_KEYS,
    remaining_listing_slugs,
    listing_prep_groups,
    seed_listing_prep_checklist,
    sync_listing_prep_checklist,
)
from services.requirement_evidence import auto_attach_for_document


def _cleanup_tx(org_id, tx_id, *, delete_tx=False):
    req_ids = [
        r.id
        for r in TransactionRequirement.query.filter_by(
            organization_id=org_id, transaction_id=tx_id,
        ).all()
    ]
    if req_ids:
        TransactionRequirementEvidence.query.filter(
            TransactionRequirementEvidence.requirement_id.in_(req_ids),
        ).delete(synchronize_session=False)
        TransactionRequirement.query.filter(
            TransactionRequirement.id.in_(req_ids),
        ).delete(synchronize_session=False)
    TransactionDocument.query.filter_by(
        organization_id=org_id, transaction_id=tx_id,
    ).delete(synchronize_session=False)
    if delete_tx:
        Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
    db.session.commit()


def _fresh_tx(seed, *, status='under_contract', side='buyer'):
    tx_type = TransactionType.query.filter_by(
        organization_id=seed['org_a'], name=side,
    ).first()
    tx = Transaction(
        organization_id=seed['org_a'],
        created_by_id=seed['owner_a'],
        transaction_type_id=tx_type.id,
        street_address='500 Checklist Ln',
        city='Austin',
        state='TX',
        status=status,
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _req(org_id, tx_id, **kwargs):
    defaults = dict(
        organization_id=org_id,
        transaction_id=tx_id,
        package_key='buyer_ctc',
        phase_key='due_diligence',
        requirement_key='survey',
        title='Survey Received',
        work_status='pending',
        deadline_rule_version='v1',
    )
    defaults.update(kwargs)
    req = TransactionRequirement(**defaults)
    db.session.add(req)
    db.session.flush()
    return req


def _doc(org_id, tx_id, **kwargs):
    defaults = dict(
        organization_id=org_id,
        transaction_id=tx_id,
        template_slug='survey',
        template_name='Survey',
        status='pending',
        is_placeholder=True,
    )
    defaults.update(kwargs)
    doc = TransactionDocument(**defaults)
    db.session.add(doc)
    db.session.flush()
    return doc


@pytest.fixture(autouse=True)
def _clear_pack_cache():
    DeadlineRulesService.clear_cache()
    yield
    DeadlineRulesService.clear_cache()


class TestChecklistService:
    def test_requirement_folds_uploaded_document(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed)
                tx_id = tx.id
                _req(
                    seed['org_a'], tx_id,
                    requirement_key='survey',
                    due_at=datetime(2026, 2, 1),
                )
                _doc(
                    seed['org_a'], tx_id,
                    template_slug='survey',
                    template_name='Survey',
                    status='signed',
                    is_placeholder=False,
                    signed_file_path='storage/survey.pdf',
                )
                db.session.commit()

                items = build_checklist(tx, seed['org_a'])
                assert len(items) == 1
                item = items[0]
                assert item['kind'] == 'requirement'
                assert item['key'] == 'survey'
                assert item['document_state'] == 'uploaded'
                assert item['expected_document_slug'] == 'survey'
                assert item['document'] is not None
                assert item['document']['template_slug'] == 'survey'
                assert not any(i['kind'] == 'document' for i in items)
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_requirement_placeholder_is_missing(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed)
                tx_id = tx.id
                _req(
                    seed['org_a'], tx_id,
                    requirement_key='survey',
                    due_at=datetime(2026, 2, 1),
                )
                _doc(
                    seed['org_a'], tx_id,
                    template_slug='survey',
                    is_placeholder=True,
                    status='pending',
                )
                db.session.commit()

                items = build_checklist(tx, seed['org_a'])
                assert len(items) == 1
                assert items[0]['document_state'] == 'missing'
                assert items[0]['expected_document_exists'] is True
                assert items[0]['document'] is not None
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_missing_without_placeholder_flag(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed)
                tx_id = tx.id
                _req(
                    seed['org_a'], tx_id,
                    requirement_key='survey',
                    due_at=datetime(2026, 2, 1),
                )
                db.session.commit()

                items = build_checklist(tx, seed['org_a'])
                assert len(items) == 1
                assert items[0]['document_state'] == 'missing'
                assert items[0]['expected_document_exists'] is False
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_standalone_document_appears(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed)
                tx_id = tx.id
                _req(
                    seed['org_a'], tx_id,
                    requirement_key='closing',
                    phase_key='closing',
                    title='Closing',
                    due_at=datetime(2026, 3, 1),
                )
                _doc(
                    seed['org_a'], tx_id,
                    template_slug='hoa-addendum',
                    template_name='HOA Addendum',
                    is_placeholder=True,
                )
                db.session.commit()

                items = build_checklist(tx, seed['org_a'])
                kinds = {(i['kind'], i['key']) for i in items}
                assert ('requirement', 'closing') in kinds
                assert ('document', 'hoa-addendum') in kinds
                standalone = next(i for i in items if i['kind'] == 'document')
                assert standalone['due_at'] is None
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_ordering_dated_before_undated(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed)
                tx_id = tx.id
                later = datetime.utcnow() + timedelta(days=10)
                earlier = datetime.utcnow() + timedelta(days=2)
                _req(
                    seed['org_a'], tx_id,
                    requirement_key='survey',
                    title='Survey Received',
                    due_at=later,
                )
                _req(
                    seed['org_a'], tx_id,
                    requirement_key='earnest_money',
                    phase_key='option_period',
                    title='Earnest Money Deposited',
                    due_at=earlier,
                )
                _req(
                    seed['org_a'], tx_id,
                    requirement_key='closing',
                    phase_key='closing',
                    title='Closing Undated',
                    due_at=None,
                )
                _doc(
                    seed['org_a'], tx_id,
                    template_slug='wire-fraud-warning',
                    template_name='Wire Fraud Warning',
                )
                db.session.commit()

                items = build_checklist(tx, seed['org_a'])
                keys = [i['key'] for i in items]
                assert keys.index('earnest_money') < keys.index('survey')
                assert keys.index('survey') < keys.index('closing')
                assert keys.index('closing') < keys.index('wire-fraud-warning')
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_empty_transaction_returns_empty_list(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed)
                tx_id = tx.id
                db.session.commit()
                assert build_checklist(tx, seed['org_a']) == []
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)


class TestEnsureExpectedPlaceholder:
    def test_creates_exact_slug_not_custom(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed, side='seller', status='under_contract')
                tx_id = tx.id
                req = _req(
                    seed['org_a'], tx_id,
                    package_key='seller_ctc',
                    requirement_key='survey',
                    title='Survey Completed',
                )
                db.session.commit()

                doc = ensure_expected_placeholder(
                    tx, seed['org_a'], req, actor_id=seed['owner_a'],
                )
                db.session.commit()

                assert doc.template_slug == 'survey'
                assert not doc.template_slug.startswith('custom-')
                assert doc.is_placeholder is True
                assert doc.status == 'pending'
                assert doc.document_source == 'placeholder'
                assert 'Survey' in (doc.included_reason or '')
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_idempotent_same_document(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed, side='seller', status='under_contract')
                tx_id = tx.id
                req = _req(
                    seed['org_a'], tx_id,
                    package_key='seller_ctc',
                    requirement_key='survey',
                    title='Survey Completed',
                )
                db.session.commit()

                first = ensure_expected_placeholder(tx, seed['org_a'], req)
                db.session.commit()
                second = ensure_expected_placeholder(tx, seed['org_a'], req)
                db.session.commit()

                assert first.id == second.id
                count = TransactionDocument.query.filter_by(
                    organization_id=seed['org_a'],
                    transaction_id=tx_id,
                    template_slug='survey',
                ).count()
                assert count == 1
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_no_expected_slug_raises(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed, side='seller', status='under_contract')
                tx_id = tx.id
                req = _req(
                    seed['org_a'], tx_id,
                    package_key='seller_ctc',
                    requirement_key='closing',
                    phase_key='closing',
                    title='Closing',
                )
                db.session.commit()

                with pytest.raises(ValueError, match='no expected document'):
                    ensure_expected_placeholder(tx, seed['org_a'], req)
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_seller_ctc_ensure_then_auto_attach(self, app, seed):
        """Seller CTC survey is unreachable via intake — ensure + upload must work."""
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed, side='seller', status='under_contract')
                tx_id = tx.id
                req = _req(
                    seed['org_a'], tx_id,
                    package_key='seller_ctc',
                    requirement_key='survey',
                    title='Survey Completed',
                    work_status='pending',
                )
                db.session.commit()

                doc = ensure_expected_placeholder(
                    tx, seed['org_a'], req, actor_id=seed['owner_a'],
                )
                assert doc.template_slug == 'survey'

                # Simulate fulfill/upload of that placeholder.
                doc.is_placeholder = False
                doc.status = 'signed'
                doc.signed_file_path = 'storage/survey.pdf'
                doc.document_source = 'completed'
                db.session.flush()

                touched = auto_attach_for_document(doc, actor_id=seed['owner_a'])
                db.session.commit()

                assert [r.id for r in touched] == [req.id]
                refreshed = db.session.get(TransactionRequirement, req.id)
                assert refreshed.work_status == 'in_progress'
                assert refreshed.work_status != 'completed'
                assert TransactionRequirementEvidence.query.filter_by(
                    organization_id=seed['org_a'],
                    requirement_id=req.id,
                    document_id=doc.id,
                ).count() == 1

                items = build_checklist(tx, seed['org_a'])
                survey_item = next(i for i in items if i['key'] == 'survey')
                assert survey_item['document_state'] == 'uploaded'
                assert survey_item['expected_document_exists'] is True
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)


class TestExpectedDocumentRoute:
    def test_route_success(self, app, seed, owner_a_client):
        tx_id = req_id = None
        with app.app_context():
            tx = _fresh_tx(seed, side='seller', status='under_contract')
            tx_id = tx.id
            req = _req(
                seed['org_a'], tx_id,
                package_key='seller_ctc',
                requirement_key='survey',
                title='Survey Completed',
            )
            db.session.commit()
            req_id = req.id

        try:
            resp = owner_a_client.post(
                f'/transactions/{tx_id}/requirements/{req_id}/expected-document',
            )
            assert resp.status_code == 200
            payload = resp.get_json()
            assert payload['success'] is True
            assert payload['document']['template_slug'] == 'survey'
            assert not payload['document']['template_slug'].startswith('custom-')
            assert payload['document']['is_placeholder'] is True
            assert payload['document']['id']
        finally:
            with app.app_context():
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_route_cross_org_404(self, app, seed, owner_a_client):
        req_id = None
        with app.app_context():
            # Requirement on org B's transaction — org A client must 404.
            req = TransactionRequirement(
                organization_id=seed['org_b'],
                transaction_id=seed['tx_b'],
                package_key='seller_ctc',
                phase_key='due_diligence',
                requirement_key='survey',
                title='Survey Completed',
                work_status='pending',
                deadline_rule_version='v1',
            )
            db.session.add(req)
            db.session.commit()
            req_id = req.id

        try:
            resp = owner_a_client.post(
                f'/transactions/{seed["tx_b"]}/requirements/{req_id}/expected-document',
            )
            assert resp.status_code == 404
        finally:
            with app.app_context():
                TransactionRequirementEvidence.query.filter_by(
                    requirement_id=req_id,
                ).delete(synchronize_session=False)
                TransactionRequirement.query.filter_by(id=req_id).delete(
                    synchronize_session=False,
                )
                db.session.commit()

    def test_route_no_slug_400(self, app, seed, owner_a_client):
        tx_id = req_id = None
        with app.app_context():
            tx = _fresh_tx(seed, side='seller', status='under_contract')
            tx_id = tx.id
            req = _req(
                seed['org_a'], tx_id,
                package_key='seller_ctc',
                requirement_key='closing',
                phase_key='closing',
                title='Closing',
            )
            db.session.commit()
            req_id = req.id

        try:
            resp = owner_a_client.post(
                f'/transactions/{tx_id}/requirements/{req_id}/expected-document',
            )
            assert resp.status_code == 400
            payload = resp.get_json()
            assert payload['success'] is False
            assert 'expected document' in payload['error'].lower()
        finally:
            with app.app_context():
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)


def test_absorb_matching_placeholder_folds_open_slot(app, seed):
    """Classified upload takes an open placeholder slot for the same slug."""
    with app.app_context():
        tx = _fresh_tx(seed, side='seller', status='preparing_to_list')
        placeholder = _doc(
            seed['org_a'], tx.id,
            template_slug='iabs',
            template_name='Information About Brokerage Services',
            status='pending',
            is_placeholder=True,
            document_source='placeholder',
            included_reason='Required by questionnaire',
        )
        uploaded = _doc(
            seed['org_a'], tx.id,
            template_slug='iabs',
            template_name='IABS',
            status='signed',
            is_placeholder=False,
            document_source='completed',
            signed_file_path='/tmp/iabs.pdf',
        )
        db.session.commit()
        placeholder_id = placeholder.id
        uploaded_id = uploaded.id
        org_id = seed['org_a']
        tx_id = tx.id

        try:
            absorbed = absorb_matching_placeholder(uploaded)
            db.session.commit()
            assert absorbed == placeholder_id
            assert TransactionDocument.query.get(placeholder_id) is None
            kept = TransactionDocument.query.get(uploaded_id)
            assert kept is not None
            assert kept.included_reason == 'Required by questionnaire'
        finally:
            _cleanup_tx(org_id, tx_id, delete_tx=True)


class TestListingPrepChecklist:
    def test_listing_agreement_upload_auto_checks_sign_row(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed, side='seller', status='preparing_to_list')
                tx_id = tx.id
                seed_listing_prep_checklist(tx, seed['org_a'], actor_id=seed['owner_a'])
                db.session.commit()

                req = TransactionRequirement.query.filter_by(
                    transaction_id=tx_id,
                    requirement_key='listing_agreement',
                ).one()
                assert req.work_status == 'pending'
                assert req.due_at is None

                _doc(
                    seed['org_a'], tx_id,
                    template_slug='listing-agreement',
                    template_name='Listing Agreement',
                    status='signed',
                    is_placeholder=False,
                    signed_file_path='storage/listing.pdf',
                )
                db.session.commit()
                sync_listing_prep_checklist(tx, actor_id=seed['owner_a'])
                db.session.commit()

                refreshed = db.session.get(TransactionRequirement, req.id)
                assert refreshed.work_status == 'completed'
                groups = listing_prep_groups(tx)
                labels = [group['label'] for group in groups]
                assert labels == [
                    'Listing Documents',
                    'Property & Marketing Prep',
                    'MLS Setup',
                ]
                visible = [
                    item['key']
                    for group in groups
                    for item in group['rows']
                ]
                assert visible == list(VISIBLE_KEYS)
                assert 'photos_ready' not in visible
                sign_row = next(
                    item for item in groups[0]['rows']
                    if item['key'] == 'listing_agreement'
                )
                assert sign_row['done'] is True
                assert sign_row['auto'] is True
                remaining_row = next(
                    item for item in groups[0]['rows']
                    if item['key'] == 'listing_docs_complete'
                )
                assert remaining_row['done'] is False
                assert remaining_row['title'] == 'Upload Remaining Listing Documents'
                assert remaining_row['remaining_count']
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)

    def test_remaining_listing_slugs_skip_agreement_and_disclosure(self, app, seed):
        with app.app_context():
            tx = _fresh_tx(seed, side='seller', status='preparing_to_list')
            slugs = remaining_listing_slugs(tx)
            assert 'listing-agreement' not in slugs
            assert 'sellers-disclosure' not in slugs
            assert slugs
            _cleanup_tx(seed['org_a'], tx.id, delete_tx=True)

    def test_hidden_pack_keys_are_not_seeded(self, app, seed):
        tx_id = None
        with app.app_context():
            try:
                tx = _fresh_tx(seed, side='seller', status='preparing_to_list')
                tx_id = tx.id
                seed_listing_prep_checklist(tx, seed['org_a'], actor_id=seed['owner_a'])
                db.session.commit()
                keys = {
                    req.requirement_key
                    for req in TransactionRequirement.query.filter_by(
                        transaction_id=tx_id,
                    ).all()
                }
                assert 'photos_ready' not in keys
                assert 'offer_intake_ready' not in keys
                assert AUTO_KEYS <= keys
            finally:
                if tx_id:
                    _cleanup_tx(seed['org_a'], tx_id, delete_tx=True)
