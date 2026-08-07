"""Phase 3 document privacy controls."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.document_privacy import (
    CONFIDENTIAL,
    INTERNAL,
    PUBLIC_OK,
    RESTRICTED_TENANT,
    apply_sensitivity_to_document,
    filter_telegram_safe_attachment_refs,
    infer_sensitivity_class,
    lease_bootstrap_allowed,
    may_send_to_telegram,
    may_use_in_llm,
    retention_until_for,
)


def test_infer_sensitivity_paystub_restricted():
    assert infer_sensitivity_class(
        document_type='pay_stub', template_slug='applicant-paystub',
    ) == RESTRICTED_TENANT
    assert infer_sensitivity_class(
        template_name='Driver License Scan',
    ) == RESTRICTED_TENANT


def test_infer_sensitivity_lease_confidential():
    assert infer_sensitivity_class(
        document_type='residential_lease',
        transaction_side='landlord',
    ) == CONFIDENTIAL
    assert infer_sensitivity_class(
        template_slug='lease-agreement',
    ) == CONFIDENTIAL


def test_infer_sensitivity_public_default():
    assert infer_sensitivity_class(
        template_slug='sellers-disclosure',
        template_name="Seller's Disclosure",
    ) == PUBLIC_OK


def test_may_send_to_telegram_bans_sensitive():
    assert may_send_to_telegram(SimpleNamespace(
        sensitivity_class=PUBLIC_OK, template_slug='hoa',
    )) is True
    assert may_send_to_telegram(SimpleNamespace(
        sensitivity_class=INTERNAL, template_slug='cda',
    )) is True
    assert may_send_to_telegram(SimpleNamespace(
        sensitivity_class=CONFIDENTIAL, template_slug='lease',
    )) is False
    assert may_send_to_telegram(SimpleNamespace(
        sensitivity_class=RESTRICTED_TENANT, template_slug='paystub',
    )) is False


def test_may_use_in_llm_restricted_requires_allowlist():
    doc = SimpleNamespace(
        sensitivity_class=RESTRICTED_TENANT,
        template_slug='paystub',
        document_type='pay_stub',
        ai_processing_allowed=True,
    )
    assert may_use_in_llm(doc) is False
    assert may_use_in_llm(doc, allowlist={'paystub'}) is True
    assert may_use_in_llm(doc, allowlist={'other'}) is False


def test_may_use_in_llm_respects_ai_processing_allowed():
    doc = SimpleNamespace(
        sensitivity_class=PUBLIC_OK,
        template_slug='contract',
        ai_processing_allowed=False,
    )
    assert may_use_in_llm(doc) is False


def test_apply_sensitivity_sets_retention_and_flags():
    doc = SimpleNamespace(
        template_slug='tenant-paystub',
        template_name='Pay Stub',
        sensitivity_class=None,
        retention_until=None,
        ai_processing_allowed=True,
    )
    cls = apply_sensitivity_to_document(doc, transaction_side='tenant')
    assert cls == RESTRICTED_TENANT
    assert doc.sensitivity_class == RESTRICTED_TENANT
    assert doc.ai_processing_allowed is False
    assert isinstance(doc.retention_until, datetime)
    assert doc.retention_until > datetime.utcnow()


def test_retention_until_for_restricted_shorter_than_public():
    public = retention_until_for(PUBLIC_OK, from_dt=datetime(2026, 1, 1))
    restricted = retention_until_for(RESTRICTED_TENANT, from_dt=datetime(2026, 1, 1))
    assert restricted < public
    assert restricted == datetime(2026, 1, 1) + timedelta(days=365)


def test_filter_telegram_safe_attachment_refs():
    docs = {
        1: SimpleNamespace(sensitivity_class=PUBLIC_OK, template_slug='hoa'),
        2: SimpleNamespace(
            sensitivity_class=RESTRICTED_TENANT, template_slug='id',
        ),
    }
    refs = [
        {'document_id': 1},
        {'document_id': 2},
        {'document_id': 99},  # missing → fail closed
    ]
    safe = filter_telegram_safe_attachment_refs(refs, documents_by_id=docs)
    assert safe == [{'document_id': 1}]


def test_lease_bootstrap_allowed_requires_privacy_flags():
    org = SimpleNamespace(id=1, feature_flags={})
    with patch('feature_flags.org_has_feature', return_value=False):
        ok, reason = lease_bootstrap_allowed(org=org, side='tenant')
        assert ok is False
        assert 'privacy' in reason

    def _flags(name, o=None):
        return name in ('BOB_VTC_PILOT', 'BOB_VTC_PRIVACY_CONTROLS')

    with patch('feature_flags.org_has_feature', side_effect=_flags):
        ok, reason = lease_bootstrap_allowed(org=org, side='tenant')
        assert ok is True

    ok, reason = lease_bootstrap_allowed(org=org, side='seller')
    assert ok is True
    assert reason == 'not_lease_side'


def test_document_review_skips_telegram_for_restricted(app, seed):
    """Sensitive docs create in-app notify but not Telegram delivery."""
    from models import (
        NotificationDelivery,
        NotificationEvent,
        Transaction,
        TransactionDocument,
        db,
    )
    from services.document_review import finalize_document_review

    with app.app_context():
        tx = Transaction.query.get(seed['tx_a'])
        doc = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='tenant-paystub',
            template_name='Pay Stub',
            status='signed',
            sensitivity_class=RESTRICTED_TENANT,
            ai_processing_allowed=False,
            field_data={'notes': 'redacted'},
        )
        db.session.add(doc)
        db.session.commit()

        with patch('services.document_review.create_notification', return_value=None), \
             patch('services.messaging.outbound.notify') as telegram_notify:
            report = finalize_document_review(
                document_id=doc.id,
                org_id=seed['org_a'],
                extraction_run_id=99001,
            )

        assert report is not None
        telegram_notify.assert_not_called()

        events = NotificationEvent.query.filter_by(
            organization_id=seed['org_a'],
        ).order_by(NotificationEvent.id.desc()).limit(10).all()
        matched = [
            e for e in events
            if (e.payload or {}).get('document_id') == doc.id
        ]
        assert matched
        # No telegram deliveries for this document's events
        for event in matched:
            methods = [
                d.delivery_method for d in NotificationDelivery.query.filter_by(
                    event_id=event.id,
                ).all()
            ]
            assert 'telegram' not in methods
            assert 'in_app' in methods
