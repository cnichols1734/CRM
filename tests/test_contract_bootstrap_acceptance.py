"""§34A Contract bootstrap acceptance (CB3, CB4, CB10)."""

from unittest.mock import patch

from models import (
    ContractBootstrapSession,
    Transaction,
    TransactionParticipant,
    TransactionRequirement,
    User,
    db,
)
from services.contract_bootstrap import (
    approve_selected,
    build_review_payload,
    classify_and_extract,
    find_transaction_matches,
    read_bootstrap_file,
    record_upload_metadata,
    run_match_discovery,
    store_bootstrap_file,
)


def _user(seed):
    return User.query.get(seed['owner_a'])


def _make_tx(org_id, user_id, tx_type_id, address, **kwargs):
    tx = Transaction(
        organization_id=org_id,
        created_by_id=user_id,
        transaction_type_id=tx_type_id,
        street_address=address,
        city=kwargs.get('city', 'Austin'),
        state=kwargs.get('state', 'TX'),
        status=kwargs.get('status', 'under_contract'),
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def _bootstrap_session(user, org_id, filename='contract.pdf'):
    return record_upload_metadata(
        file_bytes=b'%PDF-1.4 bootstrap-test',
        filename=filename,
        mime_type='application/pdf',
        source='inbox',
        user=user,
        org_id=org_id,
    )


def test_cb3_ambiguous_address_match_requires_agent_selection(app, seed):
    """
    CB3: Given two authorized txs match the same address,
    When bootstrap match discovery completes,
    Then match_status is ambiguous and nothing is silently attached/created.
    """
    with app.app_context():
        org_id = seed['org_a']
        user = _user(seed)
        address = '6004 Lakeside Drive'

        tx1 = _make_tx(org_id, user.id, seed['tx_type_a'], address)
        tx2 = _make_tx(org_id, user.id, seed['tx_type_a'], '6004 Lakeside Dr')

        before_tx_count = Transaction.query.filter_by(organization_id=org_id).count()

        session = _bootstrap_session(user, org_id)
        classify_and_extract(
            session=session,
            field_data={
                'property_address': address,
                'document_type': 'residential_contract',
                'side': 'seller',
                'closing_date': '2026-09-15',
            },
        )
        run_match_discovery(session)
        db.session.flush()

        assert session.match_status == ContractBootstrapSession.MATCH_AMBIGUOUS
        assert session.matched_transaction_id is None
        assert session.status == ContractBootstrapSession.STATUS_AWAITING_MATCH
        assert len(session.match_candidates or []) >= 2
        candidate_ids = {m['transaction_id'] for m in session.match_candidates}
        assert tx1.id in candidate_ids
        assert tx2.id in candidate_ids
        assert Transaction.query.filter_by(organization_id=org_id).count() == before_tx_count


def test_cb4_wrong_org_transaction_never_returned(app, seed):
    """
    CB4: Given a matching address exists only in another organization,
    When find_transaction_matches runs for this org,
    Then zero rows are returned (wrong-org match rejected).
    """
    with app.app_context():
        address = '999 Cross Org Lane'
        # Seed has org-B tx_b but does not expose tx_type_b; reuse its type.
        tx_b = Transaction.query.get(seed['tx_b'])
        _make_tx(
            seed['org_b'],
            seed['owner_b'],
            tx_b.transaction_type_id,
            address,
        )
        matches = find_transaction_matches(
            org_id=seed['org_a'],
            address=address,
        )
        assert matches == []

        # Sanity: same address is findable inside the owning org
        own = find_transaction_matches(org_id=seed['org_b'], address=address)
        assert len(own) >= 1
        assert all(m['transaction_id'] for m in own)


def test_cb10_classify_extract_does_not_mutate_transaction_before_approve(app, seed):
    """
    CB10: Given extraction finished and the agent has not approved,
    When classify_and_extract (+ match discovery) runs,
    Then Transaction / participants / requirements are unchanged.
    """
    with app.app_context():
        org_id = seed['org_a']
        user = _user(seed)
        tx = Transaction.query.get(seed['tx_a'])
        assert tx is not None

        snap = {
            'street_address': tx.street_address,
            'status': tx.status,
            'expected_close_date': tx.expected_close_date,
            'extra_data': dict(tx.extra_data or {}),
            'participants': TransactionParticipant.query.filter_by(
                transaction_id=tx.id,
            ).count(),
            'requirements': TransactionRequirement.query.filter_by(
                transaction_id=tx.id,
            ).count(),
            'tx_count': Transaction.query.filter_by(organization_id=org_id).count(),
        }

        session = _bootstrap_session(user, org_id, filename='pre-approve.pdf')
        classify_and_extract(
            session=session,
            field_data={
                'property_address': tx.street_address,
                'document_type': 'residential_contract',
                'side': 'seller',
                'buyer_name': 'Should Not Persist Buyer',
                'seller_name': 'Should Not Persist Seller',
                'sales_price': '750000',
                'closing_date': '2026-12-01',
                'effective_date': '2026-08-01',
            },
        )
        run_match_discovery(session)
        db.session.flush()

        db.session.refresh(tx)
        assert tx.street_address == snap['street_address']
        assert tx.status == snap['status']
        assert tx.expected_close_date == snap['expected_close_date']
        assert dict(tx.extra_data or {}) == snap['extra_data']
        assert TransactionParticipant.query.filter_by(
            transaction_id=tx.id,
        ).count() == snap['participants']
        assert TransactionRequirement.query.filter_by(
            transaction_id=tx.id,
        ).count() == snap['requirements']
        assert Transaction.query.filter_by(
            organization_id=org_id,
        ).count() == snap['tx_count']

        # Session itself may hold candidates — that is expected pre-approve state
        assert session.extracted_candidates
        assert session.status == ContractBootstrapSession.STATUS_AWAITING_MATCH
        assert session.status != ContractBootstrapSession.STATUS_APPLIED


def test_human_confirmed_side_wins_over_ai_hint(app, seed):
    """The model may provide a hint, but it cannot replace the agent's answer."""
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], filename='buyer-contract.pdf')
        session.classification = {
            'side': 'buyer',
            'side_confirmed_by_user': True,
        }

        classify_and_extract(
            session=session,
            field_data={
                'side': 'seller',
                'property_address': '501 Human Choice Way',
                'document_type': 'residential_contract',
            },
        )

        assert session.classification['side'] == 'buyer'
        assert session.classification['side_confirmed_by_user'] is True
        assert session.classification['extracted_side_hint'] == 'seller'
        assert 'side' not in (session.extracted_candidates or {})


