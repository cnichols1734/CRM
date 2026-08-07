"""Tests for requirement ↔ document evidence linking."""
from __future__ import annotations

from datetime import datetime

import pytest

from models import (
    TransactionDocument,
    TransactionRequirement,
    TransactionRequirementEvidence,
    db,
)
from services.deadline_rules import DeadlineRulesService
from services.requirement_evidence import (
    attach_document,
    auto_attach_for_document,
    detach_document,
)


def _cleanup(org_id, tx_id, req_ids=None, doc_ids=None):
    if req_ids:
        TransactionRequirementEvidence.query.filter(
            TransactionRequirementEvidence.requirement_id.in_(req_ids),
        ).delete(synchronize_session=False)
        TransactionRequirement.query.filter(
            TransactionRequirement.id.in_(req_ids),
        ).delete(synchronize_session=False)
    else:
        req_ids_q = [
            r.id
            for r in TransactionRequirement.query.filter_by(
                organization_id=org_id, transaction_id=tx_id,
            ).all()
        ]
        if req_ids_q:
            TransactionRequirementEvidence.query.filter(
                TransactionRequirementEvidence.requirement_id.in_(req_ids_q),
            ).delete(synchronize_session=False)
            TransactionRequirement.query.filter(
                TransactionRequirement.id.in_(req_ids_q),
            ).delete(synchronize_session=False)

    if doc_ids:
        TransactionDocument.query.filter(
            TransactionDocument.id.in_(doc_ids),
        ).delete(synchronize_session=False)
    db.session.commit()


def _make_requirement(org_id, tx_id, *, key='survey', package_key='buyer_ctc', **kwargs):
    defaults = dict(
        organization_id=org_id,
        transaction_id=tx_id,
        package_key=package_key,
        phase_key='due_diligence',
        requirement_key=key,
        title=key.replace('_', ' ').title(),
        work_status='pending',
        deadline_rule_version='v1',
    )
    defaults.update(kwargs)
    req = TransactionRequirement(**defaults)
    db.session.add(req)
    db.session.flush()
    return req


def _make_document(org_id, tx_id, *, slug='survey', placeholder=True, **kwargs):
    defaults = dict(
        organization_id=org_id,
        transaction_id=tx_id,
        template_slug=slug,
        template_name=slug.replace('-', ' ').title(),
        status='pending',
        is_placeholder=placeholder,
    )
    if not placeholder:
        defaults['status'] = 'signed'
        defaults['signed_file_path'] = f'storage/{slug}.pdf'
        defaults['is_placeholder'] = False
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


@pytest.fixture(autouse=True)
def _clear_seed_tx_requirements(app, seed):
    """Accept/CTC tests leave requirements on session-scoped seed tx_a."""
    with app.app_context():
        _cleanup(seed['org_a'], seed['tx_a'])
        _cleanup(seed['org_b'], seed['tx_b'])
    yield


class TestAttachDocument:
    def test_attach_creates_one_row_idempotent(self, app, seed):
        req_id = doc_id = None
        with app.app_context():
            try:
                req = _make_requirement(seed['org_a'], seed['tx_a'], key='survey')
                doc = _make_document(
                    seed['org_a'], seed['tx_a'], slug='survey', placeholder=False,
                )
                db.session.commit()
                req_id, doc_id = req.id, doc.id

                first = attach_document(req, doc, actor_id=seed['owner_a'])
                db.session.commit()
                second = attach_document(req, doc, actor_id=seed['owner_a'])
                db.session.commit()

                assert first.id == second.id
                count = TransactionRequirementEvidence.query.filter_by(
                    organization_id=seed['org_a'],
                    requirement_id=req_id,
                    document_id=doc_id,
                ).count()
                assert count == 1
            finally:
                _cleanup(seed['org_a'], seed['tx_a'], [req_id] if req_id else None, [doc_id] if doc_id else None)

    def test_attach_across_orgs_raises(self, app, seed):
        req_id = doc_id = None
        with app.app_context():
            try:
                req = _make_requirement(seed['org_a'], seed['tx_a'], key='survey')
                doc = _make_document(
                    seed['org_b'], seed['tx_b'], slug='survey', placeholder=False,
                )
                db.session.commit()
                req_id, doc_id = req.id, doc.id

                with pytest.raises(ValueError, match='organizations'):
                    attach_document(req, doc)
            finally:
                _cleanup(seed['org_a'], seed['tx_a'], [req_id] if req_id else None, None)
                _cleanup(seed['org_b'], seed['tx_b'], None, [doc_id] if doc_id else None)

    def test_attach_different_transaction_raises(self, app, seed):
        from models import Transaction, TransactionType

        req_id = doc_id = tx2_id = None
        with app.app_context():
            try:
                tx_type = TransactionType.query.filter_by(
                    organization_id=seed['org_a'], name='buyer',
                ).first()
                tx2 = Transaction(
                    organization_id=seed['org_a'],
                    created_by_id=seed['owner_a'],
                    transaction_type_id=tx_type.id,
                    street_address='999 Other St',
                    city='Austin',
                    state='TX',
                    status='under_contract',
                )
                db.session.add(tx2)
                db.session.flush()
                tx2_id = tx2.id

                req = _make_requirement(seed['org_a'], seed['tx_a'], key='survey')
                doc = _make_document(
                    seed['org_a'], tx2_id, slug='survey', placeholder=False,
                )
                db.session.commit()
                req_id, doc_id = req.id, doc.id

                with pytest.raises(ValueError, match='transactions'):
                    attach_document(req, doc)
            finally:
                _cleanup(seed['org_a'], seed['tx_a'], [req_id] if req_id else None, None)
                if tx2_id:
                    _cleanup(seed['org_a'], tx2_id, None, [doc_id] if doc_id else None)
                    Transaction.query.filter_by(id=tx2_id).delete(
                        synchronize_session=False,
                    )
                    db.session.commit()


