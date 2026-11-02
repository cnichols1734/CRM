"""Transaction parity through real BOB dispatch and the CRM application handlers."""
import pytest
from flask import g

from models import db, Transaction, TransactionType, Task, BobAction
from services.bob_tools import BobContext, dispatch, confirm_action, CONFIRM_PRECLEARED
from services.bob_tools.transaction_workflows import OPERATIONS, READ_SECTIONS


@pytest.fixture
def deal(app, seed):
    with app.app_context():
        ctx = BobContext(user_id=seed['owner_a'], organization_id=seed['org_a'], org_role='owner', is_org_admin=True)
        kind = TransactionType.query.filter_by(organization_id=seed['org_a'], name='seller').first()
        tx = Transaction(organization_id=seed['org_a'], created_by_id=seed['owner_a'],
            transaction_type_id=kind.id, street_address='901 Parity Lane', city='Houston',
            state='TX', status='preparing_to_list', intake_data={'has_hoa': False})
        db.session.add(tx)
        db.session.commit()
        tid = tx.id
        yield ctx, tid
        from services.transaction_helpers import purge_transaction_dependent_rows
        db.session.rollback()
        if db.session.get(Transaction, tid):
            # Delete linked tasks before the dependent records, preserving shared seed data.
            Task.query.filter_by(transaction_id=tid).delete(synchronize_session=False)
            purge_transaction_dependent_rows(tid)
            db.session.delete(db.session.get(Transaction, tid))
            db.session.commit()
        from services.cache_helpers import _cache
        _cache.clear()


def apply(name, args, ctx):
    result = dispatch(name, args, ctx, confirmation=CONFIRM_PRECLEARED)
    assert result.ok, result.error
    return result


def test_detail_change_requires_approval_and_preserves_other_fields(deal):
    ctx, tid = deal
    pending = dispatch('update_transaction_details', {'transaction_id': tid, 'fields': {'city': 'Austin'}}, ctx)
    assert pending.requires_confirmation, pending.error
    assert db.session.get(Transaction, tid).city == 'Houston'
    changes = pending.data['preview']['changes']
    assert {'field': 'city', 'from': 'Houston', 'to': 'Austin'} in changes
    result = confirm_action(pending.action_id, ctx)
    assert result.ok, result.error
    tx = db.session.get(Transaction, tid)
    assert (tx.city, tx.street_address, tx.state) == ('Austin', '901 Parity Lane', 'TX')
    assert not confirm_action(pending.action_id, ctx).ok


def test_status_uses_type_validation_and_seller_checkin(deal, seed):
    from models import TransactionParticipant
    ctx, tid = deal
    db.session.add(TransactionParticipant(organization_id=ctx.organization_id, transaction_id=tid,
        contact_id=seed['contact_a'], role='seller', is_primary=True))
    db.session.commit()
    denied = dispatch('update_transaction_status', {'transaction_id': tid, 'status': 'showing'}, ctx, confirmation=CONFIRM_PRECLEARED)
    assert not denied.ok
    assert db.session.get(Transaction, tid).status == 'preparing_to_list'
    apply('update_transaction_status', {'transaction_id': tid, 'status': 'active'}, ctx)
    assert Task.query.filter_by(transaction_id=tid).count() == 1


def test_price_change_history_preserves_reason(deal):
    from models import SellerListingPriceChange, SellerListingProfile
    ctx, tid = deal
    apply('update_listing_fields', {'transaction_id': tid, 'list_price': 400000}, ctx)
    apply('update_listing_fields', {'transaction_id': tid, 'list_price': 390000,
        'price_change_reason': 'Seller approved reduction', 'price_change_notes': 'Reviewed today'}, ctx)
    row = SellerListingPriceChange.query.filter_by(transaction_id=tid).order_by(SellerListingPriceChange.id.desc()).first()
    assert float(row.old_price) == 400000 and float(row.new_price) == 390000
    assert row.reason == 'Seller approved reduction' and row.notes == 'Reviewed today'
    assert float(SellerListingProfile.query.filter_by(transaction_id=tid).first().current_list_price) == 390000


def test_accept_offer_creates_contract_and_rejects_second_primary(deal):
    from models import SellerAcceptedContract, SellerOfferVersion, SellerOffer
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Test Buyer',
        'offer_price': 400000, 'proposed_close_date': '2026-12-01', 'option_period_days': 7}, ctx)
    oid = offer.data['offer_id']
    assert SellerOfferVersion.query.filter_by(offer_id=oid).count() == 1
    pending = dispatch('accept_offer', {'offer_id': oid, 'effective_date': '2026-11-01'}, ctx)
    assert pending.requires_confirmation, pending.error
    assert any(c['field'] == 'effective_date' and c['to'] == '2026-11-01' for c in pending.data['preview']['changes'])
    result = confirm_action(pending.action_id, ctx)
    assert result.ok, result.error
    contract = SellerAcceptedContract.query.filter_by(transaction_id=tid, offer_id=oid).one()
    assert contract.position == 'primary' and str(contract.effective_date) == '2026-11-01'
    assert float(contract.accepted_price) == 400000
    assert contract.milestones.count() > 0
    other = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Other Buyer', 'offer_price': 410000}, ctx)
    denied = dispatch('accept_offer', {'offer_id': other.data['offer_id']}, ctx, confirmation=CONFIRM_PRECLEARED)
    assert not denied.ok
    assert db.session.get(SellerOffer, other.data['offer_id']).status == 'new'


