"""Render/route tests for document-first intake + package workspace UI."""

from datetime import date, datetime, timedelta
from io import BytesIO
from unittest.mock import patch

from models import (
    ContractBootstrapSession,
    Organization,
    SellerAcceptedContract,
    Transaction,
    TransactionDocument,
    TransactionRequirement,
    User,
    db,
)
from services.contract_bootstrap import classify_and_extract, record_upload_metadata
from services.document_identity import (
    EXEC_EXECUTED,
    EXEC_UNKNOWN,
    KIND_LISTING_AGREEMENT,
    KIND_PURCHASE_CONTRACT,
    DocumentIdentity,
)


def _enable_vtc(seed):
    org = db.session.get(Organization, seed['org_a'])
    flags = dict(org.feature_flags or {})
    flags['BOB_VTC_PILOT'] = True
    org.feature_flags = flags
    db.session.commit()


def _user(seed):
    return db.session.get(User, seed['owner_a'])


def _bootstrap_session(user, org_id, filename='doc.pdf'):
    return record_upload_metadata(
        file_bytes=b'%PDF-1.4 document-intake-ui',
        filename=filename,
        mime_type='application/pdf',
        source='inbox',
        user=user,
        org_id=org_id,
    )


def _prepare_review_session(session, *, side, identity, fields, destination_choice=None):
    session.classification = {
        'side': side,
        'side_confirmed_by_user': True,
        'document_identity': identity.to_dict(),
    }
    if destination_choice:
        session.classification['destination_choice'] = destination_choice
    classify_and_extract(
        session=session,
        field_data=fields,
        identity=identity,
    )
    session.status = ContractBootstrapSession.STATUS_AWAITING_REVIEW
    session.match_status = ContractBootstrapSession.MATCH_CREATE_NEW
    db.session.commit()


def test_list_and_inbox_use_document_first_copy(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)

    list_resp = owner_a_client.get('/transactions/')
    assert list_resp.status_code == 200
    list_html = list_resp.get_data(as_text=True)
    assert 'Start from a document' in list_html
    assert 'Create manually' in list_html
    assert 'executed contract' not in list_html.lower()

    inbox_resp = owner_a_client.get('/transactions/bootstrap/inbox')
    assert inbox_resp.status_code == 200
    inbox_html = inbox_resp.get_data(as_text=True)
    assert 'Start from a document' in inbox_html
    assert 'name="files"' in inbox_html
    assert 'multiple' in inbox_html
    assert 'Drop PDFs here' in inbox_html
    assert 'What happens next' in inbox_html
    assert 'bg-[color:var(--paper-2)]' in inbox_html
    assert 'aria-label="What happens next"' in inbox_html
    assert 'listing agreement' in inbox_html.lower()
    assert 'hoa' in inbox_html.lower() or 'disclosure' in inbox_html.lower()
    assert 'executed contract intake' not in inbox_html.lower()
    assert 'Who do you represent?' not in inbox_html


def test_single_pdf_wait_page_uses_batch_card(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], 'listing.pdf')
        session.status = ContractBootstrapSession.STATUS_PROCESSING
        session.classification = {
            **(session.classification or {}),
            'upload_batch_id': 'batch-single-1',
        }
        db.session.commit()

    response = owner_a_client.get('/transactions/bootstrap/batch/batch-single-1')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Reading this PDF' in html
    assert 'Extracting details.' in html
    assert 'listing.pdf' in html
    assert 'Working through' not in html
    assert 'Identifying the form and extracting fields' not in html
    assert 'Nothing changes yet' not in html
    assert 'You can leave this page' not in html


def test_processing_review_redirects_to_batch_when_upload_has_batch_id(
    app, seed, owner_a_client,
):
    with app.app_context():
        _enable_vtc(seed)
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], 'listing.pdf')
        session.status = ContractBootstrapSession.STATUS_PROCESSING
        session.classification = {
            **(session.classification or {}),
            'upload_batch_id': 'batch-single-1',
        }
        db.session.commit()
        session_id = session.id

    response = owner_a_client.get(f'/transactions/bootstrap/{session_id}/review')
    assert response.status_code == 302
    assert '/transactions/bootstrap/batch/batch-single-1' in response.headers['Location']