class TestAutoAttach:
    def test_auto_attach_matches_pack_slug(self, app, seed):
        req_match_id = req_other_id = doc_id = None
        with app.app_context():
            try:
                req_match = _make_requirement(
                    seed['org_a'], seed['tx_a'],
                    key='survey', package_key='buyer_ctc',
                )
                req_other = _make_requirement(
                    seed['org_a'], seed['tx_a'],
                    key='closing', package_key='buyer_ctc',
                    phase_key='closing', title='Closing',
                )
                doc = _make_document(
                    seed['org_a'], seed['tx_a'],
                    slug='survey', placeholder=False,
                )
                db.session.commit()
                req_match_id, req_other_id, doc_id = (
                    req_match.id, req_other.id, doc.id,
                )

                touched = auto_attach_for_document(doc, actor_id=seed['owner_a'])
                db.session.commit()

                assert [r.id for r in touched] == [req_match_id]
                assert TransactionRequirementEvidence.query.filter_by(
                    requirement_id=req_match_id, document_id=doc_id,
                ).count() == 1
                assert TransactionRequirementEvidence.query.filter_by(
                    requirement_id=req_other_id,
                ).count() == 0
            finally:
                ids = [i for i in (req_match_id, req_other_id) if i]
                _cleanup(seed['org_a'], seed['tx_a'], ids, [doc_id] if doc_id else None)

    def test_auto_attach_never_completes_requirement(self, app, seed):
        req_id = doc_id = None
        with app.app_context():
            try:
                req = _make_requirement(
                    seed['org_a'], seed['tx_a'],
                    key='survey', package_key='buyer_ctc',
                    work_status='pending',
                )
                doc = _make_document(
                    seed['org_a'], seed['tx_a'],
                    slug='survey', placeholder=False,
                )
                db.session.commit()
                req_id, doc_id = req.id, doc.id

                auto_attach_for_document(doc, actor_id=seed['owner_a'])
                db.session.commit()

                refreshed = TransactionRequirement.query.get(req_id)
                assert refreshed.work_status != 'completed'
                assert refreshed.work_status == 'in_progress'
            finally:
                _cleanup(
                    seed['org_a'], seed['tx_a'],
                    [req_id] if req_id else None,
                    [doc_id] if doc_id else None,
                )


class TestDetachDocument:
    def test_detach_removes_row_and_false_when_missing(self, app, seed):
        req_id = doc_id = None
        with app.app_context():
            try:
                req = _make_requirement(seed['org_a'], seed['tx_a'], key='survey')
                doc = _make_document(
                    seed['org_a'], seed['tx_a'], slug='survey', placeholder=False,
                )
                db.session.commit()
                req_id, doc_id = req.id, doc.id

                attach_document(req, doc)
                db.session.commit()
                assert detach_document(req, doc) is True
                db.session.commit()
                assert TransactionRequirementEvidence.query.filter_by(
                    requirement_id=req_id, document_id=doc_id,
                ).count() == 0
                assert detach_document(req, doc) is False
            finally:
                _cleanup(
                    seed['org_a'], seed['tx_a'],
                    [req_id] if req_id else None,
                    [doc_id] if doc_id else None,
                )


class TestPackDocumentSlugs:
    def test_buyer_pack_slugs_parse(self):
        DeadlineRulesService.clear_cache()
        pack = DeadlineRulesService.load_pack('buyer_ctc', 'v1')
        slugs = DeadlineRulesService.document_slugs_for_pack(pack)
        assert slugs['survey'] == 'survey'
        assert slugs['title_commitment'] == 'title-commitment'
        assert slugs['inspection'] == 'inspection-report'
        assert slugs['earnest_money'] == 'earnest-option-receipt'
        assert DeadlineRulesService.expected_document_slug(
            pack, 'option_period_end',
        ) is None
        assert DeadlineRulesService.expected_document_slug(
            pack, 'survey',
        ) == 'survey'
