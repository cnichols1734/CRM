"""Document review inbox + dismissible attention toast + Review and Apply APIs."""

import logging
from typing import Any

from flask import flash, jsonify, abort, request, render_template, url_for
from flask_login import login_required, current_user

from models import DocumentReviewReport, TransactionChangeProposal, TransactionDocument, db
from services.document_extractor import field_provenance, visible_field_data
from services.document_review import (
    dismiss_toast,
    list_open_reports,
    pending_toasts,
    resolve_report,
)
from services.proposal_service import ProposalService
from services.transaction_auth import CAP_EDIT, CAP_VIEW, get_transaction_for_user
from . import transactions_bp
from .decorators import transactions_required

logger = logging.getLogger(__name__)

_SKIP_FIELD_KEYS = frozenset({
    'sanity_flags',
    'unreadable_pages',
    'authoritative_deadlines',
    'document_summary',
    'detected_documents',
    'detected_document_types',
})


def _humanize_field_key(key: str) -> str:
    return key.replace('_', ' ').capitalize()


def _proposal_changes(proposal: TransactionChangeProposal | None) -> dict:
    if not proposal or not isinstance(proposal.proposed_changes, dict):
        return {}
    return {
        str(key): value
        for key, value in proposal.proposed_changes.items()
        if not str(key).startswith('_')
    }


def _crm_value_for_field(
    key: str,
    findings_by_field: dict[str, list[dict]],
    proposed_changes: dict,
) -> Any:
    for finding in findings_by_field.get(key) or []:
        if finding.get('crm_value') is not None:
            return finding.get('crm_value')
    entry = proposed_changes.get(key)
    if isinstance(entry, dict) and 'crm_value' in entry:
        return entry.get('crm_value')
    return None


def build_workspace_fields(
    *,
    field_data: dict | None,
    findings: list[dict] | None,
    proposal: TransactionChangeProposal | None,
) -> tuple[list[dict], list[dict], dict[str, list[dict]], str | None]:
    """Build right-pane field cards + finding indexes for the review workspace."""
    visible = visible_field_data(field_data)
    provenance = field_provenance(field_data)
    document_summary = visible.get('document_summary')
    summary_text = (
        str(document_summary).strip()
        if isinstance(document_summary, str) and document_summary.strip()
        else None
    )

    findings_by_field: dict[str, list[dict]] = {}
    general_findings: list[dict] = []
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        field_key = finding.get('field_key')
        if field_key:
            findings_by_field.setdefault(str(field_key), []).append(finding)
        else:
            general_findings.append(finding)

    proposed_changes = _proposal_changes(proposal)
    fields: list[dict] = []
    for key, value in visible.items():
        if key in _SKIP_FIELD_KEYS or str(key).startswith('_'):
            continue
        meta = provenance.get(key) if isinstance(provenance.get(key), dict) else {}
        page = meta.get('page')
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        quote = meta.get('quote')
        if not isinstance(quote, str) or not quote.strip():
            quote = None
        confidence = meta.get('confidence')
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        fields.append({
            'key': key,
            'label': _humanize_field_key(key),
            'value': value,
            'page': page,
            'quote': quote,
            'confidence': confidence,
            'proposed': key in proposed_changes,
            'crm_value': _crm_value_for_field(key, findings_by_field, proposed_changes),
            'findings': findings_by_field.get(key) or [],
        })

    def sort_key(item: dict):
        page_sort = item['page'] if item['page'] is not None else 10**9
        return (
            0 if item['proposed'] else 1,
            0 if item['findings'] else 1,
            page_sort,
            item['label'].lower(),
        )

    fields.sort(key=sort_key)
    return fields, general_findings, findings_by_field, summary_text