def test_processing_review_without_batch_shows_compact_wait(
    app, seed, owner_a_client,
):
    with app.app_context():
        _enable_vtc(seed)
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], 'listing.pdf')
        session.status = ContractBootstrapSession.STATUS_PROCESSING
        db.session.commit()
        session_id = session.id

    response = owner_a_client.get(f'/transactions/bootstrap/{session_id}/review')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Reading this PDF' in html
    assert 'Extracting details.' in html
    assert 'listing.pdf' in html
    assert 'Identifying the form and extracting fields' not in html
    assert 'Nothing changes yet' not in html
    assert 'Checking for conflicts' not in html


def test_seller_listing_review_shows_create_seller_listing_cta(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], 'listing.pdf')
        identity = DocumentIdentity(
            kind=KIND_LISTING_AGREEMENT,
            template_slug='listing-agreement',
            form_number='TXR-1101',
            label='Residential Real Estate Listing Agreement',
            confidence=0.94,
            execution_state=EXEC_EXECUTED,
            possible_scopes=('listing',),
        )
        _prepare_review_session(
            session,
            side='seller',
            identity=identity,
            fields={
                'property_address': '1101 Listing Ct',
                'list_price': '525000',
            },
        )
        session_id = session.id

    response = owner_a_client.get(f'/transactions/bootstrap/{session_id}/review')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Identified as' in html
    assert 'TXR-1101' in html
    assert 'Create seller listing' in html
    assert 'js-review-side crm-segment__item' in html
    assert 'border-orange-500 bg-orange-50' not in html
    assert 'High confidence' in html or 'Needs confirmation' in html
    assert 'Contract dates and money' not in html
    assert 'Listing dates and terms' in html
    assert 'Found in uploaded contract' not in html
    assert 'Found in uploaded document' in html or 'Listing summary' in html


def test_seller_purchase_review_shows_incoming_offer_plan(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], 'offer.pdf')
        identity = DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug='one-to-four-family-contract',
            form_number='20-18',
            label='One to Four Family Residential Contract',
            confidence=0.9,
            execution_state=EXEC_UNKNOWN,
            possible_scopes=('offer', 'contract'),
        )
        _prepare_review_session(
            session,
            side='seller',
            identity=identity,
            fields={
                'property_address': '220 Offer Ave',
                'sales_price': '410000',
            },
            destination_choice='offer_thread',
        )
        session_id = session.id

    response = owner_a_client.get(f'/transactions/bootstrap/{session_id}/review')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Identified as' in html
    assert (
        'Add incoming offer' in html
        or 'incoming offer' in html.lower()
        or 'Filing plan' in html
    )
    # Destination radios appear when execution is ambiguous; filing plan always names the path.
    assert (
        'Offer still being negotiated' in html
        or 'Fully executed controlling contract' in html
        or 'Add incoming offer to seller listing' in html
        or 'Add incoming offer' in html
    )


def test_buyer_unknown_execution_requires_destination_radio(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], 'buyer-offer.pdf')
        identity = DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug='one-to-four-family-contract',
            confidence=0.88,
            execution_state=EXEC_UNKNOWN,
            possible_scopes=('offer', 'contract'),
        )
        _prepare_review_session(
            session,
            side='buyer',
            identity=identity,
            fields={
                'property_address': '330 Buyer Blvd',
                'sales_price': '390000',
            },
        )
        session_id = session.id

    response = owner_a_client.get(f'/transactions/bootstrap/{session_id}/review')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="destination_choice"' in html
    assert 'Offer still being negotiated' in html
    assert 'Fully executed controlling contract' in html
    assert 'disabled' in html  # approve disabled until destination chosen