def test_next_step_reports_document_work(deal):
    from models import TransactionDocument, DocumentReviewReport
    ctx, tid = deal
    tx = db.session.get(Transaction, tid)
    tx.status = 'active'
    doc = TransactionDocument(organization_id=ctx.organization_id, transaction_id=tid,
        template_slug='survey', template_name='Survey', status='pending', is_placeholder=True)
    db.session.add(doc)
    db.session.commit()
    result = apply('get_next_step', {'transaction_id': tid}, ctx)
    assert result.data['next_step']['action'] == 'upload_documents'
    db.session.add(DocumentReviewReport(organization_id=ctx.organization_id, transaction_id=tid,
        document_id=doc.id, title='Review dates', summary='Check closing date', status='open', severity='attention'))
    db.session.commit()
    assert apply('get_next_step', {'transaction_id': tid}, ctx).data['next_step']['action'] == 'review_documents'


@pytest.mark.parametrize('section', list(READ_SECTIONS) + ['intake', 'document_templates', 'document_routing', 'history'])
def test_workspace_reads_existing_sections(deal, section):
    ctx, tid = deal
    result = apply('get_transaction_workspace', {'transaction_id': tid, 'section': section}, ctx)
    assert isinstance(result.data, dict)


def test_nested_identity_and_cross_transaction_document_rejected(deal, seed):
    ctx, tid = deal
    invalid = dispatch('update_transaction_details', {'transaction_id': tid,
        'fields': {'organization_id': seed['org_b']}}, ctx)
    assert not invalid.ok
    result = dispatch('manage_transaction_document', {'transaction_id': tid,
        'action': 'save_fields', 'document_id': seed['doc_a'], 'fields': {'field_data': {'price': '1'}}}, ctx)
    assert not result.ok
    foreign = BobContext(user_id=seed['owner_b'], organization_id=seed['org_b'], org_role='owner')
    assert not dispatch('get_transaction_workspace', {'transaction_id': tid, 'section': 'details'}, foreign).ok


def test_adapter_restores_outer_identity(deal, seed):
    ctx, tid = deal
    from models import User
    other = db.session.get(User, seed['owner_b'])
    g._login_user = other
    apply('update_transaction_details', {'transaction_id': tid, 'fields': {'city': 'Dallas'}}, ctx)
    assert g._login_user is other
    g.pop('_login_user', None)


def test_listing_partial_updates_and_child_stale_confirmation(deal):
    from models import SellerListingProfile
    ctx, tid = deal
    apply('update_listing_operations', {'transaction_id': tid,
        'fields': {'gate_code': '1234', 'pet_notes': 'Cat inside'}}, ctx)
    pending = dispatch('update_listing_operations', {'transaction_id': tid,
        'fields': {'pet_notes': 'Dog inside'}}, ctx)
    assert pending.requires_confirmation, pending.error
    row = SellerListingProfile.query.filter_by(transaction_id=tid).one()
    row.gate_code = '5678'
    db.session.commit()
    assert not confirm_action(pending.action_id, ctx).ok
    apply('update_listing_operations', {'transaction_id': tid, 'fields': {'pet_notes': 'Dog inside'}}, ctx)
    db.session.refresh(row)
    assert row.gate_code == '5678' and row.pet_notes == 'Dog inside'


def test_intake_document_and_requirement_changes(deal):
    from models import TransactionDocument, TransactionRequirement
    ctx, tid = deal
    apply('manage_transaction_intake', {'transaction_id': tid, 'action': 'save', 'fields': {'intake_data': {'year_built': '1990'}}}, ctx)
    assert db.session.get(Transaction, tid).intake_data == {'has_hoa': False, 'year_built': '1990'}
    added = apply('manage_transaction_document', {'transaction_id': tid, 'action': 'add_placeholder', 'fields': {'document_name': 'Inspection receipt'}}, ctx)
    doc = TransactionDocument.query.filter_by(transaction_id=tid, template_name='Inspection receipt').one()
    apply('manage_transaction_document', {'transaction_id': tid, 'document_id': doc.id, 'action': 'save_fields', 'fields': {'field_data': {'reference': '123'}}}, ctx)
    apply('manage_transaction_document', {'transaction_id': tid, 'document_id': doc.id, 'action': 'save_fields', 'fields': {'field_data': {'amount': '100'}}}, ctx)
    db.session.refresh(doc)
    assert doc.field_data['reference'] == '123' and doc.field_data['amount'] == '100'
    item = apply('manage_transaction_requirement', {'transaction_id': tid, 'action': 'create', 'fields': {'title': 'Order inspection', 'due_date': '2026-11-10'}}, ctx)
    rid = item.data['requirement_id']
    apply('manage_transaction_requirement', {'transaction_id': tid, 'action': 'set_due_date', 'requirement_id': rid, 'fields': {'due_date': '2026-11-12'}}, ctx)
    req = db.session.get(TransactionRequirement, rid)
    assert str(req.due_at.date()) == '2026-11-12' and req.due_at_manual_override
    apply('manage_transaction_requirement', {'transaction_id': tid, 'action': 'remove', 'requirement_id': rid}, ctx)
    apply('manage_transaction_document', {'transaction_id': tid, 'document_id': doc.id, 'action': 'remove'}, ctx)
    assert db.session.get(TransactionDocument, doc.id) is None


def test_upload_requires_owned_attachment(deal):
    ctx, tid = deal
    result = dispatch('manage_transaction_document', {'transaction_id': tid, 'action': 'upload_completed'}, ctx)
    assert not result.ok and 'Attach' in result.error
    from services.bob_tools.context import AttachmentTurnContext
    bad = ctx.with_attachment(AttachmentTurnContext(attachment_ref='not-a-signed-reference'))
    assert not dispatch('manage_transaction_document', {'transaction_id': tid, 'action': 'upload_completed'}, bad).ok


def test_delete_deal_requires_confirmation_and_removes_it(deal):
    ctx, tid = deal
    pending = dispatch('delete_transaction', {'transaction_id': tid}, ctx)
    assert pending.requires_confirmation, pending.error
    assert db.session.get(Transaction, tid) is not None
    result = confirm_action(pending.action_id, ctx)
    assert result.ok, result.error
    assert db.session.get(Transaction, tid) is None


