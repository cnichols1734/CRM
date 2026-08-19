"""Document-first bootstrap inbox + Review and Apply routes (E1B-0)."""

from __future__ import annotations

import logging
import uuid

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from models import (
    ContractBootstrapSession,
    Transaction,
    TransactionType,
    db,
)
from services import contract_bootstrap
from services.document_classification_policy import (
    ClassificationPolicyError,
    normalize_selected_field_flags,
)
from services.document_intake_ui import (
    approve_cta_label,
    build_identification_summary,
    destination_option_label,
    resolve_bootstrap_next_url,
)
from services.transaction_auth import (
    CAP_EDIT,
    CAP_VIEW,
    get_transaction_for_user,
    transactions_visible_query,
)
from . import transactions_bp
from .decorators import bob_vtc_pilot_required, transactions_required

logger = logging.getLogger(__name__)

MAX_CONTRACT_BYTES = 25 * 1024 * 1024


def _session_for_org(session_id: int) -> ContractBootstrapSession:
    session = ContractBootstrapSession.query.filter_by(
        id=session_id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    return session


def _bootstrap_upload_files():
    """Collect PDF uploads from the inbox form (one or many)."""
    files = request.files.getlist('files') or request.files.getlist('file')
    if not files:
        contract = request.files.get('contract')
        if contract:
            files = [contract]
    return [f for f in files if f and f.filename]


@transactions_bp.route('/bootstrap/inbox', methods=['GET', 'POST'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_inbox():
    """Upload one or more first documents (listing, offer, contract, addenda)."""
    if request.method == 'POST':
        files = _bootstrap_upload_files()
        if not files:
            flash('Choose at least one PDF to upload.', 'error')
            return redirect(url_for('transactions.bootstrap_inbox'))
        if len(files) > 20:
            flash('Upload up to 20 PDFs at a time.', 'error')
            return redirect(url_for('transactions.bootstrap_inbox'))

        # Optional: BOB reads representation off the form when it can, and the
        # review page asks only when the document genuinely does not say.
        confirmed_side = (request.form.get('side') or '').strip().lower()
        if confirmed_side not in ('buyer', 'seller'):
            confirmed_side = None

        created_sessions = []
        batch_id = str(uuid.uuid4())
        try:
            # Persist every file first, then queue processing. Starting jobs
            # mid-loop races SQLite and makes later PDFs look unreadable.
            for file in files:
                filename = file.filename
                mime_type = file.mimetype or 'application/pdf'
                if not filename.lower().endswith('.pdf') and 'pdf' not in mime_type.lower():
                    raise ValueError(f'{filename}: upload a PDF.')

                file_bytes = file.read()
                if not file_bytes:
                    raise ValueError(f'{filename}: file was empty.')
                if len(file_bytes) > MAX_CONTRACT_BYTES:
                    raise ValueError(f'{filename}: larger than 25 MB.')
                if not file_bytes.lstrip().startswith(b'%PDF'):
                    raise ValueError(f'{filename}: not a readable PDF.')

                session = contract_bootstrap.process_inbox_upload(
                    file_bytes=file_bytes,
                    filename=filename,
                    mime_type=mime_type,
                    user=current_user,
                    org_id=current_user.organization_id,
                    run_extraction=False,
                    confirmed_side=confirmed_side,
                    upload_batch_id=batch_id,
                )
                created_sessions.append(session)

            for session in created_sessions:
                contract_bootstrap.enqueue_bootstrap_processing(
                    session_id=session.id,
                    org_id=current_user.organization_id,
                )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return redirect(url_for('transactions.bootstrap_inbox'))
        except Exception:
            logger.exception('Bootstrap inbox upload failed')
            db.session.rollback()
            flash('Upload failed. Try again or create the transaction manually.', 'error')
            return redirect(url_for('transactions.bootstrap_inbox'))

        return redirect(
            url_for('transactions.bootstrap_batch', batch_id=batch_id)
        )

    recent = (
        ContractBootstrapSession.query.filter_by(
            organization_id=current_user.organization_id,
        )
        .order_by(ContractBootstrapSession.created_at.desc())
        .limit(25)
        .all()
    )
    return render_template(
        'transactions/bootstrap_inbox.html',
        sessions=recent,
    )


@transactions_bp.route('/bootstrap/batch/<batch_id>', methods=['GET'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_batch(batch_id):
    """Wait while inbox PDFs are identified, then open review."""
    sessions = contract_bootstrap.sessions_for_upload_batch(
        org_id=current_user.organization_id,
        batch_id=batch_id,
    )
    if not sessions:
        flash('That upload batch was not found.', 'error')
        return redirect(url_for('transactions.bootstrap_inbox'))

    payload = contract_bootstrap.build_batch_status_payload(
        sessions=sessions,
        batch_id=batch_id,
    )
    if payload.get('ready') and payload.get('primary_session_id'):
        return redirect(
            url_for(
                'transactions.bootstrap_review',
                session_id=payload['primary_session_id'],
            )
        )
    if payload.get('ready') and not payload.get('primary_session_id'):
        flash('Could not identify any document in that upload.', 'error')
        return redirect(url_for('transactions.bootstrap_inbox'))

    return render_template(
        'transactions/bootstrap_batch.html',
        batch_id=batch_id,
        items=payload.get('items') or [],
        identified_count=payload.get('identified_count') or 0,
        total_count=payload.get('total') or 0,
        status_url=url_for('transactions.bootstrap_batch_status', batch_id=batch_id),
        poll_ms=1500,
    )


@transactions_bp.route('/bootstrap/batch/<batch_id>/status', methods=['GET'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_batch_status(batch_id):
    """Live progress for an inbox upload batch."""
    sessions = contract_bootstrap.sessions_for_upload_batch(
        org_id=current_user.organization_id,
        batch_id=batch_id,
    )
    if not sessions:
        return jsonify({'ok': False, 'error': 'batch_not_found'}), 404

    payload = contract_bootstrap.build_batch_status_payload(
        sessions=sessions,
        batch_id=batch_id,
    )
    if payload.get('ready') and payload.get('primary_session_id'):
        payload['redirect_url'] = url_for(
            'transactions.bootstrap_review',
            session_id=payload['primary_session_id'],
        )
    return jsonify(payload)


@transactions_bp.route('/bootstrap/<int:session_id>/review', methods=['GET'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_review(session_id):
    """Review and Apply page for a bootstrap session."""
    session = _session_for_org(session_id)
    if session.status in (
        ContractBootstrapSession.STATUS_UPLOADED,
        ContractBootstrapSession.STATUS_PROCESSING,
    ):
        batch_id = (session.classification or {}).get('upload_batch_id')
        if batch_id:
            return redirect(
                url_for('transactions.bootstrap_batch', batch_id=batch_id)
            )
    payload = contract_bootstrap.build_review_payload(session=session)
    classification = payload.get('classification') or {}
    side = classification.get('side')
    if side not in ('buyer', 'seller'):
        side = None
    identification = build_identification_summary(payload, side=side)
    destination_choices = []
    for option in identification.get('destination_options') or []:
        destination_choices.append({
            'value': option,
            'label': destination_option_label(option),
        })
    approve_label = approve_cta_label(
        route_action=identification.get('route_action'),
        side=side,
        match_status=session.match_status,
        destination_choice=identification.get('destination_choice'),
    )
    return render_template(
        'transactions/bootstrap_review.html',
        session=session,
        review=payload,
        identification=identification,
        destination_choices=destination_choices,
        approve_cta_label=approve_label,
        is_listing_intake=bool(identification.get('is_listing_intake')),
    )


@transactions_bp.route('/bootstrap/<int:session_id>/status', methods=['GET'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_status(session_id):
    """Small polling payload for the asynchronous contract review."""
    session = _session_for_org(session_id)
    classification = session.classification or {}
    return jsonify({
        'ok': session.status not in (
            ContractBootstrapSession.STATUS_FAILED,
            ContractBootstrapSession.STATUS_CANCELLED,
        ),
        'status': session.status,
        'match_status': session.match_status,
        'address': classification.get('property_address'),
        'error': classification.get('processing_error'),
        'ready': session.status in (
            ContractBootstrapSession.STATUS_AWAITING_MATCH,
            ContractBootstrapSession.STATUS_AWAITING_REVIEW,
            ContractBootstrapSession.STATUS_APPLIED,
            ContractBootstrapSession.STATUS_FAILED,
            ContractBootstrapSession.STATUS_CANCELLED,
        ),
    })


@transactions_bp.route('/bootstrap/transactions/search', methods=['GET'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_transaction_search():
    """Search authorized transactions by address for manual match recovery."""
    query = (request.args.get('q') or '').strip()
    side = (request.args.get('side') or '').strip().lower()
    if len(query) < 2:
        return jsonify([])

    like = f'%{query}%'
    visible = transactions_visible_query(current_user)
    if side in ('buyer', 'seller'):
        visible = visible.join(
            TransactionType,
            Transaction.transaction_type_id == TransactionType.id,
        ).filter(TransactionType.name == side)

    transactions = (
        visible.filter(or_(
            Transaction.street_address.ilike(like),
            Transaction.city.ilike(like),
        ))
        .order_by(Transaction.created_at.desc())
        .limit(10)
        .all()
    )
    return jsonify([{
        'id': tx.id,
        'address': tx.street_address,
        'city': tx.city,
        'state': tx.state,
        'status': tx.status,
        'side': tx.transaction_type.name if tx.transaction_type else None,
    } for tx in transactions])


@transactions_bp.route('/bootstrap/<int:session_id>/match', methods=['POST'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_match(session_id):
    """Resolve match: attach | select | create_new | manual."""
    session = _session_for_org(session_id)
    data = request.get_json(silent=True) or {}
    decision = (data.get('decision') or '').strip()
    transaction_id = data.get('transaction_id')
    side = data.get('side')

    if transaction_id is not None:
        try:
            transaction_id = int(transaction_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Invalid transaction_id'}), 400

    if decision in ('attach', 'select') and transaction_id:
        tx, auth = get_transaction_for_user(transaction_id, capability=CAP_EDIT)
        if not tx:
            abort(403 if auth.reason != 'not_found' else 404)

    try:
        contract_bootstrap.resolve_match(
            session=session,
            decision=decision,
            transaction_id=transaction_id,
            side=side,
        )
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception:
        logger.exception('Bootstrap match failed for session %s', session_id)
        db.session.rollback()
        return jsonify({
            'ok': False,
            'error': 'Could not resolve this match. Try again or contact support.',
        }), 500

    return jsonify({
        'ok': True,
        'session_id': session.id,
        'match_status': session.match_status,
        'status': session.status,
        'matched_transaction_id': session.matched_transaction_id,
        'review': contract_bootstrap.build_review_payload(session=session),
    })


@transactions_bp.route('/bootstrap/<int:session_id>/approve', methods=['POST'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_approve(session_id):
    """Approve selected fields and apply (create or update transaction)."""
    session = _session_for_org(session_id)
    data = request.get_json(silent=True) or {}
    selected = data.get('selected') or {}
    corrections = data.get('corrections') or {}

    if not isinstance(corrections, dict):
        return jsonify({'ok': False, 'error': 'corrections must be an object'}), 400

    try:
        selected_bool = normalize_selected_field_flags(selected)
    except ClassificationPolicyError as e:
        return jsonify({'ok': False, 'error': str(e), 'code': e.code}), 400

    if session.match_status == ContractBootstrapSession.MATCH_MATCHED:
        tx, auth = get_transaction_for_user(
            session.matched_transaction_id, capability=CAP_EDIT,
        )
        if not tx:
            abort(403 if auth.reason != 'not_found' else 404)

    party_resolutions = data.get('parties')
    if party_resolutions is not None and not isinstance(party_resolutions, list):
        return jsonify({'ok': False, 'error': 'parties must be a list'}), 400

    try:
        transaction, proposal = contract_bootstrap.approve_selected(
            session=session,
            user_id=current_user.id,
            selected_fields=selected_bool,
            corrections=corrections,
            confirmed_side=data.get('side'),
            party_resolutions=party_resolutions,
            destination_choice=data.get('destination_choice'),
        )
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception:
        logger.exception('Bootstrap approve failed for session %s', session_id)
        db.session.rollback()
        return jsonify({
            'ok': False,
            'error': 'Could not apply this document. Try again or contact support.',
        }), 500

    classification = session.classification or {}
    route = classification.get('route_decision') or {}
    route_action = route.get('action')
    offer_id = classification.get('offer_id')
    amendment_id = classification.get('amendment_id')
    side = classification.get('side')
    next_url = resolve_bootstrap_next_url(
        transaction_id=transaction.id,
        route_action=route_action,
        offer_id=int(offer_id) if offer_id else None,
        amendment_id=int(amendment_id) if amendment_id else None,
        side=side,
        bob_setup=True,
        bootstrap_session_id=session.id,
    )
    intake_url = None
    if route_action in (
        'create_or_match_listing',
        'attach_listing_document',
    ):
        intake_url = url_for(
            'transactions.intake_questionnaire',
            id=transaction.id,
            bootstrap_session_id=session.id,
        )
        # A new listing without questionnaire answers lands directly on the
        # questionnaire so required-document placeholders are generated in the
        # same sitting, not left as an optional link.
        if not (transaction.intake_data or {}):
            next_url = intake_url

    return jsonify({
        'ok': True,
        'session_id': session.id,
        'transaction_id': transaction.id,
        'proposal_id': proposal.id,
        'route_action': route_action,
        'offer_id': offer_id,
        'next_url': next_url,
        'redirect_url': next_url,
        'intake_url': intake_url,
        'approve_cta_label': approve_cta_label(
            route_action=route_action,
            side=side,
            match_status=session.match_status,
            destination_choice=data.get('destination_choice'),
        ),
    })


@transactions_bp.route('/bootstrap/<int:session_id>/complete', methods=['GET'])
@login_required
@transactions_required
@bob_vtc_pilot_required
def bootstrap_complete(session_id):
    """Old receipt URL. Send applied sessions to the transaction workspace."""
    session = _session_for_org(session_id)
    if session.status != ContractBootstrapSession.STATUS_APPLIED:
        return redirect(url_for('transactions.bootstrap_review', session_id=session.id))

    transaction, auth = get_transaction_for_user(
        session.matched_transaction_id,
        capability=CAP_VIEW,
    )
    if not transaction:
        abort(403 if auth.reason != 'not_found' else 404)

    classification = session.classification or {}
    route = classification.get('route_decision') or {}
    return redirect(resolve_bootstrap_next_url(
        transaction_id=transaction.id,
        route_action=route.get('action'),
        offer_id=classification.get('offer_id'),
        amendment_id=classification.get('amendment_id'),
        side=classification.get('side'),
        bob_setup=True,
        bootstrap_session_id=session.id,
    ))