def test_review_payload_unchecks_low_confidence_fields(app, seed):
    """Low-confidence observations require an explicit opt-in in Review and Apply."""
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], filename='unclear-contract.pdf')
        session.classification = {
            'side': 'buyer',
            'side_confirmed_by_user': True,
            'property_address': '502 Review Lane',
        }
        session.extracted_candidates = {
            'property_address': {
                'value': '502 Review Lane',
                'evidence': {'page': 2, 'confidence': 0.99},
            },
            'closing_date': {
                'value': '2026-10-01',
                'evidence': {'page': 9, 'confidence': 0.51},
            },
        }

        review = build_review_payload(session=session)
        by_key = {field['key']: field for field in review['fields']}

        assert by_key['property_address']['selected'] is True
        assert by_key['property_address']['label'] == 'Property address'
        assert by_key['closing_date']['selected'] is False
        assert by_key['closing_date']['needs_confirmation'] is True
        assert by_key['closing_date']['page'] == 9


def test_review_payload_requires_manual_address_when_extraction_misses_it(app, seed):
    """BOB presents a correction row instead of creating an unnamed transaction."""
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], filename='no-address.pdf')
        session.classification = {
            'side': 'buyer',
            'side_confirmed_by_user': True,
        }
        session.extracted_candidates = {
            'closing_date': {
                'value': '2026-10-01',
                'evidence': {'page': 9, 'confidence': 0.98},
            },
        }

        review = build_review_payload(session=session)
        address = next(
            field for field in review['fields']
            if field['key'] == 'property_address'
        )

        assert address['extracted_value'] == ''
        assert address['selected'] is False
        assert address['warning'] == 'Enter the property address.'


def test_review_payload_uses_extracted_party_names(app, seed):
    """Party review remains populated even for older sessions missing copied classification."""
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], filename='party-names.pdf')
        session.classification = {
            'side': 'buyer',
            'side_confirmed_by_user': True,
            'property_address': '503 Party Review Lane',
        }
        session.extracted_candidates = {
            'property_address': {
                'value': '503 Party Review Lane',
                'evidence': {'page': 1, 'confidence': 0.99},
            },
            'buyer_name': {
                'value': 'Jamie Buyer',
                'evidence': {'page': 1, 'confidence': 0.97},
            },
            'seller_name': {
                'value': 'Morgan Seller',
                'evidence': {'page': 1, 'confidence': 0.97},
            },
        }

        review = build_review_payload(session=session)

        assert {(party['role'], party['full_name']) for party in review['parties']} == {
            ('buyer', 'Jamie Buyer'),
            ('seller', 'Morgan Seller'),
        }