def test_catalog_has_no_excluded_workflows_and_routes_exist(app):
    excluded = ('sign', 'participant', 'assignment', 'portal', 'print', 'download')
    with app.app_context():
        for spec in OPERATIONS.values():
            assert not any(word in spec.endpoint for word in excluded), spec.endpoint
            rules = list(app.url_map.iter_rules('transactions.' + spec.endpoint))
            assert any(spec.method in rule.methods for rule in rules), spec.endpoint


def test_uploaded_file_is_pinned_through_confirmation(deal, monkeypatch):
    from models import TransactionDocument
    from services.bob_attachment_refs import make_attachment_ref, sha256_digest
    from services.bob_tools.context import AttachmentTurnContext
    ctx, tid = deal
    content = b'%PDF-1.4\nsynthetic document for filing test'
    token = make_attachment_ref(user_id=ctx.user_id, organization_id=ctx.organization_id,
        storage_path=f'user_{ctx.user_id}/receipt.pdf', mime='application/pdf', size=len(content),
        filename='receipt.pdf', digest=sha256_digest(content))
    stored = []
    monkeypatch.setattr('services.bob_attachment_refs.download_file', lambda *a: content)
    def upload(**kw):
        stored.append(kw)
        return {'path': f'transactions/{tid}/receipt.pdf'}
    monkeypatch.setattr('services.supabase_storage.upload_external_document', upload)
    monkeypatch.setattr('services.intake_service.post_upload_processing', lambda doc: None)
    attached = ctx.with_attachment(AttachmentTurnContext(attachment_ref=token, filename='receipt.pdf'))
    pending = dispatch('manage_transaction_document', {'transaction_id': tid, 'action': 'upload_completed'}, attached)
    assert pending.requires_confirmation, pending.error
    assert stored == []
    # Confirmation may arrive in a new message without attachment context.
    result = confirm_action(pending.action_id, ctx)
    assert result.ok, result.error
    assert stored[0]['file_data'] == content and stored[0]['transaction_id'] == tid
    doc = db.session.get(TransactionDocument, result.data['document_id'])
    assert doc.signed_original_filename == 'receipt.pdf'
    assert doc.organization_id == ctx.organization_id and doc.transaction_id == tid


def test_email_preview_send_and_changed_draft(deal, monkeypatch):
    from models import User
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Email Buyer', 'offer_price': 400000}, ctx)
    fields = {'offer_ids': [offer.data['offer_id']], 'to': 'synthetic@example.test',
              'subject': 'Reviewed offer', 'note': 'Please review this offer.'}
    sent = []
    def send(draft, **kw):
        sent.append((draft, kw))
        return {'recipients': ['synthetic@example.test'], 'cc': [], 'skipped': False, 'via': 'test'}
    monkeypatch.setattr('routes.transactions.offers.send_draft', send)
    pending = dispatch('send_offer_email', {'transaction_id': tid, 'fields': fields}, ctx)
    assert pending.requires_confirmation, pending.error
    assert pending.data['preview']['draft']['subject'] == 'Reviewed offer'
    changes = {row['field']: row['to'] for row in pending.data['preview']['changes']}
    assert changes['email_subject'] == 'Reviewed offer'
    assert 'synthetic@example.test' in changes['recipients']
    assert 'Please review this offer.' in changes['email_body']
    from routes.agent_bob import preview_text
    from services.messaging.conversation import _format_pending_preview
    for rendered in (preview_text(pending.data['preview']), _format_pending_preview(pending.data, 'Review email')):
        assert 'synthetic@example.test' in rendered and 'Please review this offer.' in rendered
    assert sent == []
    result = confirm_action(pending.action_id, ctx)
    assert result.ok, result.error
    assert sent[0][0].subject == 'Reviewed offer'
    assert sent[0][1]['to_emails'] == ['synthetic@example.test']
    pending = dispatch('send_offer_email', {'transaction_id': tid, 'fields': fields}, ctx)
    user = db.session.get(User, ctx.user_id)
    original_name = user.first_name
    try:
        user.first_name = 'Changed sender'
        db.session.commit()
        denied = confirm_action(pending.action_id, ctx)
        assert not denied.ok and 'draft changed' in denied.error
        assert len(sent) == 1
    finally:
        user.first_name = original_name
        db.session.commit()


def test_contract_milestone_partial_edit_and_close(deal):
    from models import SellerContractMilestone, SellerAcceptedContract, SellerClosingSummary
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Contract Buyer', 'offer_price': 400000}, ctx)
    accepted = apply('accept_offer', {'offer_id': offer.data['offer_id'], 'effective_date': '2026-11-01'}, ctx)
    cid = accepted.data['accepted_contract_id']
    apply('manage_transaction_contract', {'transaction_id': tid, 'contract_id': cid, 'action': 'update_details',
        'fields': {'closing_date': '2026-12-15', 'financing_type': 'cash'}}, ctx)
    assert str(db.session.get(SellerAcceptedContract, cid).closing_date) == '2026-12-15'
    created = apply('manage_transaction_contract', {'transaction_id': tid, 'contract_id': cid, 'action': 'create_milestone',
        'fields': {'title': 'Confirm utilities', 'due_at': '2026-12-10T10:30', 'notes': 'Keep this note'}}, ctx)
    mid = created.data['milestone']['id']
    apply('manage_transaction_contract', {'transaction_id': tid, 'contract_id': cid, 'milestone_id': mid, 'action': 'update_milestone',
        'fields': {'status': 'completed'}}, ctx)
    row = db.session.get(SellerContractMilestone, mid)
    assert row.title == 'Confirm utilities' and row.notes == 'Keep this note'
    assert row.status == 'completed' and row.due_at.hour == 10
    apply('manage_transaction_contract', {'transaction_id': tid, 'contract_id': cid, 'action': 'close',
        'fields': {'actual_closing_date': '2026-12-15', 'final_sales_price': 400000}}, ctx)
    assert db.session.get(SellerAcceptedContract, cid).status == 'closed'
    assert SellerClosingSummary.query.filter_by(transaction_id=tid).count() == 1