def test_destination_choice_posted_and_redirects_to_offers(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        user = _user(seed)
        session = _bootstrap_session(user, seed['org_a'], 'buyer-dest.pdf')
        identity = DocumentIdentity(
            kind=KIND_PURCHASE_CONTRACT,
            template_slug='one-to-four-family-contract',
            confidence=0.9,
            execution_state=EXEC_UNKNOWN,
            possible_scopes=('offer', 'contract'),
        )
        _prepare_review_session(
            session,
            side='buyer',
            identity=identity,
            fields={
                'property_address': '440 Redirect Rd',
                'sales_price': '400000',
                'effective_date': '2026-08-01',
            },
        )
        session_id = session.id

    with patch(
        'services.contract_bootstrap.read_bootstrap_file',
        return_value=b'%PDF-1.4 redirect',
    ), patch(
        'services.supabase_storage.upload_external_document',
        return_value={'path': 'test/redirect.pdf'},
    ):
        response = owner_a_client.post(
            f'/transactions/bootstrap/{session_id}/approve',
            json={
                'side': 'buyer',
                'destination_choice': 'offer_thread',
                'selected': {
                    'property_address': True,
                    'sales_price': True,
                    'effective_date': True,
                },
                'corrections': {},
            },
        )
    assert response.status_code == 200
    data = response.get_json()
    assert data['ok'] is True
    assert data['next_url']
    assert '#transaction-offers' in data['next_url'] or '#offer-' in data['next_url']
    assert 'intake' not in (data['next_url'] or '')


def test_seller_detail_lists_offers_contract_tabs_always(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='501 Discover Tabs St',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
        ))
        db.session.commit()
        tx_id = tx.id

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'id="seller-tab-listing"' in html
        assert 'id="seller-tab-offers"' in html
        assert 'id="seller-tab-contract"' in html
        assert 'id="seller-panel-contract"' in html
        assert 'Contract workspace' in html or 'No controlling contract yet' in html
    finally:
        with app.app_context():
            TransactionRequirement.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            TransactionDocument.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
            db.session.commit()


def test_buyer_detail_has_no_listing_package_wording(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a2'],
            street_address='55 Buyer Package Ln',
            city='Austin',
            state='TX',
            status='showing',
        )
        db.session.add(tx)
        db.session.commit()
        tx_id = tx.id

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Listing package' not in html
        assert 'id="seller-workspace"' not in html
        assert 'Listing Documents' not in html
        assert 'id="buyer-document-packages"' in html
        assert 'id="buyer-contract-packages"' in html
        assert 'href="#transaction-offers"' in html
        assert 'href="#buyer-contract-packages"' in html
        assert html.count('data-controller="transaction-live document-upload-hub"') == 1
        assert html.count('id="tx-document-upload-hub-title"') == 1
        assert 'This list keeps legacy actions' not in html
    finally:
        with app.app_context():
            TransactionRequirement.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
            db.session.commit()


def test_seller_detail_has_one_upload_hub(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='502 Single Hub St',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
        ))
        db.session.commit()
        tx_id = tx.id

    try:
        html = owner_a_client.get(f'/transactions/{tx_id}').get_data(as_text=True)
        assert html.count('data-controller="transaction-live document-upload-hub"') == 1
        assert html.count('id="tx-document-upload-hub-title"') == 1
        assert 'href="#seller-workspace"' in html
        assert 'This list keeps legacy actions' not in html
    finally:
        with app.app_context():
            TransactionRequirement.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            TransactionDocument.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
            db.session.commit()


def test_listing_cta_uses_canonical_listing_scope_and_slug(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='777 Missing Listing Rd',
            city='Austin',
            state='TX',
            status='preparing_to_list',
        )
        db.session.add(tx)
        db.session.commit()
        tx_id = tx.id

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'data-listing-cta-scope="listing"' in html
        assert 'data-listing-cta-slug="listing-agreement"' in html
        assert 'Upload listing agreement' in html
    finally:
        with app.app_context():
            Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
            db.session.commit()


def test_package_states_include_detected_and_unfiled(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='888 Package States Ave',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        listing = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
            signed_file_path='org/tx/listing-package.pdf',
            field_data={
                # AI package map is the source of truth for "Detected in package".
                'detected_documents': [
                    {
                        'document_type': 'listing_agreement',
                        'start_page': 1,
                        'end_page': 10,
                    },
                    {
                        'document_type': 'sellers_disclosure',
                        'start_page': 11,
                        'end_page': 16,
                    },
                ],
                '_document_identity': {
                    'kind': 'listing_agreement',
                    'template_slug': 'listing-agreement',
                    'confidence': 0.95,
                    'extras': {
                        'package_authority': 'ai_detected_documents',
                        'embedded_components': [
                            {
                                'template_slug': 'sellers-disclosure',
                                'label': "Seller's Disclosure",
                                'source': 'ai_detected_documents',
                            },
                        ],
                    },
                },
            },
        )
        unfiled = TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='completed',
            template_name='Uploaded PDF',
            status='signed',
            signed_file_path='org/tx/unfiled.pdf',
            field_data={
                '_document_identity': {
                    'kind': 'unknown',
                    'template_slug': 'completed',
                    'confidence': 0.2,
                },
            },
        )
        db.session.add_all([listing, unfiled])
        db.session.commit()
        tx_id = tx.id
        unfiled_id = unfiled.id

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Needs filing' in html
        assert 'crm-document-package--unfiled' in html
        assert 'Detected in package' in html or 'not a separate uploaded file' in html
        # Needs filing primary action is File document (not blocked by the view branch).
        assert 'File document' in html
        assert f'/documents/{unfiled_id}/review' in html
        needs_idx = html.find('data-state="needs_classification"')
        assert needs_idx != -1
        assert html.find('File document', needs_idx) != -1
        open_pdf_idx = html.find('View document', needs_idx)
        file_idx = html.find('File document', needs_idx)
        if open_pdf_idx != -1:
            assert file_idx < open_pdf_idx
    finally:
        with app.app_context():
            TransactionDocument.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
            db.session.commit()