def test_new_transaction_rejects_missing_address_and_unknown_fields(app, seed):
    """Client-submitted fields cannot bypass the reviewed address requirement."""
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], filename='unsafe-fields.pdf')
        session.classification = {
            'side': 'buyer',
            'side_confirmed_by_user': True,
        }
        session.extracted_candidates = {}
        session.match_status = ContractBootstrapSession.MATCH_CREATE_NEW
        session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW
        before_count = Transaction.query.filter_by(
            organization_id=seed['org_a'],
        ).count()

        try:
            approve_selected(
                session=session,
                user_id=user.id,
                selected_fields={'untrusted_model_instruction': True},
                corrections={'untrusted_model_instruction': 'create anyway'},
                confirmed_side='buyer',
                party_resolutions=[],
            )
            assert False, 'approve_selected should require a reviewed address'
        except ValueError as exc:
            assert 'property address' in str(exc).lower()

        assert Transaction.query.filter_by(
            organization_id=seed['org_a'],
        ).count() == before_count
        db.session.rollback()


def test_approved_buyer_contract_creates_buyer_transaction_and_deadlines(app, seed):
    """The happy path creates a buyer transaction and uses the buyer deadline pack."""
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], filename='buyer-executed.pdf')
        session.classification = {
            'side': 'buyer',
            'side_confirmed_by_user': True,
            'document_type': 'residential_contract',
        }
        session.extracted_candidates = {
            'property_address': {
                'value': '812 Agent Review Lane',
                'evidence': {'page': 1, 'confidence': 0.99},
            },
            'effective_date': {
                'value': '2026-08-04',
                'evidence': {'page': 10, 'confidence': 0.97},
            },
            'closing_date': {
                'value': '2026-09-04',
                'evidence': {'page': 9, 'confidence': 0.98},
            },
            'option_period_days': {
                'value': 7,
                'evidence': {'page': 8, 'confidence': 0.96},
            },
        }
        session.match_status = ContractBootstrapSession.MATCH_CREATE_NEW
        session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW

        selected = {key: True for key in session.extracted_candidates}
        with patch(
            'services.contract_bootstrap._link_document_to_transaction',
            return_value=None,
        ):
            transaction, _proposal = approve_selected(
                session=session,
                user_id=user.id,
                selected_fields=selected,
                corrections={},
                confirmed_side='buyer',
                party_resolutions=[],
            )

        assert transaction.street_address == '812 Agent Review Lane'
        assert transaction.transaction_type.name == 'buyer'
        requirements = TransactionRequirement.query.filter_by(
            transaction_id=transaction.id,
            organization_id=seed['org_a'],
        ).all()
        assert requirements
        assert {row.package_key for row in requirements} == {'buyer_ctc'}
        assert session.status == ContractBootstrapSession.STATUS_APPLIED

        db.session.rollback()


def test_bootstrap_store_prefers_supabase(app, seed):
    """store_bootstrap_file uploads to TRANSACTION_DOCUMENTS_BUCKET when configured."""
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'])
        payload = b'%PDF-1.4 durable-bootstrap'

        with patch(
            'services.supabase_storage.upload_file',
            return_value={'path': 'x', 'filename': 'y', 'size': len(payload)},
        ) as mock_upload:
            path = store_bootstrap_file(session=session, file_bytes=payload)

        expected = (
            f'bootstrap/{seed["org_a"]}/{session.id}/contract.pdf'
        )
        assert path == expected
        assert session.storage_path == expected
        assert (session.classification or {}).get('storage_backend') == 'supabase'
        mock_upload.assert_called_once()
        args = mock_upload.call_args.args
        assert args[0] == 'transaction-documents'
        assert args[1] == expected
        assert args[2] == payload


def test_bootstrap_store_falls_back_local_when_supabase_missing(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], filename='local.pdf')
        payload = b'%PDF-1.4 local-fallback'

        with patch(
            'services.supabase_storage.upload_file',
            side_effect=ValueError('SUPABASE_URL and SUPABASE_KEY required'),
        ):
            path = store_bootstrap_file(session=session, file_bytes=payload)

        assert path.startswith('instance/bootstrap/')
        assert session.storage_path == path
        assert (session.classification or {}).get('storage_backend') == 'local'
        assert read_bootstrap_file(session) == payload


def test_bootstrap_read_uses_supabase_for_remote_path(app, seed):
    with app.app_context():
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'])
        session.storage_path = f'bootstrap/{seed["org_a"]}/{session.id}/remote.pdf'
        db.session.flush()
        remote_bytes = b'%PDF-1.4 from-supabase'

        with patch(
            'services.supabase_storage.download_file',
            return_value=remote_bytes,
        ) as mock_download:
            data = read_bootstrap_file(session)

        assert data == remote_bytes
        mock_download.assert_called_once()
        assert mock_download.call_args.args[0] == 'transaction-documents'
        assert mock_download.call_args.args[1] == session.storage_path