def test_paid_foreign_org_and_unassigned_agent_cannot_access(deal, seed):
    from models import Organization
    ctx, tid = deal
    org = db.session.get(Organization, seed['org_b'])
    previous = org.subscription_tier
    try:
        org.subscription_tier = 'pro'
        db.session.commit()
        for user, org_id, role in [(seed['owner_b'], seed['org_b'], 'owner'), (seed['agent_a'], seed['org_a'], 'agent')]:
            other = BobContext(user_id=user, organization_id=org_id, org_role=role)
            assert not dispatch('get_transaction_workspace', {'transaction_id': tid, 'section': 'details'}, other).ok
            assert not dispatch('update_transaction_details', {'transaction_id': tid, 'fields': {'city': 'Wrong city'}}, other).ok
        assert db.session.get(Transaction, tid).city == 'Houston'
    finally:
        org.subscription_tier = previous
        db.session.commit()


def test_mcp_write_scope_cannot_delete_transaction_or_document(deal):
    from services.mcp.adapter import call_mcp_tool
    from models import TransactionDocument
    ctx, tid = deal
    doc = TransactionDocument(organization_id=ctx.organization_id, transaction_id=tid,
        template_slug='survey', template_name='Survey', status='pending')
    db.session.add(doc)
    db.session.commit()
    assert call_mcp_tool('delete_transaction', {'transaction_id': tid}, ctx, ['read', 'write'])['isError']
    assert call_mcp_tool('manage_transaction_document', {'transaction_id': tid, 'document_id': doc.id,
        'action': 'remove'}, ctx, ['read', 'write'])['isError']
    assert call_mcp_tool('manage_transaction_intake', {'transaction_id': tid,
        'action': 'generate_package'}, ctx, ['read', 'write'])['isError']
    assert db.session.get(Transaction, tid) and db.session.get(TransactionDocument, doc.id)


def test_amendment_accept_recomputes_contract_date(deal):
    from models import SellerAcceptedContract, SellerContractAmendment, SellerContractAmendmentVersion
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Amendment Buyer',
        'offer_price': 400000, 'proposed_close_date': '2026-12-01'}, ctx)
    accepted = apply('accept_offer', {'offer_id': offer.data['offer_id'], 'effective_date': '2026-11-01'}, ctx)
    cid = accepted.data['accepted_contract_id']
    amendment = SellerContractAmendment(organization_id=ctx.organization_id, transaction_id=tid,
        accepted_contract_id=cid, created_by_id=ctx.user_id, amendment_type='closing_date', status='received')
    db.session.add(amendment)
    db.session.flush()
    version = SellerContractAmendmentVersion(organization_id=ctx.organization_id, transaction_id=tid,
        amendment_id=amendment.id, created_by_id=ctx.user_id, direction='buyer_amendment',
        terms_data={'closing_date': '2026-12-20', 'sales_price': '395000'})
    db.session.add(version)
    db.session.flush()
    amendment.current_version_id = version.id
    db.session.commit()
    pending = dispatch('manage_transaction_amendment', {'transaction_id': tid,
        'amendment_id': amendment.id, 'action': 'accept', 'fields': {'selected': {'closing_date': True, 'sales_price': True}}}, ctx)
    assert pending.requires_confirmation, pending.error
    result = confirm_action(pending.action_id, ctx)
    assert result.ok, result.error
    assert str(db.session.get(SellerAcceptedContract, cid).closing_date) == '2026-12-20'
    assert str(db.session.get(Transaction, tid).expected_close_date) == '2026-12-20'
    assert float(db.session.get(SellerAcceptedContract, cid).accepted_price) == 395000


def test_bootstrap_upload_returns_readable_session(deal, monkeypatch):
    import fitz
    from models import Organization, ContractBootstrapSession
    from services.bob_attachment_refs import make_attachment_ref, sha256_digest
    from services.bob_tools.context import AttachmentTurnContext
    ctx, tid = deal
    org = db.session.get(Organization, ctx.organization_id)
    original = org.feature_flags
    org.feature_flags = {**(original or {}), 'BOB_VTC_PILOT': True}
    db.session.commit()
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), 'Synthetic listing contract')
    content = pdf.tobytes()
    pdf.close()
    token = make_attachment_ref(user_id=ctx.user_id, organization_id=ctx.organization_id,
        storage_path=f'user_{ctx.user_id}/contract.pdf', mime='application/pdf', size=len(content),
        filename='contract.pdf', digest=sha256_digest(content))
    monkeypatch.setattr('services.bob_attachment_refs.download_file', lambda *a: content)
    monkeypatch.setattr('services.contract_bootstrap.store_bootstrap_file', lambda **kw: 'local-test.pdf')
    monkeypatch.setattr('services.contract_bootstrap.enqueue_bootstrap_processing', lambda **kw: None)
    try:
        attached = ctx.with_attachment(AttachmentTurnContext(attachment_ref=token))
        pending = dispatch('manage_transaction_bootstrap', {'action': 'upload', 'fields': {'side': 'seller'}}, attached)
        assert pending.requires_confirmation, pending.error
        uploaded = confirm_action(pending.action_id, ctx)
        assert uploaded.ok, uploaded.error
        sid = uploaded.data['session_ids'][0]
        review = apply('get_contract_inbox', {'session_id': sid}, ctx)
        assert review.data['session_id'] == sid and review.data['filename'] == 'contract.pdf'
        row = db.session.get(ContractBootstrapSession, sid)
        db.session.delete(row)
        db.session.commit()
    finally:
        org.feature_flags = original
        db.session.commit()