def test_classification_form_options_safe_defaults():
    from services.document_intake_ui import build_classification_form_options

    listing = build_classification_form_options(
        identity={
            'kind': 'listing_agreement',
            'template_slug': 'listing-agreement',
            'possible_scopes': ['listing'],
            'confidence': 0.9,
        },
        routing_context={'side': 'seller', 'has_primary_contract': False, 'active_offers': []},
    )
    assert listing['selected_slug'] == 'listing-agreement'
    assert listing['suggested_scope'] == 'listing'
    assert all(o['value'] != 'contract' for o in listing['scope_options'])

    seller_purchase = build_classification_form_options(
        identity={
            'kind': 'purchase_contract',
            'template_slug': 'one-to-four-family-contract',
            'possible_scopes': ['offer', 'contract'],
            'execution_state': 'unknown',
            'confidence': 0.9,
        },
        routing_context={'side': 'seller', 'has_primary_contract': False, 'active_offers': []},
    )
    assert seller_purchase['suggested_scope'] == 'offer'
    assert all(o['value'] != 'listing' for o in seller_purchase['scope_options'])

    buyer_unknown = build_classification_form_options(
        identity={
            'kind': 'purchase_contract',
            'template_slug': 'one-to-four-family-contract',
            'possible_scopes': ['offer', 'contract'],
            'execution_state': 'unknown',
            'confidence': 0.5,
        },
        routing_context={'side': 'buyer', 'has_primary_contract': False, 'active_offers': []},
    )
    assert buyer_unknown['require_destination_choice'] is True
    assert buyer_unknown['suggested_scope'] is None

    amendment_no_baseline = build_classification_form_options(
        identity={
            'kind': 'amendment',
            'template_slug': 'amendment',
            'possible_scopes': ['amendment'],
            'confidence': 0.9,
        },
        routing_context={'side': 'seller', 'has_primary_contract': False, 'active_offers': []},
    )
    assert all(o['value'] != 'amendment' for o in amendment_no_baseline['scope_options'])

    addendum = build_classification_form_options(
        identity={
            'kind': 'addendum',
            'template_slug': 'hoa-addendum',
            'possible_scopes': ['offer', 'contract', 'listing'],
            'confidence': 0.8,
        },
        routing_context={'side': 'seller', 'has_primary_contract': True, 'active_offers': [{'id': 1}]},
    )
    assert addendum['selected_slug'] == 'hoa-addendum'
    assert addendum['require_destination_choice'] is True


