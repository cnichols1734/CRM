"""Phase 2 Telegram PDF intake — requires selected transaction, no auto-apply."""

from models import ContractBootstrapSession, Transaction, db
from services.messaging.telegram_documents import process_telegram_pdf_for_transaction


def test_telegram_pdf_creates_awaiting_review_session(app, seed):
    with app.app_context():
        from models import User

        user = User.query.get(seed['owner_a'])
        tx = Transaction.query.get(seed['tx_a'])
        # Minimal PDF header bytes — extraction fails soft to {}.
        pdf_bytes = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'

        session = process_telegram_pdf_for_transaction(
            user=user,
            transaction=tx,
            file_bytes=pdf_bytes,
            filename='offer.pdf',
            mime_type='application/pdf',
            run_extraction=False,
        )

        assert session.id is not None
        assert session.upload_source == 'telegram'
        assert session.matched_transaction_id == tx.id
        assert session.status == ContractBootstrapSession.STATUS_AWAITING_REVIEW
        assert session.match_status == ContractBootstrapSession.MATCH_MATCHED
        # No silent CRM write: transaction terms unchanged; session not applied.
        assert session.applied_at is None
        assert session.status != ContractBootstrapSession.STATUS_APPLIED


def test_telegram_pdf_dedupes_by_hash(app, seed):
    with app.app_context():
        from models import User

        user = User.query.get(seed['owner_a'])
        tx = Transaction.query.get(seed['tx_a'])
        pdf_bytes = b'%PDF-1.4 unique-phase2-hash-test\n%%EOF\n'

        first = process_telegram_pdf_for_transaction(
            user=user,
            transaction=tx,
            file_bytes=pdf_bytes,
            filename='a.pdf',
            run_extraction=False,
        )
        second = process_telegram_pdf_for_transaction(
            user=user,
            transaction=tx,
            file_bytes=pdf_bytes,
            filename='a-again.pdf',
            run_extraction=False,
        )
        assert first.id == second.id


def test_client_safe_requirements_shared_for_buyer_context(app, seed):
    """Buyer portal reuses the same client_safe_requirement_summaries helper."""
    from models import TransactionRequirement
    from services.portal_service import client_safe_requirement_summaries

    created_ids = []
    with app.app_context():
        try:
            tx = Transaction.query.get(seed['tx_a'])
            req = TransactionRequirement(
                organization_id=seed['org_a'],
                transaction_id=tx.id,
                package_key='buyer_ctc',
                phase_key='option_period',
                requirement_key='option_fee',
                title='Option Fee Delivered',
                work_status='pending',
            )
            internal = TransactionRequirement(
                organization_id=seed['org_a'],
                transaction_id=tx.id,
                package_key='buyer_ctc',
                phase_key='closing',
                requirement_key='cda_internal',
                title='Internal CDA commission note',
                work_status='pending',
            )
            db.session.add_all([req, internal])
            db.session.commit()
            created_ids = [req.id, internal.id]

            rows = client_safe_requirement_summaries(tx)
            titles = {r['title'] for r in rows}
            assert 'Option Fee Delivered' in titles
            assert not any('commission' in t.lower() for t in titles)
        finally:
            if created_ids:
                TransactionRequirement.query.filter(
                    TransactionRequirement.id.in_(created_ids)
                ).delete(synchronize_session=False)
                db.session.commit()