def test_mcp_email_requires_unchanged_reviewed_preview(deal, monkeypatch):
    from dataclasses import replace
    from models import SellerOffer
    from services.mcp.adapter import call_mcp_tool
    ctx, tid = deal
    ctx = replace(ctx, surface='mcp')
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Receipt Buyer', 'offer_price': 400000}, ctx)
    fields = {'offer_ids': [offer.data['offer_id']], 'to': 'synthetic@example.test', 'subject': 'Reviewed subject'}
    sent = []
    def send(draft, **kw):
        sent.append(kw)
        return {'recipients': kw['to_emails'], 'cc': [], 'skipped': False, 'via': 'test'}
    monkeypatch.setattr('routes.transactions.offers.send_draft', send)
    args = {'transaction_id': tid, 'fields': fields}
    assert call_mcp_tool('send_offer_email', args, ctx, ['read', 'write'])['isError']
    preview = apply('preview_offer_email', args, ctx)
    args['preview_receipt'] = preview.data['preview_receipt']
    assert call_mcp_tool('send_offer_email', {**args, 'fields': {**fields, 'to': 'other@example.test'}}, ctx, ['read', 'write'])['isError']
    row = db.session.get(SellerOffer, offer.data['offer_id'])
    row.buyer_names = 'Changed Buyer'
    db.session.commit()
    assert call_mcp_tool('send_offer_email', args, ctx, ['read', 'write'])['isError']
    assert sent == []
    args['preview_receipt'] = apply('preview_offer_email', args, ctx).data['preview_receipt']
    assert not call_mcp_tool('send_offer_email', args, ctx, ['read', 'write'])['isError']
    assert len(sent) == 1 and sent[0]['to_emails'] == ['synthetic@example.test']


@pytest.mark.parametrize('surface,allowed,sensitivity', [('bob_chat', False, 'internal'), ('telegram', True, 'confidential'), ('bob_telegram', True, 'confidential')])
def test_restricted_document_content_is_not_returned(deal, surface, allowed, sensitivity):
    from dataclasses import replace
    from models import TransactionDocument, DocumentReviewReport
    ctx, tid = deal
    ctx = replace(ctx, surface=surface)
    doc = TransactionDocument(organization_id=ctx.organization_id, transaction_id=tid,
        template_slug='survey', template_name='Private file', status='pending',
        field_data={'private': 'private-document-value'}, ai_processing_allowed=allowed, sensitivity_class=sensitivity)
    db.session.add(doc)
    db.session.flush()
    db.session.add(DocumentReviewReport(organization_id=ctx.organization_id, transaction_id=tid,
        document_id=doc.id, title='Review', summary='private-document-value', status='open', severity='attention'))
    db.session.commit()
    result = apply('get_transaction_workspace', {'transaction_id': tid, 'section': 'documents'}, ctx)
    assert result.data['records'] == [{'id': doc.id, 'status': 'pending', 'content_restricted': True}]
    for section in ('reviews', 'proposals', 'intake', 'history', 'document_routing', 'offer_versions'):
        denied = dispatch('get_transaction_workspace', {'transaction_id': tid, 'section': section}, ctx)
        assert not denied.ok and 'private-document-value' not in str(denied.for_model())


def test_contract_effective_date_and_time_partial_edits(deal):
    from datetime import datetime, date
    from models import SellerAcceptedContract
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Time Buyer', 'offer_price': 400000}, ctx)
    result = apply('accept_offer', {'offer_id': offer.data['offer_id'], 'effective_date': '2026-11-01', 'effective_at': '2026-11-01T12:30'}, ctx)
    cid = result.data['accepted_contract_id']
    args = {'transaction_id': tid, 'contract_id': cid, 'action': 'update_details'}
    pending = dispatch('manage_transaction_contract', {**args, 'fields': {'effective_date': '2026-11-02'}}, ctx)
    assert {'field': 'effective_date', 'from': '2026-11-01', 'to': '2026-11-02'} in pending.data['preview']['changes']
    assert confirm_action(pending.action_id, ctx).ok
    contract = db.session.get(SellerAcceptedContract, cid)
    assert contract.effective_date == date(2026, 11, 2)
    assert contract.effective_at == datetime(2026, 11, 1, 12, 30)
    apply('manage_transaction_contract', {**args, 'fields': {'effective_at': '2026-11-02T14:15'}}, ctx)
    assert contract.effective_date == date(2026, 11, 2)
    assert contract.effective_at == datetime(2026, 11, 2, 14, 15)


def test_accept_offer_rechecks_document_privacy_at_confirmation(deal):
    from models import TransactionDocument, SellerAcceptedContract
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Private Buyer', 'offer_price': 400000}, ctx)
    doc = TransactionDocument(organization_id=ctx.organization_id, transaction_id=tid,
        template_slug='seller-offer-contract', template_name='Offer', status='pending', ai_processing_allowed=True)
    db.session.add(doc)
    db.session.commit()
    args = {'offer_id': offer.data['offer_id']}
    pending = dispatch('accept_offer', args, ctx)
    assert pending.requires_confirmation
    doc.ai_processing_allowed = False
    db.session.commit()
    assert not confirm_action(pending.action_id, ctx).ok
    assert not dispatch('accept_offer', args, ctx, confirmation=CONFIRM_PRECLEARED).ok
    assert SellerAcceptedContract.query.filter_by(transaction_id=tid).count() == 0