def test_amendment_cta_gated_on_controlling_contract(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        tx = Transaction(
            organization_id=seed['org_a'],
            created_by_id=seed['owner_a'],
            transaction_type_id=seed['tx_type_a'],
            street_address='999 Amendment Gate St',
            city='Austin',
            state='TX',
            status='active',
        )
        db.session.add(tx)
        db.session.flush()
        db.session.add(TransactionDocument(
            organization_id=seed['org_a'],
            transaction_id=tx.id,
            template_slug='listing-agreement',
            template_name='Listing Agreement',
            status='signed',
        ))
        db.session.commit()
        tx_id = tx.id

    try:
        before = owner_a_client.get(f'/transactions/{tx_id}')
        before_html = before.get_data(as_text=True)
        assert 'Upload amendment' not in before_html
        assert 'Amendments become available after a controlling contract' in before_html

        with app.app_context():
            db.session.add(SellerAcceptedContract(
                organization_id=seed['org_a'],
                transaction_id=tx_id,
                created_by_id=seed['owner_a'],
                position='primary',
                status='active',
                accepted_price=500000,
                closing_date=date.today() + timedelta(days=30),
            ))
            db.session.commit()

        after = owner_a_client.get(f'/transactions/{tx_id}')
        after_html = after.get_data(as_text=True)
        assert 'Upload amendment' in after_html
    finally:
        with app.app_context():
            SellerAcceptedContract.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            TransactionDocument.query.filter_by(transaction_id=tx_id).delete(
                synchronize_session=False,
            )
            Transaction.query.filter_by(id=tx_id).delete(synchronize_session=False)
            db.session.commit()


def test_classification_panel_choices_on_review_workspace(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        doc = db.session.get(TransactionDocument, seed['doc_a'])
        original = doc.field_data
        original_slug = doc.template_slug
        doc.template_slug = 'completed'
        doc.field_data = {
            '_document_identity': {
                'kind': 'listing_agreement',
                'template_slug': 'listing-agreement',
                'form_number': 'TXR-1101',
                'label': 'Residential Real Estate Listing Agreement',
                'confidence': 0.92,
                'possible_scopes': ['listing'],
            },
        }
        db.session.commit()
        doc_id = doc.id
        tx_id = seed['tx_a']

    try:
        response = owner_a_client.get(f'/transactions/{tx_id}/documents/{doc_id}/review')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Document identity' in html
        assert 'Residential Real Estate Listing Agreement' in html
        assert 'File this document' not in html
        assert 'Confirm filing' not in html
        assert 'Choose a destination' not in html
    finally:
        with app.app_context():
            doc = db.session.get(TransactionDocument, seed['doc_a'])
            doc.field_data = original
            doc.template_slug = original_slug
            db.session.commit()


def test_upload_endpoint_accepts_scoped_payload(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        tx_id = seed['tx_a']

    with patch(
        'services.supabase_storage.upload_external_document',
        return_value={'path': 'test/scoped-upload.pdf'},
    ), patch(
        'services.intake_service.post_upload_processing',
        return_value=None,
    ):
        response = owner_a_client.post(
            f'/transactions/{tx_id}/documents/upload-completed',
            data={
                'file': (BytesIO(b'%PDF-1.4 scoped'), 'scoped.pdf'),
                'scope': 'listing',
                'template_slug': 'listing-agreement',
                'document_name': 'Listing Agreement',
            },
            content_type='multipart/form-data',
            headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
        )
    assert response.status_code in (200, 201)
    data = response.get_json()
    assert data.get('success') is True
    assert data.get('scope') == 'listing'
    assert data.get('document_id')


def test_upload_endpoint_accepts_multiple_pdfs(app, seed, owner_a_client):
    with app.app_context():
        _enable_vtc(seed)
        tx_id = seed['tx_a']

    with patch(
        'services.supabase_storage.upload_external_document',
        side_effect=[
            {'path': 'test/listing.pdf'},
            {'path': 'test/hoa.pdf'},
            {'path': 'test/mud.pdf'},
        ],
    ), patch(
        'services.intake_service.post_upload_processing',
        return_value=None,
    ):
        response = owner_a_client.post(
            f'/transactions/{tx_id}/documents/upload-completed',
            data={
                'files': [
                    (BytesIO(b'%PDF-1.4 listing'), 'listing.pdf'),
                    (BytesIO(b'%PDF-1.4 hoa'), 'hoa.pdf'),
                    (BytesIO(b'%PDF-1.4 mud'), 'mud.pdf'),
                ],
                'scope': 'listing',
                'template_slug': 'hoa-addendum',
            },
            content_type='multipart/form-data',
            headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
        )
    assert response.status_code in (200, 201)
    data = response.get_json()
    assert data.get('success') is True
    assert data.get('uploaded_count') == 3
    assert len(data.get('document_ids') or []) == 3
    # Mixed pack: do not stamp every file with the same explicit type.
    with app.app_context():
        from models import TransactionDocument
        docs = TransactionDocument.query.filter(
            TransactionDocument.id.in_(data['document_ids']),
        ).all()
        assert len(docs) == 3
        assert all((d.template_slug or '') == 'completed' for d in docs)
