"""Seller accepted contract routes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from models import (
    SellerAcceptedContract,
    SellerContractDocument,
    SellerContractMilestone,
    Transaction,
    TransactionDocument,
    db,
)
from services.intake_service import post_upload_processing
from services.seller_workflow import (
    close_contract,
    create_contract_milestones,
    derive_financing_approval_deadline,
    get_offer_document_type,
    infer_offer_document_type_from_pdf,
    promote_backup_contract,
    terminate_contract,
)
from . import transactions_bp
from .decorators import transactions_required


def _can_manage_transaction(transaction):
    return (
        transaction.created_by_id == current_user.id
        or getattr(current_user, 'role', None) == 'admin'
        or getattr(current_user, 'org_role', None) in ('admin', 'owner')
    )


def _get_seller_transaction(id):
    transaction = Transaction.query.filter_by(
        id=id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    if not _can_manage_transaction(transaction):
        abort(403)
    if transaction.transaction_type and transaction.transaction_type.name != 'seller':
        return None
    return transaction


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_date(value):
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).replace('$', '').replace(',', '').strip())
    except (InvalidOperation, AttributeError):
        return None


MILESTONE_STATUSES = {
    'not_started',
    'waiting',
    'due_soon',
    'overdue',
    'completed',
    'not_applicable',
}


def _milestone_payload(milestone):
    return {
        'id': milestone.id,
        'title': milestone.title,
        'milestone_key': milestone.milestone_key,
        'due_at': milestone.due_at.isoformat() if milestone.due_at else None,
        'status': milestone.status,
        'responsible_party': milestone.responsible_party,
        'source': milestone.source,
        'notes': milestone.notes,
        'completed_at': milestone.completed_at.isoformat() if milestone.completed_at else None,
    }


def _get_contract_for_update(transaction, contract_id):
    return SellerAcceptedContract.query.filter_by(
        id=contract_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()


def _contract_document_payload(contract_document):
    doc = contract_document.document
    return {
        'contract_document_id': contract_document.id,
        'document_id': contract_document.transaction_document_id,
        'document_type': contract_document.document_type,
        'display_name': contract_document.display_name,
        'template_slug': doc.template_slug if doc else None,
        'extraction_status': doc.extraction_status if doc else None,
        'filename': doc.signed_original_filename if doc else None,
        'is_primary_contract_document': contract_document.is_primary_contract_document,
    }


def _json_safe_value(value):
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _sync_contract_frozen_terms(contract):
    from sqlalchemy.orm.attributes import flag_modified

    terms = dict(contract.frozen_terms or {})
    for field in (
        'accepted_price',
        'effective_date',
        'closing_date',
        'option_period_days',
        'financing_approval_deadline',
        'financing_type',
        'cash_down_payment',
        'financing_amount',
        'seller_concessions_amount',
        'survey_choice',
        'survey_furnished_by',
        'residential_service_contract',
        'buyer_agent_commission_percent',
        'buyer_agent_commission_flat',
    ):
        terms[field] = _json_safe_value(getattr(contract, field))
    contract.frozen_terms = terms
    flag_modified(contract, 'frozen_terms')


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/documents/upload', methods=['POST'])
@login_required
@transactions_required
def upload_seller_contract_document(id, contract_id):
    """Upload executed contract PDFs to an accepted seller contract workspace."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = _get_contract_for_update(transaction, contract_id)
    if contract.status != 'active':
        return jsonify({'success': False, 'error': 'Documents can only be uploaded to an active contract'}), 400

    files = request.files.getlist('files') or request.files.getlist('file')
    files = [file for file in files if file and file.filename]
    if not files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400

    document_types = (
        request.form.getlist('document_type')
        or request.form.getlist('document_types[]')
        or request.form.getlist('direction')
    )

    try:
        from services.supabase_storage import upload_external_document as upload_storage

        uploaded = []
        max_size = 25 * 1024 * 1024
        for index, file in enumerate(files):
            file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
            if file_ext != 'pdf':
                return jsonify({'success': False, 'error': f'{file.filename}: only PDF files are allowed'}), 400

            file_data = file.read()
            if len(file_data) > max_size:
                return jsonify({'success': False, 'error': f'{file.filename}: file too large. Maximum size is 25MB.'}), 400

            explicit_type = document_types[index] if index < len(document_types) else None
            document_type = infer_offer_document_type_from_pdf(
                file_data,
                file.filename,
                explicit_type or request.form.get('document_type') or 'final_acceptance',
            )
            if document_type in ('buyer_offer', 'offer_package'):
                document_type = 'final_acceptance'
            doc_config = get_offer_document_type(document_type)
            template_slug = doc_config['template_slug']
            display_name = doc_config['label']

            result = upload_storage(
                transaction_id=transaction.id,
                file_data=file_data,
                original_filename=file.filename,
                content_type='application/pdf',
            )

            doc = TransactionDocument(
                organization_id=current_user.organization_id,
                transaction_id=transaction.id,
                template_slug=template_slug,
                template_name=display_name,
                status='signed',
                document_source='completed',
                signed_file_path=result['path'],
                signed_file_size=len(file_data),
                signed_original_filename=file.filename,
                signed_at=datetime.utcnow(),
                extraction_status='pending',
                field_data={},
            )
            db.session.add(doc)
            db.session.flush()

            contract_document = SellerContractDocument(
                organization_id=current_user.organization_id,
                transaction_id=transaction.id,
                accepted_contract_id=contract.id,
                transaction_document_id=doc.id,
                created_by_id=current_user.id,
                document_type=document_type,
                display_name=display_name,
                is_primary_contract_document=doc_config['primary_terms'],
                extraction_summary={},
            )
            db.session.add(contract_document)
            db.session.flush()
            uploaded.append(contract_document)

        db.session.commit()

        for contract_document in uploaded:
            post_upload_processing(contract_document.document)

        documents_payload = [_contract_document_payload(contract_document) for contract_document in uploaded]
        first = documents_payload[0] if documents_payload else {}
        return jsonify({
            'success': True,
            'message': f'{len(documents_payload)} contract document{"s" if len(documents_payload) != 1 else ""} uploaded. Extraction has started.',
            'accepted_contract_id': contract.id,
            'document_id': first.get('document_id'),
            'documents': documents_payload,
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/documents', methods=['GET'])
@login_required
@transactions_required
def list_seller_contract_documents(id, contract_id):
    """Return documents attached to an accepted seller contract workspace."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = _get_contract_for_update(transaction, contract_id)
    documents = SellerContractDocument.query.filter_by(
        accepted_contract_id=contract.id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).order_by(SellerContractDocument.created_at.asc()).all()

    return jsonify({
        'success': True,
        'accepted_contract_id': contract.id,
        'documents': [_contract_document_payload(document) for document in documents],
    })


def _apply_milestone_data(milestone, data):
    title = (data.get('title') or '').strip()
    if not title:
        raise ValueError('Milestone title is required')

    status = data.get('status') or milestone.status or 'not_started'
    if status not in MILESTONE_STATUSES:
        raise ValueError('Invalid milestone status')

    milestone.title = title
    milestone.due_at = _parse_datetime(data.get('due_at'))
    milestone.status = status
    milestone.responsible_party = data.get('responsible_party') or None
    milestone.notes = data.get('notes') or None
    milestone.source = 'manual'
    if status == 'completed':
        milestone.completed_at = milestone.completed_at or datetime.utcnow()
    else:
        milestone.completed_at = None
    return milestone


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/details', methods=['POST', 'PATCH'])
@login_required
@transactions_required
def update_seller_contract_details(id, contract_id):
    """Update accepted contract terms that drive seller milestones."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = _get_contract_for_update(transaction, contract_id)
    data = request.get_json(silent=True) or request.form

    try:
        if 'accepted_price' in data:
            contract.accepted_price = _decimal(data.get('accepted_price'))
        if 'effective_date' in data:
            contract.effective_date = _parse_date(data.get('effective_date'))
            contract.effective_at = _parse_datetime(data.get('effective_at')) if data.get('effective_at') else None
        if 'closing_date' in data:
            contract.closing_date = _parse_date(data.get('closing_date'))
        if 'option_period_days' in data:
            contract.option_period_days = int(data.get('option_period_days')) if data.get('option_period_days') else None

        for field in (
            'financing_type',
            'survey_choice',
            'survey_furnished_by',
            'residential_service_contract',
        ):
            if field in data:
                setattr(contract, field, data.get(field) or None)

        for field in (
            'cash_down_payment',
            'financing_amount',
            'seller_concessions_amount',
            'buyer_agent_commission_percent',
            'buyer_agent_commission_flat',
        ):
            if field in data:
                setattr(contract, field, _decimal(data.get(field)))

        if 'financing_approval_deadline' in data and data.get('financing_approval_deadline'):
            contract.financing_approval_deadline = _parse_date(data.get('financing_approval_deadline'))
        else:
            contract.financing_approval_deadline = derive_financing_approval_deadline(
                contract.frozen_terms or {},
                contract.effective_date,
            )

        _sync_contract_frozen_terms(contract)
        create_contract_milestones(contract, replace=True)
        db.session.commit()
        return jsonify({'success': True, 'accepted_contract_id': contract.id})
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/milestones', methods=['POST'])
@login_required
@transactions_required
def create_seller_contract_milestone(id, contract_id):
    """Create a manual milestone for a seller contract."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = _get_contract_for_update(transaction, contract_id)
    data = request.get_json(silent=True) or request.form
    milestone = SellerContractMilestone(
        organization_id=current_user.organization_id,
        transaction_id=transaction.id,
        accepted_contract_id=contract.id,
        created_by_id=current_user.id,
        milestone_key='manual',
        title='Manual milestone',
        source='manual',
    )

    try:
        _apply_milestone_data(milestone, data)
        db.session.add(milestone)
        db.session.commit()
        return jsonify({'success': True, 'milestone': _milestone_payload(milestone)}), 201
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/milestones/<int:milestone_id>', methods=['POST', 'PATCH'])
@login_required
@transactions_required
def update_seller_contract_milestone(id, contract_id, milestone_id):
    """Manually update a seller contract milestone."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = _get_contract_for_update(transaction, contract_id)
    milestone = SellerContractMilestone.query.filter_by(
        id=milestone_id,
        accepted_contract_id=contract.id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    data = request.get_json(silent=True) or request.form

    try:
        _apply_milestone_data(milestone, data)
        db.session.commit()
        return jsonify({'success': True, 'milestone': _milestone_payload(milestone)})
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/terminate', methods=['POST'])
@login_required
@transactions_required
def terminate_seller_contract(id, contract_id):
    """Terminate a primary contract and promote a backup if requested."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = SellerAcceptedContract.query.filter_by(
        id=contract_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    data = request.get_json(silent=True) or request.form
    reason = data.get('termination_reason') or 'other'

    try:
        termination = terminate_contract(
            contract,
            reason=reason,
            actor_id=current_user.id,
            terminated_at=_parse_datetime(data.get('terminated_at')) or datetime.utcnow(),
            document_id=data.get('termination_document_id') or None,
            notes=data.get('notes'),
        )

        promote_backup_id = data.get('promote_backup_contract_id')
        if promote_backup_id:
            backup_contract = SellerAcceptedContract.query.filter_by(
                id=int(promote_backup_id),
                transaction_id=transaction.id,
                organization_id=current_user.organization_id,
                position='backup',
                status='active',
            ).first_or_404()
            promoted = promote_backup_contract(
                contract,
                backup_contract,
                _parse_datetime(data.get('backup_notice_received_at')) or datetime.utcnow(),
                actor_id=current_user.id,
            )
            termination.promoted_backup_contract_id = promoted.id
            termination.backup_promoted = True
            transaction.status = 'under_contract'
        else:
            termination.returned_to_active = True
            transaction.status = 'active'

        db.session.commit()
        return jsonify({'success': True, 'backup_promoted': termination.backup_promoted})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@transactions_bp.route('/<int:id>/seller/contracts/<int:contract_id>/close', methods=['POST'])
@login_required
@transactions_required
def close_seller_contract(id, contract_id):
    """Close a seller transaction from an active primary contract."""
    transaction = _get_seller_transaction(id)
    if transaction is None:
        return jsonify({'success': False, 'error': 'Contracts are only available for seller transactions'}), 400

    contract = SellerAcceptedContract.query.filter_by(
        id=contract_id,
        transaction_id=transaction.id,
        organization_id=current_user.organization_id,
        position='primary',
    ).first_or_404()
    data = request.get_json(silent=True) or request.form

    try:
        closing = close_contract(
            contract,
            current_user.id,
            actual_closing_date=_parse_date(data.get('actual_closing_date')),
            funded_recorded_at=_parse_datetime(data.get('funded_recorded_at')),
            final_sales_price=_decimal(data.get('final_sales_price')),
            final_seller_concessions=_decimal(data.get('final_seller_concessions')),
            final_listing_commission=_decimal(data.get('final_listing_commission')),
            final_coop_compensation=_decimal(data.get('final_coop_compensation')),
            final_referral_fee=_decimal(data.get('final_referral_fee')),
            final_net_proceeds=_decimal(data.get('final_net_proceeds')),
            deed_recording_reference=data.get('deed_recording_reference'),
            final_walkthrough_complete=str(data.get('final_walkthrough_complete', '')).lower() in ('1', 'true', 'yes', 'on'),
            key_access_handoff_complete=str(data.get('key_access_handoff_complete', '')).lower() in ('1', 'true', 'yes', 'on'),
            possession_status=data.get('possession_status'),
            notes=data.get('notes'),
        )
        db.session.commit()
        return jsonify({'success': True, 'closing_summary_id': closing.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