@transactions_bp.route('/<int:id>/document-reviews', methods=['GET'])
@login_required
@transactions_required
def list_document_reviews(id):
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    reports = list_open_reports(tx.id, current_user.organization_id)
    proposals = ProposalService.list_pending_proposals(
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    )
    proposal_document_ids = {
        proposal.source_document_id
        for proposal in proposals
        if proposal.source_document_id is not None
    }
    attention_count = sum(
        1 for report in reports if report.severity in ('attention', 'critical')
    )
    return jsonify({
        'reports': [r.to_dict() for r in reports],
        'pending_toasts': [r.to_dict() for r in pending_toasts(tx.id, current_user.organization_id)],
        'report_count': len(reports),
        'attention_count': attention_count,
        'html': render_template(
            'transactions/_document_review_reports.html',
            document_review_reports=reports,
            pending_proposal_document_ids=proposal_document_ids,
        ),
    })


@transactions_bp.route(
    '/<int:id>/document-reviews/<int:report_id>/dismiss-toast',
    methods=['POST'],
)
@login_required
@transactions_required
def dismiss_document_review_toast(id, report_id):
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    report = DocumentReviewReport.query.filter_by(
        id=report_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    updated = dismiss_toast(report.id, current_user.id, current_user.organization_id)
    return jsonify({'ok': True, 'report': updated.to_dict() if updated else None})


@transactions_bp.route(
    '/<int:id>/document-reviews/<int:report_id>/resolve',
    methods=['POST'],
)
@login_required
@transactions_required
def resolve_document_review(id, report_id):
    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    report = DocumentReviewReport.query.filter_by(
        id=report_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    updated = resolve_report(report.id, current_user.id, current_user.organization_id)
    return jsonify({'ok': True, 'report': updated.to_dict() if updated else None})


@transactions_bp.route('/<int:id>/proposals', methods=['GET'])
@login_required
@transactions_required
def list_transaction_proposals(id):
    """List pending change proposals for Review and Apply."""
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)
    proposals = ProposalService.list_pending_proposals(
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    )
    return jsonify({
        'proposals': [
            {
                'id': p.id,
                'change_type': p.change_type,
                'status': p.status,
                'rationale': p.rationale,
                'proposed_changes': p.proposed_changes or {},
                'source_document_id': p.source_document_id,
                'source_extraction_run_id': p.source_extraction_run_id,
                'created_at': p.created_at.isoformat() if p.created_at else None,
            }
            for p in proposals
        ]
    })


@transactions_bp.route(
    '/<int:id>/proposals/<int:proposal_id>/approve-selected',
    methods=['POST'],
)
@login_required
@transactions_required
def approve_selected_fields(id, proposal_id):
    """
    Approve selected fields from a pending proposal and apply as one change-set.
    Body: { selected: {field_key: bool}, corrections?: {field_key: value}, bob_action_id?: int }
    """
    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    proposal = TransactionChangeProposal.query.filter_by(
        id=proposal_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    data = request.get_json(silent=True) or {}
    selected = data.get('selected') or data.get('selected_fields') or {}
    corrections = data.get('corrections') or {}
    bob_action_id = data.get('bob_action_id')

    if not isinstance(corrections, dict):
        return jsonify({'ok': False, 'error': 'corrections must be an object'}), 400

    from services.controlling_contracts import (
        ControllingContractConflict,
        ControllingContractSeedError,
        maybe_create_baseline_after_term_approval,
    )
    from services.document_classification_policy import (
        ClassificationPolicyError,
        normalize_selected_field_flags,
    )

    try:
        selected_bool = normalize_selected_field_flags(selected)
    except ClassificationPolicyError as e:
        return jsonify({'ok': False, 'error': str(e), 'code': e.code}), 400

    try:
        audit = ProposalService.approve_and_apply_selected(
            proposal_id=proposal.id,
            reviewed_by_id=current_user.id,
            selected_fields=selected_bool,
            corrections=corrections,
            bob_action_id=int(bob_action_id) if bob_action_id is not None else None,
        )

        # Direct contract upload completion: classification confirmed scope=contract
        # with no baseline yet → create controlling baseline from selected fields only.
        source_doc = None
        if proposal.source_document_id:
            source_doc = TransactionDocument.query.filter_by(
                id=proposal.source_document_id,
                transaction_id=tx.id,
                organization_id=current_user.organization_id,
            ).first()
        baseline = maybe_create_baseline_after_term_approval(
            transaction=tx,
            document=source_doc,
            approved_terms=dict(proposal.proposed_changes or {}),
            actor_id=current_user.id,
        )
        db.session.commit()
    except ControllingContractConflict as e:
        db.session.rollback()
        return jsonify({
            'ok': False,
            'error': str(e),
            'code': e.code,
            'existing_contract_id': e.existing_contract_id,
        }), e.status
    except ControllingContractSeedError as e:
        db.session.rollback()
        return jsonify({
            'ok': False,
            'error': str(e),
            'code': e.code,
        }), 500
    except ValueError as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception:
        logger.exception(
            'approve_selected_fields failed tx=%s proposal=%s', id, proposal_id,
        )
        db.session.rollback()
        return jsonify({
            'ok': False,
            'error': 'Could not apply selected fields. Try again or contact support.',
        }), 500

    if data.get('flash_on_success'):
        flash('Selected fields applied to the transaction.', 'success')

    db.session.refresh(proposal)
    db.session.refresh(tx)
    from services.intake_service import seller_intake_handoff_url
    next_url = (
        seller_intake_handoff_url(tx, source_doc)
        or url_for('transactions.view_transaction', id=tx.id)
    )
    return jsonify({
        'ok': True,
        'proposal_id': proposal.id,
        'status': proposal.status,
        'applied_keys': list((proposal.proposed_changes or {}).keys()),
        'audit_event_id': audit.id if audit else None,
        'accepted_contract_id': baseline.id if baseline else None,
        'transaction_status': tx.status,
        'next_url': next_url,
    })


@transactions_bp.route(
    '/<int:id>/proposals/<int:proposal_id>/reject',
    methods=['POST'],
)
@login_required
@transactions_required
def reject_proposal(id, proposal_id):
    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    proposal = TransactionChangeProposal.query.filter_by(
        id=proposal_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    data = request.get_json(silent=True) or {}
    try:
        ProposalService.reject_proposal(
            proposal.id,
            current_user.id,
            rejection_reason=data.get('reason'),
        )
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 400

    return jsonify({'ok': True, 'proposal_id': proposal.id, 'status': proposal.status})


@transactions_bp.route('/<int:id>/documents/<int:doc_id>/review', methods=['GET'])
@login_required
@transactions_required
def document_review_workspace(id, doc_id):
    """Full-page side-by-side PDF + BOB findings workspace."""
    tx, decision = get_transaction_for_user(id, capability=CAP_VIEW)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    doc = TransactionDocument.query.filter_by(
        id=doc_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    report = (
        DocumentReviewReport.query.filter_by(
            document_id=doc.id,
            organization_id=current_user.organization_id,
        )
        .order_by(DocumentReviewReport.created_at.desc())
        .first()
    )

    proposals = ProposalService.list_pending_proposals(
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    )
    proposal = next(
        (p for p in proposals if p.source_document_id == doc.id),
        None,
    )

    fields, general_findings, _findings_by_field, document_summary = build_workspace_fields(
        field_data=doc.field_data if isinstance(doc.field_data, dict) else {},
        findings=(report.findings if report else None),
        proposal=proposal,
    )

    pdf_url = url_for('transactions.view_document_pdf', id=tx.id, doc_id=doc.id)
    approve_url = (
        url_for(
            'transactions.approve_selected_fields',
            id=tx.id,
            proposal_id=proposal.id,
        )
        if proposal else ''
    )
    resolve_url = url_for(
        'transactions.resolve_document_review_workspace',
        id=tx.id,
        doc_id=doc.id,
    )
    return_url = url_for('transactions.view_transaction', id=tx.id)
    download_url = pdf_url

    from models import SellerOfferDocument
    from services.document_classification_confirm import (
        build_routing_context_payload,
        mark_auto_filed_offer_confirmation,
    )
    from services.document_intake_ui import (
        build_classification_form_options,
        confidence_wording,
        identity_display_label,
    )
    from services.document_review import refresh_document_review_findings

    routing_context = build_routing_context_payload(tx)
    field_data = doc.field_data if isinstance(doc.field_data, dict) else {}
    identity = field_data.get('_document_identity') or {}
    confirmation = field_data.get('_classification_confirmation') or {}
    offer_link = SellerOfferDocument.query.filter_by(
        organization_id=tx.organization_id,
        transaction_id=tx.id,
        transaction_document_id=doc.id,
    ).first()
    linked_offer_id = offer_link.offer_id if offer_link else None
    if linked_offer_id and not (isinstance(confirmation, dict) and confirmation.get('offer_id')):
        mark_auto_filed_offer_confirmation(
            doc,
            actor_id=current_user.id,
            offer_id=int(linked_offer_id),
            template_slug=doc.template_slug,
            kind=(identity.get('kind') if isinstance(identity, dict) else None),
        )
        db.session.commit()
        field_data = doc.field_data if isinstance(doc.field_data, dict) else {}
        confirmation = field_data.get('_classification_confirmation') or {}
        refresh_document_review_findings(doc.id, org_id=tx.organization_id)
        db.session.commit()
        report = (
            DocumentReviewReport.query.filter_by(
                document_id=doc.id,
                organization_id=tx.organization_id,
                transaction_id=tx.id,
            )
            .order_by(DocumentReviewReport.created_at.desc())
            .first()
        ) or report
    classification_form = build_classification_form_options(
        identity=identity,
        routing_context=routing_context,
        document_template_slug=doc.template_slug,
        field_data=field_data,
        linked_offer_id=linked_offer_id,
    )
    classification_confirm_url = url_for(
        'transactions.confirm_document_classification_route',
        id=tx.id,
        doc_id=doc.id,
    )

    return render_template(
        'transactions/document_review_workspace.html',
        transaction=tx,
        document=doc,
        report=report,
        proposal=proposal,
        fields=fields,
        general_findings=general_findings,
        document_summary=document_summary,
        pdf_url=pdf_url,
        download_url=download_url,
        approve_url=approve_url,
        resolve_url=resolve_url,
        return_url=return_url,
        routing_context=routing_context,
        document_identity=identity,
        classification_confirmation=confirmation,
        classification_form=classification_form,
        identity_label=identity_display_label(identity),
        identity_confidence=confidence_wording(identity),
        classification_confirm_url=classification_confirm_url,
    )


@transactions_bp.route(
    '/<int:id>/documents/<int:doc_id>/review/resolve',
    methods=['POST'],
)
@login_required
@transactions_required
def resolve_document_review_workspace(id, doc_id):
    """Resolve the latest open review report for a document from the workspace."""
    tx, decision = get_transaction_for_user(id, capability=CAP_EDIT)
    if not tx:
        abort(403 if decision.reason != 'not_found' else 404)

    doc = TransactionDocument.query.filter_by(
        id=doc_id,
        transaction_id=tx.id,
        organization_id=current_user.organization_id,
    ).first_or_404()

    report = (
        DocumentReviewReport.query.filter_by(
            document_id=doc.id,
            organization_id=current_user.organization_id,
            transaction_id=tx.id,
        )
        .filter(DocumentReviewReport.status.in_([
            DocumentReviewReport.STATUS_OPEN,
            DocumentReviewReport.STATUS_ACKNOWLEDGED,
        ]))
        .order_by(DocumentReviewReport.created_at.desc())
        .first()
    )
    if not report:
        report = (
            DocumentReviewReport.query.filter_by(
                document_id=doc.id,
                organization_id=current_user.organization_id,
            )
            .order_by(DocumentReviewReport.created_at.desc())
            .first()
        )
    if not report:
        abort(404)

    resolve_report(report.id, current_user.id, current_user.organization_id)
    from services.intake_service import seller_intake_handoff_url
    next_url = seller_intake_handoff_url(tx, doc)
    return jsonify({'ok': True, 'next_url': next_url})