def test_send_only_collaborator_can_send_reviewed_email(deal, seed, monkeypatch):
    from models import TransactionAssignment
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Collaborator Buyer', 'offer_price': 400000}, ctx)
    assignment = TransactionAssignment(organization_id=ctx.organization_id, transaction_id=tid,
        user_id=seed['agent_a'], role='collaborator', capabilities={'send_comms': True})
    db.session.add(assignment)
    db.session.commit()
    collaborator = BobContext(user_id=seed['agent_a'], organization_id=ctx.organization_id)
    assert not dispatch('update_transaction_details', {'transaction_id': tid, 'fields': {'city': 'Denied'}}, collaborator).ok
    sent = []
    def send(draft, **kw):
        sent.append(kw)
        return {'recipients': kw['to_emails'], 'cc': [], 'skipped': False, 'via': 'test'}
    monkeypatch.setattr('routes.transactions.offers.send_draft', send)
    pending = dispatch('send_offer_email', {'transaction_id': tid,
        'fields': {'offer_ids': [offer.data['offer_id']], 'to': 'synthetic@example.test'}}, collaborator)
    assert pending.requires_confirmation, pending.error
    result = confirm_action(pending.action_id, collaborator)
    assert result.ok, result.error
    assert len(sent) == 1


def test_bootstrap_candidates_and_target_stale_approval(deal, seed):
    from models import ContractBootstrapSession, Organization
    ctx, tid = deal
    org = db.session.get(Organization, ctx.organization_id)
    previous = org.feature_flags
    row = ContractBootstrapSession(organization_id=ctx.organization_id, uploader_user_id=seed['agent_a'],
        match_candidates=[{'transaction_id': tid, 'address': 'Unassigned address'}],
        classification={'side': 'seller', 'parties': [{'matches': [{'email': 'private-contact@example.test'}]}]})
    try:
        org.feature_flags = {**(previous or {}), 'BOB_VTC_PILOT': True}
        db.session.add(row)
        db.session.commit()
        agent = BobContext(user_id=seed['agent_a'], organization_id=ctx.organization_id)
        result = apply('manage_transaction_bootstrap', {'session_id': row.id, 'action': 'match', 'fields': {'decision': 'manual'}}, agent)
        assert result.data['review']['match_candidates'] == []
        assert 'parties' not in result.data['review']
        assert 'parties' not in result.data['review']['bob_questions']
        assert 'private-contact@example.test' not in str(result.for_model())
        inbox = apply('get_contract_inbox', {'session_id': row.id}, agent)
        assert 'private-contact@example.test' not in str(inbox.for_model())
        row.uploader_user_id = ctx.user_id
        db.session.commit()
        args = {'session_id': row.id, 'action': 'match', 'fields': {'decision': 'attach', 'transaction_id': tid}}
        pending = dispatch('manage_transaction_bootstrap', args, ctx)
        assert pending.requires_confirmation, pending.error
        tx = db.session.get(Transaction, tid)
        tx.city = 'Changed after preview'
        db.session.commit()
        assert not confirm_action(pending.action_id, ctx).ok
        assert row.matched_transaction_id is None
        apply('manage_transaction_bootstrap', args, ctx)
        row.extracted_candidates = {'sales_price': {'value': '400000', 'evidence': {}}}
        db.session.commit()
        pending = dispatch('manage_transaction_bootstrap', {'session_id': row.id, 'action': 'approve', 'fields': {'selected': {'sales_price': True}, 'corrections': {'sales_price': '415000'}}}, ctx)
        assert any(c['field'] == 'sales_price' and c['to'] == '415000' for c in pending.data['preview']['changes'])
        assert pending.data['preview']['transaction_id'] == tid
        assert '901 Parity Lane' in pending.data['preview']['address']
        assert pending.requires_confirmation, pending.error
        tx.city = 'Changed after approval preview'
        db.session.commit()
        denied = confirm_action(pending.action_id, ctx)
        assert not denied.ok and 'changed after the preview' in denied.error
        assert tx.city == 'Changed after approval preview'
    finally:
        db.session.delete(row)
        org.feature_flags = previous
        db.session.commit()


@pytest.mark.parametrize('supplied_id', [None, 0])
def test_selected_transaction_is_pinned_in_pending_action(deal, supplied_id):
    from dataclasses import replace
    ctx, tid = deal
    selected = replace(ctx, selected_transaction_id=tid)
    pending = dispatch('update_transaction_details', {'transaction_id': supplied_id, 'fields': {'city': 'Austin'}}, selected)
    assert pending.requires_confirmation, pending.error
    switched = replace(ctx, selected_transaction_id=-999)
    result = confirm_action(pending.action_id, switched)
    assert result.ok, result.error
    assert db.session.get(Transaction, tid).city == 'Austin'


def test_contract_edits_survive_unrelated_amendment_and_mcp_remove_needs_scope(deal):
    from models import SellerAcceptedContract, SellerContractAmendment, SellerContractAmendmentVersion, SellerContractMilestone
    from services.mcp.adapter import call_mcp_tool
    from services.mcp.scopes import annotations_for_tool
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Alias Buyer', 'offer_price': 400000, 'proposed_close_date': '2026-12-01'}, ctx)
    cid = apply('accept_offer', {'offer_id': offer.data['offer_id'], 'effective_date': '2026-11-01'}, ctx).data['accepted_contract_id']
    apply('manage_transaction_contract', {'transaction_id': tid, 'contract_id': cid, 'action': 'update_details', 'fields': {'accepted_price': 425000, 'closing_date': '2026-12-15', 'effective_at': '2026-11-02T14:15'}}, ctx)
    amendment = SellerContractAmendment(organization_id=ctx.organization_id, transaction_id=tid, accepted_contract_id=cid, created_by_id=ctx.user_id, amendment_type='other', status='received')
    db.session.add(amendment)
    db.session.flush()
    version = SellerContractAmendmentVersion(organization_id=ctx.organization_id, transaction_id=tid, amendment_id=amendment.id, created_by_id=ctx.user_id, direction='buyer_amendment', terms_data={'option_period_days': 10})
    db.session.add(version)
    db.session.flush()
    amendment.current_version_id = version.id
    db.session.commit()
    pending = dispatch('manage_transaction_amendment', {'transaction_id': tid, 'amendment_id': amendment.id, 'action': 'accept'}, ctx)
    assert pending.requires_confirmation, pending.error
    assert any(c['field'] == 'option_period_days' and c['to'] == '10' for c in pending.data['preview']['changes'])
    assert confirm_action(pending.action_id, ctx).ok
    contract = db.session.get(SellerAcceptedContract, cid)
    assert float(contract.accepted_price) == 425000
    assert str(contract.effective_at) == '2026-11-02 14:15:00'
    assert str(contract.closing_date) == '2026-12-15'
    created = apply('manage_transaction_contract', {'transaction_id': tid, 'contract_id': cid, 'action': 'create_milestone', 'fields': {'title': 'Permission regression milestone'}}, ctx)
    milestone = db.session.get(SellerContractMilestone, created.data['milestone']['id'])
    args = {'transaction_id': tid, 'contract_id': cid, 'milestone_id': milestone.id, 'action': 'update_milestone', 'fields': {'action': 'remove'}}
    assert call_mcp_tool('manage_transaction_contract', args, ctx, ['read', 'write'])['isError']
    assert not (milestone.source_data or {}).get('removed_at')
    result = call_mcp_tool('manage_transaction_contract', args, ctx, ['read', 'write', 'destructive'])
    assert not result['isError'], result
    assert milestone.source_data.get('removed_at')
    assert annotations_for_tool('manage_transaction_contract')['destructiveHint']


def test_proposal_preview_shows_selected_corrected_values(deal):
    from models import TransactionChangeProposal
    ctx, tid = deal
    row = TransactionChangeProposal(organization_id=ctx.organization_id, transaction_id=tid, change_type='extracted_fields', status='pending', proposed_changes={'street_address': 'Extracted address', 'closing_date': '2026-12-01'})
    db.session.add(row)
    db.session.commit()
    pending = dispatch('manage_transaction_review', {'transaction_id': tid, 'proposal_id': row.id, 'action': 'approve_fields', 'fields': {'selected': {'street_address': True, 'closing_date': False}, 'corrections': {'street_address': 'Corrected address'}}}, ctx)
    assert pending.requires_confirmation, pending.error
    changes = pending.data['preview']['changes']
    assert {'field': 'street_address', 'from': '901 Parity Lane', 'to': 'Corrected address'} in changes
    assert not any(c['field'] in ('closing_date', 'selected', 'corrections') for c in changes)


@pytest.mark.parametrize('surface', ['bob_chat', 'bob_telegram'])
def test_unfiled_sensitive_bootstrap_is_not_exposed(deal, surface):
    from dataclasses import replace
    from models import ContractBootstrapSession, Organization
    ctx, tid = deal
    org = db.session.get(Organization, ctx.organization_id)
    previous = org.feature_flags
    row = ContractBootstrapSession(organization_id=ctx.organization_id, uploader_user_id=ctx.user_id,
        classification={'side': 'tenant', 'document_type': 'lease'}, original_filename='lease.pdf',
        extracted_candidates={'private': {'value': 'private-tenant-value'}})
    try:
        org.feature_flags = {**(previous or {}), 'BOB_VTC_PILOT': True}
        db.session.add(row)
        db.session.commit()
        result = dispatch('get_contract_inbox', {'session_id': row.id}, replace(ctx, surface=surface))
        assert not result.ok
        assert 'private-tenant-value' not in str(result.for_model())
        inbox = apply('get_contract_inbox', {}, replace(ctx, surface=surface))
        restricted = next(item for item in inbox.data['sessions'] if item['id'] == row.id)
        assert restricted['content_restricted'] and 'original_filename' not in restricted
    finally:
        db.session.delete(row)
        org.feature_flags = previous
        db.session.commit()


def test_approved_proposal_replaces_original_offer_aliases(deal):
    from models import TransactionChangeProposal, SellerAcceptedContract
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Proposal Buyer', 'offer_price': 400000, 'proposed_close_date': '2026-12-01'}, ctx)
    cid = apply('accept_offer', {'offer_id': offer.data['offer_id'], 'effective_date': '2026-11-01'}, ctx).data['accepted_contract_id']
    row = TransactionChangeProposal(organization_id=ctx.organization_id, transaction_id=tid, change_type='extracted_fields', status='pending', proposed_changes={'sales_price': '425000', 'closing_date': '2026-12-15'})
    db.session.add(row)
    db.session.commit()
    pending = dispatch('manage_transaction_review', {'transaction_id': tid, 'proposal_id': row.id, 'action': 'approve_fields', 'fields': {'selected': {'sales_price': True, 'closing_date': True}}}, ctx)
    assert pending.requires_confirmation, pending.error
    result = confirm_action(pending.action_id, ctx)
    assert result.ok, result.error
    contract = db.session.get(SellerAcceptedContract, cid)
    assert float(contract.accepted_price) == 425000
    assert str(contract.closing_date) == '2026-12-15'
    assert contract.frozen_terms['offer_price'] == contract.frozen_terms['sales_price']
    assert contract.frozen_terms['proposed_close_date'] == contract.frozen_terms['closing_date']


def test_document_field_edits_preserve_server_metadata(deal):
    from models import TransactionDocument
    ctx, tid = deal
    metadata = {'_document_identity': {'kind': 'purchase_contract'}, '_controlling_baseline': {'contract_id': 123}, '_meta': {'amount': {'page': 1}}}
    doc = TransactionDocument(organization_id=ctx.organization_id, transaction_id=tid, template_slug='custom-regression', template_name='Regression', status='pending', field_data={**metadata, 'amount': '100'})
    db.session.add(doc)
    db.session.commit()
    apply('manage_transaction_document', {'transaction_id': tid, 'document_id': doc.id, 'action': 'save_fields', 'fields': {'field_data': {'amount': '200', '_document_identity': {'kind': 'changed'}}}}, ctx)
    assert doc.field_data['amount'] == '200'
    assert {key: doc.field_data[key] for key in metadata} == metadata


def test_assigned_editor_can_accept_offer_and_update_listing(deal, seed):
    from models import TransactionAssignment
    ctx, tid = deal
    db.session.add(TransactionAssignment(organization_id=ctx.organization_id, transaction_id=tid,
        user_id=seed['agent_a'], role='collaborator', capabilities={'edit': True}))
    db.session.commit()
    editor = BobContext(user_id=seed['agent_a'], organization_id=ctx.organization_id)
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Editor Buyer', 'offer_price': 400000}, editor)
    accepted = apply('accept_offer', {'offer_id': offer.data['offer_id'], 'effective_date': '2026-11-01'}, editor)
    assert accepted.data['accepted_contract_id']
    result = apply('update_listing_operations', {'transaction_id': tid, 'fields': {'pet_notes': 'Cat inside'}}, editor)
    assert result.ok


def test_bootstrap_address_approval_does_not_create_participants(deal, monkeypatch):
    from models import ContractBootstrapSession, Organization, Contact, TransactionParticipant
    from services.transaction_helpers import purge_transaction_dependent_rows
    ctx, tid = deal
    org = db.session.get(Organization, ctx.organization_id)
    previous = org.feature_flags
    row = ContractBootstrapSession(organization_id=ctx.organization_id, uploader_user_id=ctx.user_id,
        original_filename='listing.pdf', status='awaiting_review', match_status='create_new',
        classification={'side': 'seller', 'document_type': 'listing_agreement', 'seller_name': 'Unselected Person', 'seller_email': 'unselected@example.test'},
        extracted_candidates={'property_address': {'value': '12 Explicit Review Road'}, 'seller_name': {'value': 'Unselected Person'}})
    new_tid = None
    monkeypatch.setattr('services.contract_bootstrap._link_document_to_transaction', lambda **kwargs: None)
    try:
        org.feature_flags = {**(previous or {}), 'BOB_VTC_PILOT': True}
        db.session.add(row)
        db.session.commit()
        before_contacts = Contact.query.count()
        pending = dispatch('manage_transaction_bootstrap', {'session_id': row.id, 'action': 'approve', 'fields': {'selected': {'property_address': True}}}, ctx)
        assert pending.requires_confirmation, pending.error
        result = confirm_action(pending.action_id, ctx)
        assert result.ok, result.error
        new_tid = row.matched_transaction_id
        assert new_tid and db.session.get(Transaction, new_tid).street_address == '12 Explicit Review Road'
        assert TransactionParticipant.query.filter_by(transaction_id=new_tid).count() == 0
        assert Contact.query.count() == before_contacts
    finally:
        db.session.delete(row)
        db.session.flush()
        if new_tid:
            Task.query.filter_by(transaction_id=new_tid).delete(synchronize_session=False)
            purge_transaction_dependent_rows(new_tid)
            db.session.delete(db.session.get(Transaction, new_tid))
        org.feature_flags = previous
        db.session.commit()


@pytest.mark.parametrize('source', ['manual', 'listing_agreement_extraction'])
def test_partial_commission_approval_honors_explicit_clear(deal, source):
    from models import SellerCommissionTerms
    from services.transaction_helpers import sync_seller_commission_terms_from_listing
    ctx, tid = deal
    terms = SellerCommissionTerms(organization_id=ctx.organization_id, transaction_id=tid, created_by_id=ctx.user_id,
        listing_commission_percent=3, coop_compensation_percent=2, source=source)
    db.session.add(terms)
    db.session.flush()
    sync_seller_commission_terms_from_listing(transaction=db.session.get(Transaction, tid),
        field_data={'listing_side_percent': '', 'listing_side_flat': '8000'},
        user_id=ctx.user_id, org_id=ctx.organization_id, partial=True)
    assert terms.listing_commission_percent is None
    assert float(terms.listing_commission_flat) == 8000
    assert float(terms.coop_compensation_percent) == 2
    sync_seller_commission_terms_from_listing(transaction=db.session.get(Transaction, tid),
        field_data={'listing_side_flat': '', 'buyer_agent_commission_percent': ''},
        user_id=ctx.user_id, org_id=ctx.organization_id, partial=True)
    assert terms.listing_commission_flat is None
    assert terms.coop_compensation_percent is None


def test_milestone_notes_preserve_automatic_date_precision(deal):
    from datetime import datetime
    from models import SellerContractMilestone
    ctx, tid = deal
    offer = apply('create_offer', {'transaction_id': tid, 'buyer_names': 'Milestone Buyer', 'offer_price': 400000}, ctx)
    cid = apply('accept_offer', {'offer_id': offer.data['offer_id'], 'effective_date': '2026-11-01'}, ctx).data['accepted_contract_id']
    milestone = SellerContractMilestone(organization_id=ctx.organization_id, transaction_id=tid, accepted_contract_id=cid,
        milestone_key='regression', title='Automatic deadline', source='effective_date', due_at=datetime(2026, 11, 7, 12, 30, 45))
    db.session.add(milestone)
    db.session.commit()
    apply('manage_transaction_contract', {'transaction_id': tid, 'contract_id': cid, 'milestone_id': milestone.id,
        'action': 'update_milestone', 'fields': {'notes': 'Call before deadline'}}, ctx)
    assert milestone.notes == 'Call before deadline'
    assert milestone.due_at == datetime(2026, 11, 7, 12, 30, 45)
    assert milestone.source == 'effective_date'
